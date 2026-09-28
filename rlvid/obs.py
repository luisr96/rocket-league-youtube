"""Control OBS recording over its built-in WebSocket server (OBS 28+, protocol v5)."""
import base64
import logging
import os
import struct
import subprocess
import time
from pathlib import Path

import obsws_python as obs

log = logging.getLogger(__name__)
# obsws-python logs connection parameters (including the password) at INFO and
# full tracebacks for errors we handle ourselves; keep only its critical messages.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)


class ObsError(Exception):
    pass


def _obs_processes() -> int:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"], capture_output=True, text=True).stdout
    return out.lower().count("obs64.exe")


def ensure_running(exe: str) -> None:
    """Start OBS if it isn't running; refuse to continue if several copies are running."""
    n = _obs_processes()
    if n > 1:
        raise ObsError(f"{n} copies of OBS are running; close all but one (check the system tray). "
                       f"Two copies fight over the game capture and record black video.")
    if n == 1:
        return
    path = Path(exe)
    if not path.exists():
        raise ObsError(f"OBS not found at {path}; set [obs] exe in config.toml")
    log.info("starting OBS")
    print("Starting OBS ...")
    # A normal window (not the tray) so it is visible that OBS is running. OBS
    # starts before the game, so it can't take focus away from it. OBS must be
    # started from its own folder or it fails to find its data files.
    # --disable-shutdown-check: after a crash, start normally instead of asking
    # whether to use Safe Mode (a dialog nobody is there to answer).
    subprocess.Popen([str(path), "--disable-updater", "--disable-shutdown-check"], cwd=path.parent)


def connect(host: str, port: int, exe: str, timeout: float = 60) -> "Recorder":
    """Start OBS if needed and return a connected Recorder, waiting for its WebSocket server."""
    ensure_running(exe)
    deadline = time.monotonic() + timeout
    while True:
        try:
            return Recorder(host, port)
        except ObsError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


class Recorder:
    def __init__(self, host: str, port: int, timeout: float = 5):
        password = os.getenv("OBS_WEBSOCKET_PASSWORD", "")
        try:
            self.client = obs.ReqClient(host=host, port=port, password=password, timeout=timeout)
            # Right after launch OBS accepts connections but answers 207 "not ready".
            v = self.client.get_version()
        except Exception as e:  # connection refused, auth failure, not ready, ...
            raise ObsError(f"cannot connect to OBS WebSocket at {host}:{port} "
                           f"(is OBS running with the WebSocket server enabled, and "
                           f"OBS_WEBSOCKET_PASSWORD set in .env?): {e}") from e
        log.info("connected to OBS %s (websocket %s)", v.obs_version, v.obs_web_socket_version)

    def wait_for_capture(self, source: str, timeout: float = 45) -> None:
        """Wait until the capture source shows a picture (not black).

        After the game starts, OBS's Game Capture needs a few seconds (it retries
        every ~4 s) to hook the game; recording before that gives black video.
        """
        deadline = time.monotonic() + timeout
        level = 0.0
        while time.monotonic() < deadline:
            try:
                level = _brightness(self.client.get_source_screenshot(source, "bmp", 64, 36, -1).image_data)
            except Exception as e:
                raise ObsError(f"cannot take a screenshot of OBS source {source!r} "
                               f"(set [obs] capture_source in config.toml): {e}") from e
            if level > 8:
                log.info("OBS capture of %r is live (brightness %.0f)", source, level)
                return
            time.sleep(1)
        raise ObsError(f"OBS source {source!r} stayed black for {timeout:.0f}s; "
                       f"is Game Capture hooking Rocket League?")

    def setup_overlay(self, source: str, url: str) -> None:
        """Put the Browser Source `source` on top of the current scene, showing `url` (no names).

        The source is created the first time. The overlay page is 1920x1080; it is
        scaled to the OBS canvas if that is a different size.
        """
        try:
            scene = self.client.get_current_program_scene().current_program_scene_name
            if source not in [i["inputName"] for i in self.client.get_input_list().inputs]:
                self.client.create_input(scene, source, "browser_source",
                                         {"url": url, "width": 1920, "height": 1080}, True)
                log.info("created OBS browser source %r in scene %r", source, scene)
            else:
                self.client.set_input_settings(source, {"url": url, "width": 1920, "height": 1080}, True)
                if source not in [i["sourceName"] for i in self.client.get_scene_item_list(scene).scene_items]:
                    self.client.create_scene_item(scene, source, True)
            item = self.client.get_scene_item_id(scene, source).scene_item_id
            count = len(self.client.get_scene_item_list(scene).scene_items)
            self.client.set_scene_item_index(scene, item, count - 1)  # on top of the game
            self.client.set_scene_item_enabled(scene, item, True)
            base = self.client.get_video_settings().base_width
            self.client.set_scene_item_transform(scene, item, {"positionX": 0, "positionY": 0,
                                                               "scaleX": base / 1920, "scaleY": base / 1920})
        except Exception as e:
            raise ObsError(f"cannot set up the names overlay {source!r}: {e}") from e

    def show_overlay(self, source: str, url: str) -> None:
        """Load `url` into the overlay; the page shows the names and fades them out by itself."""
        self.client.set_input_settings(source, {"url": url}, True)

    def fade_out(self, black_scene: str, ms: int) -> None:
        """Fade the program output to an empty (black) scene, remembering the current scene."""
        try:
            self.scene = self.client.get_current_program_scene().current_program_scene_name
            if black_scene not in [s["sceneName"] for s in self.client.get_scene_list().scenes]:
                self.client.create_scene(black_scene)
                self.client.set_current_program_scene(self.scene)  # creating a scene may switch to it
            self.client.set_current_scene_transition("Fade")
            self.client.set_current_scene_transition_duration(ms)
            self.client.set_current_program_scene(black_scene)
        except Exception as e:
            raise ObsError(f"cannot fade to scene {black_scene!r} (is Studio Mode off?): {e}") from e
        time.sleep(ms / 1000 + 0.3)
        log.info("faded out to %r", black_scene)

    def restore_scene(self) -> None:
        """Switch straight back to the scene from before fade_out (e.g. after recording stopped)."""
        try:
            self.client.set_current_scene_transition("Cut")
            self.client.set_current_program_scene(self.scene)
        except Exception as e:
            log.warning("could not switch OBS back to scene %r: %s", self.scene, e)

    def fade_in(self, ms: int) -> None:
        """Fade back to the scene that was showing before fade_out."""
        self.client.set_current_program_scene(self.scene)
        time.sleep(ms / 1000 + 0.1)
        log.info("faded in to %r", self.scene)

    def _status(self):
        return self.client.get_record_status()

    def start(self) -> None:
        if self._status().output_active:
            raise ObsError("OBS is already recording; stop that recording first")
        self.client.start_record()
        self._wait(lambda s: s.output_active, "recording to start")
        log.info("recording started")

    def pause(self) -> None:
        self.client.pause_record()
        self._wait(lambda s: s.output_paused, "recording to pause")
        log.info("recording paused")

    def resume(self) -> None:
        self.client.resume_record()
        self._wait(lambda s: not s.output_paused, "recording to resume")
        log.info("recording resumed")

    def stop(self) -> Path:
        """Stop recording and return the path of the file OBS wrote, once OBS has finished it."""
        resp = self.client.stop_record()
        path = Path(resp.output_path)
        # StopRecord returns before OBS has finalized the file (Hybrid MP4 rewrites
        # its index at the end), and Windows lets us open it while OBS still
        # writes, so wait for the output to be inactive and the size to settle.
        self._wait(lambda s: not s.output_active, "recording to stop", timeout=60)
        deadline = time.monotonic() + 60
        last_size, stable_since = -1, None
        while time.monotonic() < deadline:
            size = path.stat().st_size if path.exists() else -1
            if size > 0 and size == last_size:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 2:
                    log.info("recording stopped: %s (%d MB)", path, size // 2**20)
                    return path
            else:
                stable_since = None
            last_size = size
            time.sleep(0.5)
        raise ObsError(f"OBS did not finish writing {path}")

    def abort(self) -> None:
        """Stop a recording after a failure; the partial file is left for inspection."""
        try:
            if getattr(self, "scene", None):
                self.client.set_current_program_scene(self.scene)  # don't leave OBS on the black scene
        except Exception as e:
            log.warning("could not restore OBS scene: %s", e)
        try:
            if self._status().output_active:
                path = self.client.stop_record().output_path
                log.warning("recording aborted, partial file kept at %s", path)
        except Exception as e:
            log.warning("could not stop OBS recording: %s", e)

    def _wait(self, pred, what: str, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred(self._status()):
                return
            time.sleep(0.2)
        raise ObsError(f"timed out waiting for {what}")


def _brightness(data_url: str) -> float:
    """Average pixel value (0-255) of an uncompressed 24/32-bit BMP data URL."""
    raw = base64.b64decode(data_url.split(",", 1)[1])
    offset = struct.unpack_from("<I", raw, 10)[0]
    bpp = struct.unpack_from("<H", raw, 28)[0] // 8
    pixels = raw[offset:]
    rgb = [b for i, b in enumerate(pixels) if i % bpp < 3]  # skip alpha
    return sum(rgb) / max(len(rgb), 1)
