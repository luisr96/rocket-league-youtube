"""Upload recorded videos that are not on YouTube yet, then delete the large files.

A video is pending when it is in the output folder with its .json data file,
is not a debug video, and history has no YouTube id for it. After a successful
upload the YouTube id is saved in history and the video and its data file are
deleted; the thumbnails are kept so another one can be chosen in YouTube Studio.
"""
import json
import logging
from datetime import date
from pathlib import Path

from . import describe, thumbnail, youtube
from .config import project_path

log = logging.getLogger(__name__)


def pending(out_dir: Path, history) -> list[Path]:
    """Videos waiting to be uploaded, oldest first."""
    out = []
    for v in sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime):
        if "_debug" in v.stem or v.stem.endswith(".part") or not v.with_suffix(".json").exists():
            continue
        if any(e.get("youtube_id") for e in history.for_video(str(v))):
            continue  # uploaded before, but the files were not deleted
        out.append(v)
    return out


def thumbnail_for(video: Path, data: dict) -> Path | None:
    """The chosen goal's thumbnail (see thumbnail.choose_goal), else any candidate (e.g. the no-goal one)."""
    choice = thumbnail.default_label(data)
    preferred = video.with_name(f"{video.stem}_thumb_{choice}.jpg")
    if choice and preferred.exists():
        return preferred
    others = sorted(video.parent.glob(f"{video.stem}_thumb_*.jpg"))
    return others[0] if others else None


RECENT_TITLES = 5  # a title used in this many latest uploads is not picked again (if others are left)


def upload_one(yt, video: Path, settings: dict, history) -> str:
    data = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    titles = describe.load_titles(project_path(settings.get("titles_file", "titles.txt")))
    used = [e["youtube_title"] for e in history.entries if e.get("youtube_title")]
    recent = list(dict.fromkeys(reversed(used)))[:RECENT_TITLES]  # newest first, one per video
    title = describe.title(data, titles, recent, special_weight=float(settings.get("special_title_weight", 3)))
    desc, tags = describe.description(data), describe.tags(data)
    print(f"Uploading {video.name}\n  title: {title}")
    log.info("uploading %s as %r", video, title)
    vid = youtube.upload(yt, video, title, desc, tags, settings.get("privacy", "private"),
                         str(settings.get("category_id", "20")))
    url = f"https://youtu.be/{vid}"
    print(f"  uploaded: {url}")
    history.update_video(str(video), {"youtube_id": vid, "youtube_url": url, "youtube_title": title,
                                      "uploaded_date": date.today().isoformat()})

    thumb = thumbnail_for(video, data)
    if thumb:
        try:
            youtube.set_thumbnail(yt, vid, thumb)
            print(f"  thumbnail: {thumb.name}")
        # Any failure here (also the daily quota) is only a warning: the video is already
        # on YouTube and marked uploaded, so stopping now would leave its files behind.
        except youtube.YouTubeError as e:  # e.g. channel not verified, or quota used up
            log.warning("thumbnail not set for %s: %s", vid, e)
            print(f"  WARNING: thumbnail not set ({e}); set it in YouTube Studio")

    if settings.get("delete_after_upload", True):
        for p in (video, video.with_suffix(".json")):
            try:
                p.unlink()
            except OSError as e:
                log.warning("could not delete %s: %s", p, e)
        print("  deleted the video and its data file (thumbnails kept)")
    return vid


def cmd_upload(cfg, api, history) -> int:
    """Upload every pending video. Returns 0 if all went well."""
    s = cfg.raw.get("youtube", {})
    videos = pending(cfg.path("output_dir"), history)
    if not videos:
        print("Nothing to upload.")
        return 0
    yt = youtube.connect(project_path(s.get("client_secrets", "youtube_client_secret.json")),
                         project_path(s.get("token_file", "youtube_token.json")))
    failed = 0
    for v in videos:
        try:
            upload_one(yt, v, s, history)
        except youtube.QuotaError as e:
            log.warning("%s", e)
            print(f"Stopping: {e}")
            return 1
        except (youtube.YouTubeError, OSError, KeyError, ValueError) as e:
            failed += 1
            log.error("upload of %s failed: %s", v, e)
            print(f"  ERROR: upload failed ({e}); it will be retried next time")
    return 1 if failed else 0
