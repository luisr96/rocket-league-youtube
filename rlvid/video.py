"""Post-processing of recordings with ffmpeg."""
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Console tools (tasklist, taskkill, ffmpeg) open no window: under pythonw each
# would otherwise flash one up.
NO_WINDOW = subprocess.CREATE_NO_WINDOW


class VideoError(Exception):
    pass


def find_ffmpeg(configured: str) -> str:
    exe = shutil.which(configured) or (configured if Path(configured).exists() else None)
    if not exe:
        raise VideoError(f"ffmpeg not found ({configured!r}); install it (winget install Gyan.FFmpeg) "
                         f"or set [video] ffmpeg in config.toml")
    return exe


def finalize(ffmpeg: str, src: Path, dest: Path) -> None:
    """Remux src into a standard, seekable MP4 at dest (stream copy: no re-encode, no quality loss).

    OBS's Hybrid/fragmented MP4 has no single index, so some players (e.g.
    Windows Media Player) cannot seek in it; +faststart also puts the index at
    the front for quick loading.
    """
    tmp = dest.with_suffix(".part.mp4")
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy",
           "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise VideoError(f"ffmpeg remux failed: {r.stderr.strip()[:500]}")
    if r.stderr.strip():
        log.warning("ffmpeg: %s", r.stderr.strip()[:500])
    tmp.replace(dest)
    src.unlink()
    log.info("remuxed %s -> %s", src, dest)
