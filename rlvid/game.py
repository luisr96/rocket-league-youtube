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


def _game_window() -> int:
    """Handle of Rocket League's main window, or 0 if there is none."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith("Rocket League") and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(check, 0)
    return found[0] if found else 0


def bring_to_front(only_if_minimized: bool = False) -> bool:
    """Restore the game window if minimized and put it in front. Returns True if it had to act.

    A full-screen game minimizes itself when another window takes focus, and
    OBS then captures black. With only_if_minimized, a game that is merely not
    in front (e.g. windowed, while you use another app) is left alone.
    """
    user32 = ctypes.windll.user32
    hwnd = _game_window()
    if not hwnd:
        return False
    minimized = bool(user32.IsIconic(hwnd))
    if not minimized and (only_if_minimized or user32.GetForegroundWindow() == hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows only lets the app with the last input take the foreground; a
    # synthetic Alt press counts as input, so SetForegroundWindow is allowed.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)  # KEYEVENTF_KEYUP
    user32.SetForegroundWindow(hwnd)
    log.info("brought the game window to the front")
    return True


def close_game() -> None:
    """Force-close Rocket League after a run (OBS and BakkesMod are left running)."""
    if _running("RocketLeague.exe"):
        log.info("closing RocketLeague.exe")
        print("Closing Rocket League ...")
        subprocess.run(["taskkill", "/IM", "RocketLeague.exe", "/F"], capture_output=True)


def close_all(timeout: float = 60) -> None:
    """Close OBS, Rocket League and BakkesMod so a run starts from a clean state.

    OBS is asked to close normally (a forced kill makes it show a crash/safe-mode
    dialog on the next start); the game and BakkesMod are force-closed.
    """
    closing = []
    for image, force in (("obs64.exe", False), ("RocketLeague.exe", True), ("BakkesMod.exe", True)):
        if _running(image):
            log.info("closing %s", image)
            subprocess.run(["taskkill", "/IM", image] + (["/F"] if force else []), capture_output=True)
            closing.append(image)
    if closing:
        print(f"Closing {', '.join(closing)} for a fresh start ...")
    deadline = time.monotonic() + timeout
    while any(_running(i) for i in closing):
        if time.monotonic() > deadline:
            still = [i for i in closing if _running(i)]
            raise GameError(f"could not close {', '.join(still)} (is an OBS dialog waiting for an answer?)")
        time.sleep(1)
    if closing:
        time.sleep(3)  # let the processes release files, ports and the game capture hook


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


def start_replay(rcon: Rcon, replay: Path, target: str, timeout: float, kickoff_seconds: float = 0,
                 hold: bool = False) -> dict:
    """Load the replay with the camera locked on target (focus id or name). Returns status once locked.

    The plugin applies the HUD and camera settings itself from the first frame,
    so nothing needs configuring after playback starts.
    """
    before = read_status() or {}
    park_cursor()
    rcon.send(f'rlvid_play "{replay}" "{target}" {kickoff_seconds:g}' + (" hold" if hold else ""))

    # If a replay is already playing, its status still says "playing" until the
    # new one loads, so first wait for evidence of a reload: leaving the replay
    # (loading screen) or the frame counter going back.
    if before.get("in_replay"):
        old_frame = before.get("frame", 0)
        _wait(lambda s: not s["in_replay"] or 0 <= s["frame"] < old_frame or s["event"].startswith("play_error"),
              timeout, "previous replay to unload")
    s = _wait(lambda s: s["event"].startswith("play_error")
              or (s["in_replay"] and s.get("locked") and (s.get("paused") or not hold)),
              timeout, f"replay to start with camera locked on {target}")
    if s["event"].startswith("play_error"):
        raise GameError(f"plugin could not play replay: {s['event']}")
    log.info("replay playing, camera %s on %s (%s)", s["camera_mode"], s["focused"], s["focus_id"])
    time.sleep(0.5)  # the camera change reaches the screen a few frames after the plugin reports it
    return s


def release(rcon: Rcon) -> None:
    """Resume a replay that start_replay(hold=True) left paused on its first frame."""
    rcon.send("rlvid_release")
    _wait(lambda s: s["in_replay"] and not s.get("paused"), 10, "replay to resume")


def wait_for_end(max_seconds: float, on_tick=None) -> str:
    """Block until the replay finishes. Returns why it stopped: 'ended' or 'timeout'.

    An unreadable status file (the plugin replaces it twice a second) is treated
    as "unknown", never as "ended"; leaving the replay must be seen for 2 s in a
    row. A status file that stops updating raises GameError.
    """
    deadline = time.monotonic() + max_seconds
    last_frame, stalled_since, left_since = -1, None, None
    last_fresh = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if on_tick:
            on_tick()
        if bring_to_front(only_if_minimized=True):
            log.warning("the game window was minimized during recording (black video); restored it")
            print("  The game window was minimized; restored it (that part of the video may be black).")
        s = read_status()
        if not s or time.time() - s.get("time", 0) > 3:
            if time.monotonic() - last_fresh > 15:
                raise GameError("RLVid status stopped updating (game closed or crashed?)")
            continue
        last_fresh = time.monotonic()

        if not s["in_replay"]:
            left_since = left_since or time.monotonic()
            if time.monotonic() - left_since >= 2:
                return "ended"
            continue
        left_since = None

        frame, total = s["frame"], s["num_frames"]
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
    return "timeout"
