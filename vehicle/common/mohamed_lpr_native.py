"""Explicit local CUDA bindings; no capture, policy, provider or download route."""
from contextlib import contextmanager
from hashlib import sha256
import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

from .mohamed_lpr_contract import Provenance, ReferenceIndex, canonical
from .mohamed_lpr_engine import BindingError

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = "vehicle/license_plate_service/run_video_pipeline"
PINNED = "f64ac4cc61db4a3531e041cc2d7a6a877b4a4f16"


def file_hash(path):
    value = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def hashed(value):
    return sha256(canonical(value)).hexdigest()


def check(condition, code):
    if not condition:
        raise BindingError(code)


def revision(root):
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def cuda_preflight():
    """Observe actual imported builds, not environment names or dist-info alone."""
    packages = {}
    for n in (
        "torch", "torchvision", "paddlepaddle-gpu", "paddleocr", "paddlex",
        "onnxruntime-gpu", "open-image-models", "ultralytics", "numpy", "opencv-python", "lap"):
        try:
            packages[n] = importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            raise BindingError("missing_runtime_package_" + n) from None
    for name, expected in {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
                           "paddlepaddle-gpu": "3.3.1", "paddleocr": "3.7.0",
                           "open-image-models": "0.6.0", "ultralytics": "8.4.46"}.items():
        check(packages[name] == expected, "unapproved_runtime_" + name)
    import torch
    import paddle
    import onnxruntime as ort
    check(torch.cuda.is_available() and torch.version.cuda == "12.8", "torch_cuda_unavailable")
    check(paddle.__version__ == "3.3.1" and paddle.is_compiled_with_cuda()
          and paddle.device.cuda.device_count() > 0, "paddle_cuda_unavailable")
    check("CUDAExecutionProvider" in ort.get_available_providers(), "onnx_cuda_unavailable")
    paddle.set_device("gpu:0")
    torch.cuda.synchronize(0)
    paddle.device.synchronize()
    return dict(packages=packages, python=sys.version.split()[0], torch_cuda=torch.version.cuda,
                paddle_cuda=paddle.version.cuda(), gpu=torch.cuda.get_device_name(0),
                paddle_device=paddle.get_device(), onnx_providers=ort.get_available_providers(),
                device="cuda:0", cpu_fallback=False)


class Assets:
    """A reviewed lock, not trust-on-first-use of arbitrary files."""
    def __init__(self, source, cache, *, lock=None):
        self.source, self.cache = Path(source).resolve(), Path(cache).resolve()
        self.lock = lock or json.loads(Path(__file__).with_name("mohamed_lpr_assets.json").read_text())
        check(self.lock["source_head"] == PINNED == revision(self.source), "source_revision_drift")
        self.active = self.source / ACTIVE
        self.runtime = None
        self.effective = {}
        self.binding_files = {str(p.relative_to(ROOT)): file_hash(p) for p in (
            Path(__file__), ROOT / "vehicle/common/mohamed_lpr_engine.py",
            ROOT / "vehicle/common/engine_plate_local.py", ROOT / "vehicle/common/real_video/plate_local.py",
            ROOT / "vehicle/license_plate_service/run_video_pipeline/marker_localizer.py",
            ROOT / "vehicle/license_plate_service/run_video_pipeline/crop_geometry.py")}
        self.verify_all()

    def path(self, label):
        item = self.lock["files"][label]
        base = {"source": self.source, "cache": self.cache, "target": ROOT}[item["base"]]
        path = (base / item["path"]).resolve()
        check(path.is_relative_to(base), "artifact_path_escape")
        return path

    def verify(self, labels):
        for label in labels:
            expected = self.lock["files"][label]
            path = self.path(label)
            check(path.is_file(), "missing_artifact_" + label)
            check(path.stat().st_size == expected["bytes"] and file_hash(path) == expected["sha256"],
                  "wrong_artifact_" + label)
        for relative, expected in self.binding_files.items():
            check(file_hash(ROOT / relative) == expected, "engine_binding_drift")

    def verify_all(self):
        self.verify(tuple(self.lock["files"]))
        check(subprocess.check_output(["git", "-C", str(self.source), "status", "--porcelain"], text=True) == "",
              "source_checkout_dirty")
        for relative, expected in self.lock["source_code"].items():
            check(file_hash(self.active / relative) == expected, "source_code_drift")
        vendor = self.active / "license_plate_plugin/assets/vendor/dinov3"
        check(revision(vendor) == self.lock["vendor_revision"], "dino_vendor_revision_drift")
        tree = {str(p.relative_to(vendor)): file_hash(p) for p in vendor.rglob("*.py") if "__pycache__" not in p.parts}
        check(hashed(tree) == self.lock["vendor_python_sha256"], "dino_vendor_code_drift")
        # Exact matching reference bytes may live in the source experiment data;
        # the bundled manifest's images directory is absent. Never rebuild index.
        for entry in self.lock["references"]:
            check(file_hash(self.source / entry["path"]) == entry["sha256"], "wrong_reference_image")
        metadata = json.loads(self.path("index-metadata").read_text())
        check(metadata["compatibility"] == self.lock["index_compatibility"]
              and metadata["compatibility_fingerprint"] == self.lock["index_compatibility_fingerprint"], "wrong_reference_index")
        entries = tuple((str(r["id"]), r["category"]) for r in metadata["references"])
        check(entries == tuple((r["reference_id"], r["category"]) for r in self.lock["references"]), "wrong_index_categories")
        self.index_identity = ReferenceIndex("approved-dinov3-r2", hashed({k: self.lock["files"][k]["sha256"]
            for k in ("index", "index-metadata", "reference-manifest")}), entries)

    def labels(self, role):
        if role == "visual":
            return ("dino", "index", "index-metadata", "reference-manifest", "plugin-config")
        if role == "marker":
            return ("marker",)
        prefix = "arabic" if role == "category_ocr" else "registration"
        return tuple(prefix + "-" + n for n in ("inference.json", "inference.pdiparams", "inference.yml"))

    def material(self, role):
        import yaml
        labels = self.labels(role)
        config = dict(role=role, source_head=PINNED, source_code=self.lock["source_code"],
                      binding_code=self.binding_files, device="gpu:0" if role in {"category_ocr", "registration", "left", "right"} else "cuda:0")
        key = role if role in {"category_ocr", "visual", "marker"} else "registration"
        if key in self.effective:
            config["observed_effective_runner"] = self.effective[key]
        if role == "visual":
            config.update(preprocessing=self.lock["index_compatibility"], full_reference_vector=True)
        elif role == "marker":
            config.update(resize="direct-stretch-512", conf=.05, imgsz=512, stream=False)
        else:
            yml = yaml.safe_load(self.path(labels[-1]).read_text())
            config.update(export_config_sha256=self.lock["files"][labels[-1]]["sha256"],
                          dictionary_sha256=hashed(yml.get("PostProcess", {}).get("character_dict", [])),
                          enable_hpi=False, enable_mkldnn=False, cpu_threads=1, batch_size=1,
                          input="R2-budget-unpadded-BGR" if role == "category_ocr" else "original-requested-BGR-region")
        return dict(model_files={k: self.lock["files"][k]["sha256"] for k in labels}, config=config)

    def provenance(self, role):
        check(self.runtime is not None, "cuda_not_verified")
        material = self.material(role)
        model = {"visual": "dinov3_vits16-R2", "marker": "tounes_word_detector", "category_ocr": "arabic_PP-OCRv5_mobile_rec"}.get(role, "PP-OCRv6_medium_rec")
        return Provenance(model, hashed(material["model_files"]), hashed(material["config"]),
                          hashed(dict(config=material["config"], role=role)), hashed(self.runtime), material["config"]["device"])


def source_plugin(assets):
    """Private namespace: never replace T1's legacy absolute package alias."""
    alias = "_t1_mohamed_f64ac4_lpr_plugin"
    path = assets.active / "license_plate_plugin"
    if alias in sys.modules:
        check(tuple(sys.modules[alias].__path__) == (str(path),), "source_namespace_conflict")
    else:
        package = types.ModuleType(alias)
        package.__path__ = [str(path)]
        sys.modules[alias] = package  # Do not execute classifier/package __init__.
    return lambda module: importlib.import_module(alias + "." + module)


@contextmanager
def source_detector_config():
    """Scope source's two legacy absolute imports to explicit Engine settings."""
    names = ("run_video_pipeline", "run_video_pipeline.config")
    previous = {n: sys.modules.get(n) for n in names}
    package = types.ModuleType(names[0]); package.__path__ = []
    config = types.ModuleType(names[1])
    for k, v in dict(PLATE_CONF=.25, DETECT_IMGSZ=640, PLATE_DEDUP_IOU=.8,
                     PLATE_DETECTOR_PROVIDERS="CUDAExecutionProvider",
                     PLATE_DETECTOR_INTRA_OP_THREADS=2, PLATE_DETECTOR_INTER_OP_THREADS=1).items():
        setattr(config, k, v)
    package.config = config
    sys.modules[names[0]], sys.modules[names[1]] = package, config
    try:
        yield config
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class CudaModels:
    def __init__(self, assets):
        self.assets = assets
        check(assets.runtime is not None, "cuda_not_verified")
        self.models, self.placements, self.calls = {}, {}, {}
        self.index_identity = assets.index_identity
        self.closed = False

    def expected(self, role):
        return self.assets.provenance(role)

    def verify(self, role):
        check(not self.closed, "models_closed")
        self.assets.verify(self.assets.labels(role))
        check(self.index_identity == self.assets.index_identity, "reference_index_identity_drift")
        if role == "visual" and "visual" in self.models:
            _, index, _ = self.models["visual"]
            check(index is not None and tuple((str(r["id"]), r["category"]) for r in index.references)
                  == self.index_identity.entries, "loaded_reference_index_drift")

    def initialize(self, role):
        key = role if role in {"visual", "category_ocr", "marker"} else "registration"
        if key in self.models:
            return self.models[key]
        import torch
        check(torch.cuda.is_available(), "cuda_disappeared")
        if key == "visual":
            mod = source_plugin(self.assets)
            config = mod("config").load_config(self.assets.path("plugin-config"))
            encoder = mod("dinov3").DinoV3Encoder(config.dinov3_repo, self.assets.path("dino"), "cuda:0")
            self.models[key] = (encoder, None, config)  # Retain partial initialization for cleanup.
            expected = mod("reference_index").compatibility_record(config, encoder)
            check(expected == self.assets.lock["index_compatibility"], "actual_dino_index_incompatible")
            index = mod("reference_index").ReferenceIndex.load(self.assets.path("index"), expected)
            model = (encoder, index, config)
            placement = dict(device=str(encoder.device), parameter_devices=sorted({str(p.device) for p in encoder.model.parameters()}),
                             reference_index_sha256=self.index_identity.index_sha256)
        elif key == "marker":
            from ultralytics import YOLO
            model = YOLO(str(self.assets.path("marker")), task="detect")
            self.models[key] = model
            model.to("cuda:0")
            check(Path(model.ckpt_path).resolve() == self.assets.path("marker"), "actual_marker_artifact_mismatch")
            placement = dict(device="cuda:0", parameter_devices=sorted({str(p.device) for p in model.model.parameters()}))
        else:
            import paddle
            from paddleocr import TextRecognition
            directory = self.assets.path(("arabic" if key == "category_ocr" else "registration") + "-inference.yml").parent
            model_name = "arabic_PP-OCRv5_mobile_rec" if key == "category_ocr" else "PP-OCRv6_medium_rec"
            check(paddle.is_compiled_with_cuda() and paddle.get_device() == "gpu:0", "paddle_cpu_fallback")
            model = TextRecognition(model_name=model_name, model_dir=str(directory), device="gpu:0",
                                    enable_hpi=False, enable_mkldnn=False, cpu_threads=1)
            self.models[key] = model
            check(model._model_name == model_name and Path(model._model_dir).resolve() == directory, "actual_ocr_artifact_mismatch")
            # Inspect the initialized Paddle predictor option rather than only
            # reporting the requested argument or process-global Paddle device.
            runner = model.paddlex_predictor.runner
            option = runner._config
            check(type(runner).__name__ == "PaddleStaticRunner" and option["device_type"] == "gpu"
                  and option["device_id"] == 0, "actual_ocr_placement_mismatch")
            self.assets.effective[key] = dict(option)
            placement = dict(device="gpu:0", predictor_device=option["device_type"], device_id=option["device_id"],
                             paddle_device=paddle.get_device(), cuda_compiled=paddle.is_compiled_with_cuda())
        if key in {"visual", "marker"}:
            check(placement["parameter_devices"] == ["cuda:0"], "actual_torch_cpu_fallback")
        self.models[key], self.placements[key] = model, placement
        return model

    def synchronize(self):
        import torch
        import paddle
        torch.cuda.synchronize(0)
        paddle.device.synchronize()

    def invoke(self, role, image, context):
        try:
            model = self.initialize(role)
            # Probe identities again immediately before the actual native call.
            self.verify(role)
            context.started(self.expected(role))
            self.calls[role] = self.calls.get(role, 0) + 1
            if role == "visual":
                from .mohamed_plate_policy import ReferenceScore
                mod = source_plugin(self.assets)
                encoder, index, config = model
                query, _ = mod("representations").extract_r2(image, encoder, config.policy)
                scores = index.embeddings @ query.astype("float32")
                # Preserve every native cosine; no clipping, threshold or winner
                # selection in Engine. Invalid native scores fail contract decode.
                return dict(scores=tuple(ReferenceScore(str(r["id"]), r["category"], float(s))
                                         for r, s in zip(index.references, scores)), reference_index=self.index_identity)
            if role == "marker":
                import cv2
                from .engine_plate_local import marker_tools
                tools = marker_tools()
                check(tools.config.WORD_IMGSZ == 512, "marker_decoder_configuration_mismatch")
                outputs = model.predict([cv2.resize(image, (512, 512))], conf=.05, imgsz=512,
                                        device=0, verbose=False, stream=False)
                check(len(outputs) == 1, "marker_output_cardinality")
                marker = tools.TounesMarkerLocalizer._decode(outputs[0], image)
                return dict(detected=marker.detected, confidence=marker.confidence, bbox=marker.bbox)
            if role == "category_ocr":
                mod = source_plugin(self.assets)
                geometry = mod("preprocessing").calculate_geometry(image.shape, (16, 16), mod("config").ResolutionPolicy())
                image, _ = mod("preprocessing").resize_and_pad(image, geometry, mod("config").ResolutionPolicy())
            from .engine_plate_local import raw_ocr
            return raw_ocr(list(model.predict(input=image, batch_size=1)))
        finally:
            # Failure after native start is a failed read only if every native
            # user is quiescent. A synchronization failure leaves the lease held.
            self.synchronize()
            context.retired()

    def close(self):
        if self.closed:
            return
        self.synchronize()
        for key, model in tuple(self.models.items()):
            if key in {"category_ocr", "registration"}:
                model.close()
        self.models.clear()
        model = None
        import gc
        gc.collect()
        import torch
        torch.cuda.empty_cache()
        self.closed = True


class CudaPlateDetector:
    """Approved ONNX backend plus the unchanged T1 ByteTrack/geometry binding."""
    def __init__(self, assets, *, frame_rate):
        self.assets, self.frame_rate = assets, frame_rate
        self.backend = self.tracker = None
        self.closed = False
        self.placement = {}

    def initialize(self):
        self.load_backend()
        if self.tracker is not None:
            return
        check(self.frame_rate is not None and self.frame_rate > 0, "missing_actual_frame_rate")
        from .real_video.plate_local import TrackerIDs
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.trackers.basetrack import BaseTrack
        import yaml
        self.ids = TrackerIDs(BaseTrack)
        args = types.SimpleNamespace(**yaml.safe_load(self.assets.path("plate-tracker").read_text()))
        with self.ids.use():
            self.tracker = BYTETracker(args, frame_rate=self.frame_rate)
        self.placement.update(frame_rate=self.frame_rate,
                              tracker_sha256=self.assets.lock["files"]["plate-tracker"]["sha256"])

    def load_backend(self):
        if self.backend is not None:
            return
        self.assets.verify(("plate", "plate-tracker"))
        path = self.assets.active / "onnx_detector.py"
        spec = importlib.util.spec_from_file_location("_t1_mohamed_onnx", path)
        module = importlib.util.module_from_spec(spec)
        with source_detector_config():
            spec.loader.exec_module(module)
        self.backend = module.LocalPlateBackend(self.assets.path("plate"))
        self.backend.load()
        self.placement = {k: self.backend.metadata[k] for k in (
            "model_id", "sha256", "actual_providers", "cpu_fallback_allowed", "precision", "input_shape")}
        self.placement.update(device="cuda:0")

    def process(self, image):
        self.initialize()
        self.assets.verify(("plate", "plate-tracker"))
        from .real_video.plate_local import geometry, PlateDetections
        g, tools = geometry()
        raw = []
        for result in self.backend.predict(image):
            box = result.bounding_box
            confidence = float(result.confidence)
            if confidence < .25:
                continue
            clipped = tools.clamp_bbox((box.x1, box.y1, box.x2, box.y2), image.shape[1], image.shape[0])
            if clipped is not None:
                raw.append((*clipped, confidence))
        detections = tools.deduplicate_detections(raw, iou_threshold=.8)
        with self.ids.use():
            tracked = self.tracker.update(g.TrackerDetections.from_tuples(detections, association_padding=.18), image)
        used, matched = set(), []
        for row in tracked:
            detection = g._detection_for_track(row, detections, .18, used)
            if detection is not None:
                matched.append((int(row[4]), tuple(detection)))
        return PlateDetections(raw, matched, [d for i, d in enumerate(detections) if i not in used], len(raw) - len(detections))

    def close(self):
        self.backend = self.tracker = None
        self.closed = True
