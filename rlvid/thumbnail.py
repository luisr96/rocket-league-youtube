"""YouTube thumbnail candidates for a video, from its <video>.json data file.

For the chosen goal, a frame is taken at several moments around it (see
SHOTS) and overlay/thumbnail.html draws game 1's names on it; headless Edge
renders the page and ffmpeg saves it as a JPG (YouTube allows up to 2 MB).
"""
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from .describe import player_name

log = logging.getLogger(__name__)

OVERLAY_DIR = Path(__file__).resolve().parent.parent / "overlay"
PAGE = OVERLAY_DIR / "thumbnail.html"

# (file name label, seconds relative to the goal)
SHOTS = [("1s-before", -1.0), ("0.5s-before", -0.5), ("0.1s-before", -0.1),
         ("0.4s-after", 0.4), ("5s-after", 5.0)]
# Without any usable goal: one frame this long after game 1 starts (the names
# overlay has faded by then).
NO_GOAL_SECONDS = 8.0

EDGE_PATHS = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
              Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]


class ThumbnailError(Exception):
    pass


def choose_goal(data: dict) -> tuple[dict, str] | None:
    """The goal to build thumbnails around, and why it was chosen.

    Target's fastest goal in game 1, else a teammate's fastest in game 1,
    else the target's fastest in game 2.
    """
    games = data.get("games", [])
    g1 = games[0]["goals"] if games else []
    g2 = games[1]["goals"] if len(games) > 1 else []
    for goals, by, why in ((g1, "target", "target's goal, game 1"),
                           (g1, "teammate", "teammate's goal, game 1"),
                           (g2, "target", "target's goal, game 2")):
        found = [g for g in goals if g.get("by") == by]
        if found:
            return max(found, key=lambda g: g.get("speed", 0)), why
    return None


def rank_icon(data: dict) -> Path | None:
    """overlay/rank_<rank id>.png (e.g. rank_supersonic-legend.png) if there is one for the video's rank."""
    rank = data.get("rank") or ""
    p = OVERLAY_DIR / f"rank_{rank}.png"
    return p if rank and p.exists() else None


def page_url(settings: dict, frame: Path, data: dict) -> str:
    """The thumbnail page for one frame. Zoom and sharpening are done by ffmpeg on the frame itself."""
    overlay = data["games"][0]["overlay"]
    big = player_name(data)
    q = [("bg", frame.as_uri()),
         ("big", big if settings.get("big_name", True) else ""),
         ("layout", settings.get("layout", "corner")),
         ("teams", "1" if settings.get("team_names", False) else "0"),
         ("size", str(settings.get("names_size", 70))),
         ("pos", settings.get("names_position", "bottom")),
         ("zoom", "0"),
         ("pop", "1" if settings.get("colours", True) else "0"),
         ("sat", str(settings.get("saturation", 1.7))),
         ("con", str(settings.get("contrast", 1.25)))]
    icon = rank_icon(data) if settings.get("rank_icon", True) else None
    if icon:
        q.append(("icon", icon.as_uri()))
    q += [("blue", n) for n in overlay["blue"]] + [("orange", n) for n in overlay["orange"]]
    return PAGE.as_uri() + "?" + urlencode(q)


def frame_filter(settings: dict) -> str:
    """ffmpeg filter for the video frame: optional zoom (crop to the middle 70% of the full
    1920x1080 frame, so no detail is lost), scale to 1280x720, light sharpening."""
    steps = ["crop=iw*0.7:ih*0.7"] if settings.get("zoom", True) else []
    steps.append("scale=1280:720:flags=lanczos")
    if settings.get("sharpen", True):
        steps.append("unsharp=5:5:0.8")
    return ",".join(steps)


def find_edge(configured: str = "") -> str:
    for p in ([Path(configured)] if configured else []) + EDGE_PATHS:
        if p.exists():
            return str(p)
    exe = shutil.which("msedge")
    if exe:
        return exe
    raise ThumbnailError("Microsoft Edge not found (set [thumbnail] edge in config.toml)")


def _run(cmd: list[str], what: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ThumbnailError(f"{what} failed: {(r.stderr or r.stdout).strip()[-300:]}")


def make(ffmpeg: str, video: Path, settings: dict | None = None) -> list[Path]:
    """Write the thumbnail candidates next to video (from video's .json). Returns their paths."""
    settings = settings or {}
    data = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    chosen = choose_goal(data)
    if chosen:
        goal, why = chosen
        shots = [(label, goal["video_time"] + off) for label, off in SHOTS]
        log.info("thumbnails around %s by %s at %.1fs (%s)", goal["team"], goal["scorer"], goal["video_time"], why)
    else:
        shots = [("no-goal", data["games"][0].get("video_start", 0) + NO_GOAL_SECONDS)]
        log.info("thumbnails: no usable goal, using a frame %gs into game 1", NO_GOAL_SECONDS)

    edge = find_edge(settings.get("edge", ""))
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for label, t in shots:
            frame, shot = tmp / f"{label}.png", tmp / f"{label}_page.png"
            _run([ffmpeg, "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", str(video),
                  "-frames:v", "1", "-vf", frame_filter(settings), str(frame)], f"frame at {t:.1f}s")
            _run([edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                  f"--user-data-dir={tmp / 'edge'}", "--window-size=1280,720", "--virtual-time-budget=3000",
                  f"--screenshot={shot}", page_url(settings, frame, data)], "Edge screenshot")
            if not shot.exists():
                raise ThumbnailError(f"Edge did not write a screenshot for {label}")
            dest = video.with_name(f"{video.stem}_thumb_{label}.jpg")
            _run([ffmpeg, "-v", "error", "-y", "-i", str(shot), "-q:v", "2", str(dest)], "JPG conversion")
            out.append(dest)
    log.info("thumbnails: %s", ", ".join(p.name for p in out))
    return out
