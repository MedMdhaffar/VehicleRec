"""Opt-in real Django/standalone import audit in separate Python processes."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("PLATE_PLUGIN_IMPORT_AUDIT") == "1", "Requires installed plugin and pipeline dependencies")
class IntegrationImportTests(unittest.TestCase):
    def test_django_and_standalone_resolve_same_plugin_and_actual_recognition(self):
        plugin = Path(__file__).resolve().parents[1]
        repository = plugin.parents[2]
        code = '''
import importlib
import sys
from pathlib import Path
import license_plate_plugin
from license_plate_plugin import load_default_config
root, mode, plugin = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
assert Path(license_plate_plugin.__file__).resolve() == plugin / "license_plate_plugin/__init__.py"
assert load_default_config().reference_index == plugin / "assets/reference_index/reference_r2.npz"
if mode == "django":
    sys.path.insert(0, str(root / "vehicle"))
    from license_plate_service.service import _ensure_pipeline_import_path
    _ensure_pipeline_import_path()
    active_root = root / "vehicle/license_plate_service"
else:
    active_root = root / "services/license_plate_service"
    sys.path.insert(0, str(active_root))
video = importlib.import_module("run_video_pipeline.run_video_prototype")
rtsp = importlib.import_module("run_rtsp_pipeline.run_rtsp_prototype")
rtsp_runner = importlib.import_module("run_rtsp_pipeline.run_video_prototype")
video_prototype = importlib.import_module("run_video_pipeline.prototype_pipeline")
rtsp_prototype = importlib.import_module("run_rtsp_pipeline.prototype_pipeline")
recognition = importlib.import_module("run_video_pipeline.pipeline")
assert Path(video.__file__).resolve() == active_root / "run_video_pipeline/run_video_prototype.py"
assert Path(rtsp.__file__).resolve() == active_root / "run_rtsp_pipeline/run_rtsp_prototype.py"
assert rtsp.VideoPrototypeRunner is rtsp_runner.VideoPrototypeRunner
assert rtsp.PrototypePlatePipeline is rtsp_prototype.PrototypePlatePipeline
assert rtsp_prototype.analyze_plate_crop is recognition.analyze_plate_crop
assert video_prototype.analyze_plate_crop is recognition.analyze_plate_crop
assert rtsp_runner.PrototypePlatePipeline is video_prototype.PrototypePlatePipeline
assert rtsp_prototype.route_plate.__module__ == "run_video_pipeline.routing"
assert rtsp.get_reader.__module__ == "run_rtsp_pipeline.ocr"
assert rtsp.get_reader.__globals__["config"].__name__ == "run_video_pipeline.config"
print(mode, "imports verified:", recognition.__file__, license_plate_plugin.__file__)
'''
        # Remove source-path injection: the plugin must resolve via installation.
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("django", "standalone"):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        [sys.executable, "-B", "-c", code, str(repository), mode, str(plugin)],
                        cwd=directory, env=env, capture_output=True, text=True, timeout=120,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
