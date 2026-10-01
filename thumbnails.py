"""Remake the thumbnail candidates for an existing video (needs its .json data file).

    python thumbnails.py "<path to video>.mp4"
"""
import sys
from pathlib import Path

from rlvid import thumbnail, video
from rlvid.config import ConfigError, load_config

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    if not path.with_suffix(".json").exists():
        sys.exit(f"No data file next to the video: {path.with_suffix('.json')}")
    try:
        cfg = load_config()
        ffmpeg = video.find_ffmpeg(cfg.raw.get("video", {}).get("ffmpeg", "ffmpeg"))
        for p in thumbnail.make(ffmpeg, path, cfg.raw.get("thumbnail", {})):
            print(p)
    except (ConfigError, video.VideoError, thumbnail.ThumbnailError) as e:
        sys.exit(f"Error: {e}")
