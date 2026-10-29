"""Prepare pipeline output for playback in the web application."""
import shutil
import subprocess
import tempfile
from pathlib import Path


def prepare_browser_video(path: Path) -> None:
    """Replace an OpenCV MP4 with H.264, preserving the source on failure."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required to prepare browser videos. Install ffmpeg and retry.")
    with tempfile.TemporaryDirectory(prefix="browser-video-", dir=path.parent) as directory:
        output = Path(directory) / "encoded.mp4"
        result = subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(path), "-map", "0:v:0", "-an",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(output),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Browser video encoding failed: {result.stderr.strip()}")
        output.replace(path)
