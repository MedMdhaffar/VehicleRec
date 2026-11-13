"""Explicit v3 owned-Redis CUDA review. No DB1, RTSP or exported readings."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None, *, review_hook=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--enable-mohamed-lpr-v3", required=True, action="store_true")
    for name in ("source-root", "ocr-cache", "video", "output", "redis-server"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--tunis-mode", required=True, choices=("legacy_whole", "marker_first"))
    p.add_argument("--max-frames", required=True, type=int, choices=range(1, 9))
    p.add_argument("--max-crops", required=True, type=int, choices=range(1, 9))
    p.add_argument("--max-seconds", required=True, type=float)
    a = p.parse_args(argv)
    if not 0 < a.max_seconds <= 300:
        p.error("max-seconds must be positive and at most 300")
    a.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True", YOLO_AUTOINSTALL="false", YOLO_OFFLINE="true",
        OPENROUTER_ENABLED="false", COLOR_AGENT_ENABLED="false", FLAGS_allocator_strategy="auto_growth",
        YOLO_CONFIG_DIR=str(a.output / "runtime-settings"), OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def private_connect(self, address):
        if self.family != socket.AF_UNIX:
            raise RuntimeError("network_disabled_v3_review")
        return connect(self, address)
    def private_connect_ex(self, address):
        if self.family != socket.AF_UNIX:
            raise RuntimeError("network_disabled_v3_review")
        return connect_ex(self, address)
    socket.socket.connect, socket.socket.connect_ex = private_connect, private_connect_ex

    from vehicle.common.mohamed_lpr_native import Assets, CudaModels, CudaPlateDetector, cuda_preflight, file_hash
    from vehicle.common.mohamed_lpr_engine import BindingError, bind_crop
    from vehicle.common.mohamed_lpr_stream_codec import Scope, canonical, hashed, STREAMS
    from vehicle.common.mohamed_lpr_stream_policy import memory_replay
    from vehicle.common.mohamed_lpr_streams import LiveEngine, PlateWorker, EnginePeer, pending
    from vehicle.common.mohamed_lpr_redis_runtime import OwnedRedis, client_preflight
    from vehicle.common.engine_plate import native_access
    from vehicle.common.db1_context import CameraRegistration, ModelRegistration, ResolutionInfo, RunContext
    from vehicle.common.frame_receipt import FrameReceiptClock, ClockPolicy
    from vehicle.common.observation_evidence import RecognitionIdentity
    from vehicle.common.real_video.local import LocalDetector, pixel_operations
    from vehicle.common.real_video.input import MP4Input
    from vehicle.common.real_video.plate_engine import CombinedEngine, VideoPlateLimits
    from vehicle.common.vehicle_observation import RunEnd

    report = dict(status="starting", command=sys.argv, tunis_mode=a.tunis_mode,
        limits=dict(frames=a.max_frames, crops=a.max_crops, admission_seconds=a.max_seconds,
                    crop_bytes=8 * 1024 * 1024, retained_crop_bytes=32 * 1024 * 1024),
        frames=0, decoder_opens=0, accepted=0, unresolved=0, execution_errors=0, parity=[],
        provider_calls=0, db1_records=0, db1_publications=0)
    started, deadline = time.perf_counter(), time.monotonic() + a.max_seconds
    assets = source = engine = vehicle = detector = models = owned = live = plate = peer = None
    exit_code, admission = 1, False
    try:
        try:
            redis_client = client_preflight()
        except RuntimeError as error:
            raise BindingError(str(error)) from None
        owned = OwnedRedis(a.redis_server).__enter__()
        report["redis_client"] = dict(version=redis_client.__version__, module_file=redis_client.__file__,
            module_sha256=file_hash(Path(redis_client.__file__)))
        assets = Assets(a.source_root, a.ocr_cache)
        expected = (assets.source / "services/license_plate_service/run_video_pipeline/test_video.mp4").resolve()
        if a.video.resolve() != expected or file_hash(a.video) != assets.lock["clip_sha256"]:
            raise BindingError("wrong_audited_clip")
        assets.runtime = cuda_preflight()
        report.update(runtime=assets.runtime, source_head=assets.lock["source_head"], clip_sha256=assets.lock["clip_sha256"],
            artifact_lock_sha256=file_hash(ROOT / "vehicle/common/mohamed_lpr_assets.json"),
            redis_binary_sha256=file_hash(a.redis_server), reference_index=asdict(assets.index_identity))
        import torch
        import cv2
        torch.set_num_threads(1); cv2.setNumThreads(1)
        models, detector = CudaModels(assets), CudaPlateDetector(assets, frame_rate=None)
        with native_access():
            for role in ("category_ocr", "visual", "registration", "marker"):
                try:
                    models.initialize(role)
                except BindingError:
                    raise
                except Exception as error:
                    raise BindingError("model_initialization_failed_" + role + "_" + type(error).__name__) from None
            detector.load_backend()
        now = datetime.now(timezone.utc)
        org = UUID("12345678-1234-4234-8234-123456789012")
        context = RunContext(org, CameraRegistration(org, "local-v3-redis-mp4", None, "Africa/Tunis", now, None),
            a.output.name, "file", now, "mohamed-lpr-streams-v3-review",
            ResolutionInfo(None, None, "vehicle", ModelRegistration(None, "local-vehicle-yolo26", 1, "detect"),
                           None, "audited MP4; review-only v3 Streams", False), "development")
        clock = FrameReceiptClock(context, utc_clock=lambda: datetime.now(timezone.utc), monotonic_clock=time.monotonic, policy=ClockPolicy())
        if not owned.client.ping():
            raise BindingError("owned_redis_predecode_ping_failed")
        report["predecode_checks"] = dict(redis_import=True, owned_redis_reachable=True,
            artifacts_verified=True, cuda_verified=True, decoder_opens=0)
        source = MP4Input(a.video, clock)
        source.__enter__()
        report["decoder_opens"] = source.opens
        fps = source.metadata["fps"]
        if fps <= 0:
            raise BindingError("missing_actual_fps")
        detector.frame_rate = fps
        with native_access():
            vehicle = LocalDetector(assets.path("vehicle"), assets.path("vehicle-tracker"), device="cuda:0", frame_rate=fps, threads=1)
        estimate, quality = pixel_operations()
        engine = CombinedEngine(context, clock, vehicle, detector, {}, algorithm=RecognitionIdentity("t1-hsv-review", 1, "classify"),
            quality_algorithm="t1-colour-quality", plate_quality_algorithm="t1-plate-crop-quality", estimate=estimate, quality_fn=quality,
            limits=VideoPlateLimits(max_frames=a.max_frames, max_seconds=a.max_seconds, max_attempts_total=a.max_crops))
        admission = True
        frames, stop = {}, "frame_budget"
        while source.decoded < a.max_frames:
            if time.monotonic() >= deadline:
                stop = "time_budget"; break
            packet = source.read()
            if packet is None:
                stop = "eof"; break
            frames[packet.receipt.frame_index] = (packet.receipt, sha256(packet.pixels.tobytes(order="C")).hexdigest())
            engine.process(packet)
            if vehicle.placement["parameter_devices"] != ["cuda:0"]:
                raise BindingError("vehicle_cpu_fallback")
        report.update(frames=source.decoded, stop_reason=stop, source_metadata=source.metadata)
        source.close()
        engine.finish(RunEnd.END_OF_INPUT if stop == "eof" else RunEnd.SERVICE_STOP, trigger=stop)
        selected = [(s, resource) for s in engine.plate.lives.values() for _, resource in s.best.values()]
        if not selected or len(selected) > a.max_crops:
            raise BindingError("associated_crop_budget_or_missing")
        report["associations"] = dict(Counter(row["association"]["status"] for row in engine.plate.associations))
        scope = Scope(str(org), context.run_id, context.camera.camera_id, uuid4().hex)
        namespace = "cuda-v3-" + uuid4().hex[:20]
        live = LiveEngine(owned.client, namespace=namespace, scope=scope, runtime=models)
        for s, resource in selected:
            if s.blocker is not None:
                raise BindingError("t1_association_evidence_blocked")
            c = resource.binding.candidate
            receipt, frame_hash = frames[c.receipt.frame_index]
            bound = bind_crop(resource, s.life, plate_epoch=engine.plate.plate_epoch, frame_receipt=receipt, frame_sha256=frame_hash)
            live.add(bound, tunis_mode=a.tunis_mode)
        plate = PlateWorker(owned.client, codec=live.codec, namespace=namespace, consumer="review-plate")
        peer = EnginePeer(owned.client, live=live, namespace=namespace, consumer="review-engine")
        plate.setup(); peer.setup(); live.flush_offers()
        while True:
            if time.monotonic() >= deadline:
                raise BindingError("redis_exchange_admission_deadline")
            plate.step(); peer.step()
            if all(s.completion is not None for s in live.slots.values()) and pending(owned.client, plate) == pending(owned.client, peer) == 0:
                break
        report["streams"] = {k: owned.client.xlen(live.journal.key(v)) for k, v in STREAMS.items()}
        report["pending"] = dict(plate=pending(owned.client, plate), engine=pending(owned.client, peer),
            plate_intent=plate.journal.client.hexists(plate.journal.state, "pending"), engine_intent=peer.journal.client.hexists(peer.journal.state, "pending"))
        rows = owned.client.xrange(live.journal.key(STREAMS["plate_result"]))
        if len(rows) != len(selected) or report["streams"]["diagnostic"] != 0 or report["streams"]["engine_diagnostic"] != 0:
            raise BindingError("review_result_count_or_diagnostic")
        for _, fields in rows:
            body = live.codec.read(fields[b"json"])["body"]
            c = next(s.engine.crop for s in live.slots.values() if asdict(s.engine.crop) == body["crop"])
            s = live.slots[c.session_id]
            calls = dict(models.calls)
            reference = memory_replay(c, a.tunis_mode, s.thresholds, live.captured(c.session_id))
            if canonical(body["decision"]) != canonical(asdict(reference.decision)) or calls != models.calls:
                raise BindingError("redis_memory_decision_parity_failed")
            report["parity"].append(dict(equal=True, decision_sha256=body["decision_sha256"],
                category=reference.decision.category, status=reference.decision.status, reason=reference.decision.reason,
                reason_kind=reference.decision.reason_kind, operations=len(s.engine._requests), retired=len(s.engine._results),
                replay_model_calls=0))
        report["accepted"] = sum(v["status"] == "accepted" for v in report["parity"])
        report["unresolved"] = sum(v["status"] == "unresolved" for v in report["parity"])
        if review_hook is not None:
            before = dict(models.calls)
            report["db1_review"] = review_hook(engine=engine, models=models, live=live, plate=plate, owned=owned, report=report)
            report["db1_records"] = report["db1_review"].get("db1_records", 0)
            report["db1_publications"] = report["db1_review"].get("db1_publications", 0)
            if before != models.calls:
                raise BindingError("delivery_invoked_recognition")
        report["status"], exit_code = "passed", 0
    except Exception as error:
        report["status"] = "blocked" if not admission or isinstance(error, BindingError) else "failed"
        report["failure_type"] = type(error).__name__
        report["failure_code"] = str(error) if isinstance(error, BindingError) else "bounded_v3_review_exception"
        code = getattr(error, "code", None)
        if isinstance(code, str) and __import__('re').fullmatch(r'[A-Z0-9_]{1,96}', code):
            report["failure_code"] = code
        exit_code = 2 if report["status"] == "blocked" else 1
    finally:
        errors = []
        resources = list(engine.plate.buffers) if engine is not None else []
        if live is not None:
            resources += [a.bound.resource for s in live.slots.values() for a in s.adapters.values()]
        resources = list({id(resource): resource for resource in resources}.values())
        if source is not None:
            source.close()
            report["decoder_opens"] = source.opens
        active = live.pending_native if live is not None else 0
        report["unretired_operations"] = active
        if live is not None:
            report["operations"] = dict(admitted=sum(len(s.engine._requests) for s in live.slots.values()),
                                         retired=sum(len(s.engine._results) for s in live.slots.values()))
        if not active:
            if live is not None:
                for s in live.slots.values():
                    for adapter in s.adapters.values():
                        adapter.release(discard=True)  # Explicit bounded-test abort cleanup if not completed.
            if engine is not None:
                for resource in engine.plate.buffers:
                    resource.release()
            for obj in (models, engine if engine is not None else vehicle, detector if engine is None else None):
                if obj is not None:
                    try:
                        if obj is engine:
                            obj.close()
                        else:
                            with native_access():
                                obj.close()
                    except Exception as error:
                        errors.append(type(error).__name__)
        else:
            errors.append("unretired_native_resources_retained")
        stages = defaultdict(lambda: dict(calls=0, seconds=0.0, failed=0))
        if live is not None:
            for s in live.slots.values():
                for adapter in s.adapters.values():
                    for event in adapter.events:
                        stages[event["role"]]["calls"] += 1
                        stages[event["role"]]["seconds"] += event["seconds"]
                        stages[event["role"]]["failed"] += event["outcome"] == "failed"
        if engine is not None:
            starts = {}
            for event in engine.audit.events:
                if event["phase"] == "start":
                    starts[event["kind"]] = event["monotonic"]
                else:
                    stages[event["kind"]]["calls"] += 1
                    stages[event["kind"]]["seconds"] += event["monotonic"] - starts.pop(event["kind"])
            report["vehicle_plate_calls"] = engine.audit.calls
            report["crop_cleanup"] = dict(allocations=len(resources), released=sum(r.released for r in resources),
                release_calls=sum(r.release_calls for r in resources), remaining=sum(not r.released for r in resources),
                retained_handles=len(engine.plate.buffers))
        report["execution_errors"] = sum(v["failed"] for v in stages.values())
        report["per_stage"] = dict(stages)
        if models is not None:
            report.update(model_calls=models.calls, model_placements=models.placements,
                actual_provenance={r: asdict(models.expected(r)) for r in models.calls},
                configuration_manifests={r: assets.material(r) for r in models.calls})
        report["detector_placement"] = detector.placement if detector else None
        report["vehicle_placement"] = vehicle.placement if vehicle else None
        if owned is not None:
            owned.__exit__(None, None, None)
            report["owned_redis_cleaned"] = owned.cleaned
            if not owned.cleaned:
                errors.append("owned_redis_cleanup_failed")
        report["cleanup_errors"] = errors
        if (errors or report["execution_errors"]) and report["status"] == "passed":
            report["status"], exit_code = "failed", 1
        report["elapsed_seconds"] = time.perf_counter() - started
        (a.output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({k: report[k] for k in ("status", "frames", "decoder_opens", "accepted", "unresolved", "execution_errors", "cleanup_errors")}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
