"""Opt-in bounded CUDA MP4 review. Local sanitized decisions; no DB1 delivery."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import socket
import sys
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--enable-mohamed-lpr-v3", action="store_true", required=True)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--ocr-cache", type=Path, required=True)
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--tunis-mode", choices=("legacy_whole", "marker_first"), required=True)
    p.add_argument("--max-frames", type=int, required=True, choices=range(1, 9))
    p.add_argument("--max-crops", type=int, required=True, choices=range(1, 9))
    p.add_argument("--max-seconds", type=float, required=True)
    args = p.parse_args(argv)
    if not 0 < args.max_seconds <= 300:
        p.error("max-seconds must be positive and at most 300")
    return args


def main(argv=None):
    args = arguments(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True", YOLO_AUTOINSTALL="false",
                      YOLO_OFFLINE="true", OPENROUTER_ENABLED="false", COLOR_AGENT_ENABLED="false",
                      YOLO_CONFIG_DIR=str(args.output / "runtime-settings"), FLAGS_allocator_strategy="auto_growth",
                      OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    # No internet/model retrieval, provider, Redis or database connection.
    def offline(*_args, **_kwargs):
        raise RuntimeError("network_disabled_mohamed_review")
    socket.socket.connect = socket.socket.connect_ex = offline

    from vehicle.common.mohamed_lpr_native import Assets, CudaModels, CudaPlateDetector, cuda_preflight, file_hash, hashed
    from vehicle.common.mohamed_lpr_engine import BindingError, RetirementPending, V3EngineAdapter, bind_crop
    from vehicle.common.mohamed_lpr_contract import encode, decode
    from vehicle.common.mohamed_lpr_harness import Harness
    from vehicle.common.engine_plate import native_access
    from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
    from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
    from vehicle.common.observation_evidence import RecognitionIdentity
    from vehicle.common.real_video.input import MP4Input
    from vehicle.common.real_video.local import LocalDetector, pixel_operations
    from vehicle.common.real_video.plate_engine import CombinedEngine, VideoPlateLimits
    from vehicle.common.vehicle_observation import RunEnd
    report = dict(status="starting", command=sys.argv, tunis_mode=args.tunis_mode,
                  limits=dict(frames=args.max_frames, crops=args.max_crops, seconds=args.max_seconds,
                              native_api_operations=2 * args.max_frames + 6 * args.max_crops,
                              crop_bytes=8 * 1024 * 1024, retained_crop_bytes=32 * 1024 * 1024),
                  frames=0, decisions=[], accepted=0, unresolved=0, execution_errors=0,
                  provider_calls=0, db1_records=0, redis_publications=0, decoder_opens=0)
    source = engine = vehicle = detector = models = assets = None
    adapters = []
    admitted = False
    deadline = time.monotonic() + args.max_seconds
    started = time.perf_counter()
    exit_code = 1
    try:
        assets = Assets(args.source_root, args.ocr_cache)
        report["artifact_lock_sha256"] = file_hash(ROOT / "vehicle/common/mohamed_lpr_assets.json")
        report["artifact_files"] = {k: {n: v[n] for n in ("sha256", "bytes")} for k, v in assets.lock["files"].items()}
        report["source_head"] = assets.lock["source_head"]
        expected_clip = (assets.source / "services/license_plate_service/run_video_pipeline/test_video.mp4").resolve()
        if args.video.resolve() != expected_clip or file_hash(args.video) != assets.lock["clip_sha256"]:
            raise BindingError("wrong_audited_clip")
        report["clip_sha256"] = assets.lock["clip_sha256"]
        assets.runtime = cuda_preflight()
        report["runtime"] = assets.runtime
        report["reference_index"] = asdict(assets.index_identity)
        report["references"] = [{k: r[k] for k in ("reference_id", "category", "sha256")} for r in assets.lock["references"]]
        report["asset_preflight_seconds"] = time.perf_counter() - started
        import cv2
        import torch
        cv2.setNumThreads(1)
        torch.set_num_threads(1)
        models = CudaModels(assets)
        detector = CudaPlateDetector(assets, frame_rate=None)
        report["initialization_seconds"] = {}
        # All required native bindings must initialize before the sole decoder
        # opens. Initialization is not a recognition/category-coverage claim.
        with native_access():
            for role in ("category_ocr", "visual", "registration", "marker"):
                tick = time.perf_counter()
                try:
                    models.initialize(role)
                except BindingError:
                    raise
                except Exception as error:
                    raise BindingError("model_initialization_failed_" + role + "_" + type(error).__name__) from None
                report["initialization_seconds"][role] = time.perf_counter() - tick
            tick = time.perf_counter()
            detector.load_backend()
            report["initialization_seconds"]["plate"] = time.perf_counter() - tick
        admitted = True
        now = datetime.now(timezone.utc)
        org = UUID("12345678-1234-4234-8234-123456789012")
        context = RunContext(org, CameraRegistration(org, "local-mohamed-mp4", None, "Africa/Tunis", now, None),
            args.output.name, "file", now, "mohamed-lpr-cuda-review-v3",
            ResolutionInfo(None, None, "vehicle", ModelRegistration(None, "local-vehicle-yolo26", 1, "detect"),
                           None, "audited local MP4; review-only v3 decisions", False), "development")
        clock = FrameReceiptClock(context, utc_clock=lambda: datetime.now(timezone.utc),
                                  monotonic_clock=time.monotonic, policy=ClockPolicy())
        source = MP4Input(args.video, clock)
        source.__enter__()  # Sole capture, opened once; no preflight decode.
        report["decoder_opens"] = source.opens
        fps = source.metadata["fps"]
        if fps <= 0:
            raise BindingError("missing_clip_fps")
        with native_access():
            vehicle = LocalDetector(assets.path("vehicle"), assets.path("vehicle-tracker"),
                                    device="cuda:0", frame_rate=fps, threads=1)
        detector.frame_rate = fps
        estimate, quality = pixel_operations()
        limits = VideoPlateLimits(max_frames=args.max_frames, max_seconds=args.max_seconds,
                                  max_attempts_total=args.max_crops, max_api_calls=80)
        engine = CombinedEngine(context, clock, vehicle, detector, {},
            algorithm=RecognitionIdentity("t1-hsv-review", 1, "classify"), quality_algorithm="t1-colour-quality",
            plate_quality_algorithm="t1-original-plate-crop-quality", estimate=estimate, quality_fn=quality, limits=limits)
        frames = {}
        reason = "frame_budget"
        while source.decoded < args.max_frames:
            if time.monotonic() >= deadline:
                reason = "time_budget"
                break
            packet = source.read()
            if packet is None:
                reason = "eof"
                break
            frames[packet.receipt.frame_index] = (packet.receipt, file_hash_pixels(packet.pixels))
            engine.process(packet)
            if vehicle.placement["parameter_devices"] != ["cuda:0"]:
                raise BindingError("vehicle_cpu_fallback")
        report.update(frames=source.decoded, stop_reason=reason, source_metadata=source.metadata)
        source.close()  # Decoder/frame arrays are not retained by recognition.
        engine.finish(RunEnd.END_OF_INPUT if reason == "eof" else RunEnd.SERVICE_STOP, trigger=reason)
        report["associations"] = dict(Counter(e["association"]["status"] for e in engine.plate.associations))
        report["candidate_statuses"] = dict(Counter(e["status"] for e in engine.plate.associations))
        report["crop_replacements"] = engine.plate.crop_replacements
        selected = [(v, resource) for v in engine.plate.lives.values() for _, resource in v.best.values()]
        if len(selected) > args.max_crops:
            raise BindingError("crop_budget_exceeded_no_truncation")
        for life, resource in selected:
            if life.blocker is not None:
                report["decisions"].append(dict(category=None, status="unresolved", reason=life.blocker,
                                               reason_kind="t1_evidence_blocker"))
                continue
            c = resource.binding.candidate
            receipt, frame_sha = frames[c.receipt.frame_index]
            bound = bind_crop(resource, life.life, plate_epoch=engine.plate.plate_epoch,
                              frame_receipt=receipt, frame_sha256=frame_sha)
            harness = Harness(bound.scalar, tunis_mode=args.tunis_mode)
            adapter = V3EngineAdapter(models, bound, harness)
            adapters.append(adapter)
            seal = adapter.drive(deadline=deadline)
            # Real wire codec round trip, without transport, raw evidence export
            # or a second recognition. Replay the last execution from cache.
            for r in harness.engine.current_results:
                if decode(encode(r)) != r:
                    raise BindingError("v3_wire_roundtrip_mismatch")
            before = dict(models.calls)
            cached = adapter.execute(harness.engine.current_requests[-1])
            harness.inject(cached)
            if before != models.calls or harness.seal() != seal:
                raise BindingError("retry_reexecuted_or_changed_seal")
            d = seal.decision
            report["decisions"].append(dict(category=d.category, status=d.status,
                reason=d.reason, reason_kind=d.reason_kind, warnings=list(d.warnings)))
            adapter.release()
        report["accepted"] = sum(d["status"] == "accepted" for d in report["decisions"])
        report["unresolved"] = sum(d["status"] == "unresolved" for d in report["decisions"])
        report["categories_exercised"] = dict(Counter(d["category"] or "unknown" for d in report["decisions"]))
        report["status"] = "passed" if report["frames"] > 0 and adapters else "failed"
        if report["status"] == "failed":
            report["failure_code"] = "no_associated_recognition_exercised"
        exit_code = 0 if report["status"] == "passed" else 1
    except Exception as error:
        report["status"] = "blocked" if not admitted or isinstance(error, (BindingError, RetirementPending)) else "failed"
        # Never export native exception text, which can embed OCR/crop paths.
        report["failure_type"] = type(error).__name__
        report["failure_code"] = str(error) if isinstance(error, (BindingError, RetirementPending)) else "bounded_run_exception"
        exit_code = 2 if report["status"] == "blocked" else 1
    finally:
        cleanup = []
        if source is not None:
            source.close()
            report["decoder_opens"] = source.opens
        unretired = any(a.active is not None for a in adapters)
        report["unretired_operations"] = sum(a.active is not None for a in adapters)
        if not unretired:
            for adapter in adapters:
                adapter.release(discard=True)
            if engine is not None:
                for crop in engine.plate.buffers:
                    crop.release()  # Includes diagnostics/aborted admitted work after retirement.
            for obj in (models, engine if engine is not None else vehicle, detector if engine is None else None):
                if obj is not None:
                    try:
                        if obj is engine:
                            obj.close()  # CombinedEngine owns its own native gates.
                        else:
                            with native_access():
                                obj.close()
                    except Exception as error:
                        cleanup.append(type(error).__name__)
        else:
            cleanup.append("native_retirement_unconfirmed_resources_retained")
        if models is not None:
            report.update(model_calls=models.calls, model_placements=models.placements,
                          actual_provenance={role: asdict(models.expected(role)) for role in models.calls})
        stages = defaultdict(lambda: dict(calls=0, seconds=0.0, failed=0))
        for a in adapters:
            for e in a.events:
                stages[e["role"]]["calls"] += 1
                stages[e["role"]]["seconds"] += e["seconds"]
                stages[e["role"]]["failed"] += e["outcome"] == "failed"
        if engine is not None:
            starts = {}
            for e in engine.audit.events:
                if e["phase"] == "start":
                    starts[e["kind"]] = e["monotonic"]
                else:
                    stages[e["kind"]]["calls"] += 1
                    stages[e["kind"]]["seconds"] += e["monotonic"] - starts.pop(e["kind"])
            report["retained_crops_after_cleanup"] = len(engine.plate.buffers)
        report["execution_errors"] = sum(s["failed"] for s in stages.values())
        if report["execution_errors"] and report["status"] == "passed":
            report["status"], exit_code = "failed", 1
        report["per_stage"] = dict(stages)
        report["detector_placement"] = detector.placement if detector is not None else None
        report["vehicle_placement"] = vehicle.placement if vehicle is not None else None
        report["cleanup_errors"] = cleanup
        report["elapsed_seconds"] = time.perf_counter() - started
        if cleanup and report["status"] == "passed":
            report["status"], exit_code = "failed", 1
        (args.output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({k: report[k] for k in ("status", "frames", "accepted", "unresolved", "execution_errors", "cleanup_errors")}))
    return exit_code


def file_hash_pixels(pixels):
    from hashlib import sha256
    return sha256(pixels.tobytes(order="C")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
