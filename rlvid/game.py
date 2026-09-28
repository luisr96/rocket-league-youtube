"""Launch Rocket League (Epic) with BakkesMod and play a replay through the RLVid plugin."""
import ctypes
import logging
import os
import subprocess
import time
from pathlib import Path

from .bakkes import BakkesError, Rcon, read_status

log = logging.getLogger(__name__)


class GameError(Exception):
    pass


def _running(image: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                         capture_output=True, text=True).stdout
    return image.lower() in out.lower()


def ensure_game(cfg: dict) -> Rcon:
    """Start BakkesMod and the game if needed; return a connected rcon once the plugin is live."""
    if not _running("BakkesMod.exe"):
        exe = Path(cfg["bakkesmod_exe"])
        if not exe.exists():
            raise GameError(f"BakkesMod not found at {exe}")
        log.info("starting BakkesMod")
        subprocess.Popen([str(exe)])
        time.sleep(5)
    if not _running("RocketLeague.exe"):
        log.info("launching Rocket League via Epic")
        print("Launching Rocket League ...")
        os.startfile(cfg["epic_launch_uri"])  # not "cmd start": the URI contains "&"

    rcon = Rcon()
    deadline = time.monotonic() + cfg["launch_timeout_seconds"]
    while True:
        try:
            rcon.connect()
            break
        except BakkesError:
            if time.monotonic() > deadline:
                raise GameError("game/BakkesMod did not become ready in time (rcon unreachable). "
                                "Is -noeac set in the Epic launch options?")
            time.sleep(3)
    rcon.send("rcon_refresh_allowed")
    rcon.send("plugin load rlvid")  # no-op if plugins.cfg already loaded it

    # Wait for the plugin's status file to be fresh, i.e. the plugin is running.
    while True:
        s = read_status()
        if s and time.time() - s["time"] < 3:
            break
        if time.monotonic() > deadline:
            raise GameError("RLVid plugin is not reporting status; is RLVid.dll in the BakkesMod plugins folder?")
        time.sleep(1)
    time.sleep(cfg["menu_settle_seconds"])
    return rcon


def _wait(pred, timeout: float, what: str) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = read_status()
        if s and pred(s):
            return s
        time.sleep(0.5)
    raise GameError(f"timed out waiting for {what} (last status: {read_status()})")


def park_cursor() -> None:
    """Move the mouse cursor to the bottom-right screen corner so it is out of the way."""
    user32 = ctypes.windll.user32
    user32.SetCursorPos(user32.GetSystemMetrics(0) - 1, user32.GetSystemMetrics(1) - 1)


def start_replay(rcon: Rcon, replay: Path, target: str, timeout: float, kickoff_seconds: float = 0) -> dict:
    """Load the replay with the camera locked on target (focus id or name). Returns status once locked.

    The plugin applies the HUD and camera settings itself from the first frame,
    so nothing needs configuring after playback starts.
    """
    before = read_status() or {}
    park_cursor()
    rcon.send(f'rlvid_play "{replay}" "{target}" {kickoff_seconds:g}')

    # If a replay is already playing, its status still says "playing" until the
    # new one loads, so first wait for evidence of a reload: leaving the replay
    # (loading screen) or the frame counter going back.
    if before.get("in_replay"):
        old_frame = before.get("frame", 0)
        _wait(lambda s: not s["in_replay"] or 0 <= s["frame"] < old_frame or s["event"].startswith("play_error"),
              timeout, "previous replay to unload")
    s = _wait(lambda s: s["event"].startswith("play_error") or (s["in_replay"] and s.get("locked")),
              timeout, f"replay to start with camera locked on {target}")
    if s["event"].startswith("play_error"):
        raise GameError(f"plugin could not play replay: {s['event']}")
    log.info("replay playing, camera %s on %s (%s)", s["camera_mode"], s["focused"], s["focus_id"])
    return s


def wait_for_end(max_seconds: float) -> str:
    """Block until the replay finishes. Returns why it stopped: 'ended' or 'timeout'."""
    deadline = time.monotonic() + max_seconds
    last_frame, stalled_since = -1, None
    while time.monotonic() < deadline:
        s = read_status() or {}
        if not s.get("in_replay"):
            return "ended"
        frame, total = s.get("frame", -1), s.get("num_frames", -1)
        if total > 0 and frame >= total - 2:
            return "ended"
        # The game stops advancing at the last frames without always reaching num_frames.
        if frame == last_frame and total > 0 and frame > total * 0.95:
            stalled_since = stalled_since or time.monotonic()
            if time.monotonic() - stalled_since > 3:
                return "ended"
        else:
            stalled_since = None
        last_frame = frame
        time.sleep(0.5)
    return "timeout"
