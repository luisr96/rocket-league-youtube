"""Local web server for the broadcast overlay (overlay/hud.html) while recording.

OBS's Browser Source loads http://127.0.0.1:<port>/hud.html, which polls
/state ~30 times a second. /state is the plugin's live data
(bakkesmod/data/rlvid_hud.json: scores, clock, players with boost and stats)
plus what only the recorder knows: which player is the POV, the label under
the scoreboard ("SSL 2v2") and the intro to play at the start of each game.
"""
import json
import logging
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bakkes import STATUS_FILE

log = logging.getLogger(__name__)

OVERLAY_DIR = Path(__file__).resolve().parent.parent / "overlay"
HUD_DATA = STATUS_FILE.with_name("rlvid_hud.json")


class HudServer:
    def __init__(self, port: int, data_file: Path = HUD_DATA):
        self.port = port
        self.data_file = data_file
        self.game: dict = {}   # pov_id, pov_name, label
        self.intro: dict | None = None
        self._intro_time = 0.0
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._last_data = {"in_replay": False}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hud.html"

    def start(self) -> None:
        server = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(OVERLAY_DIR), **kw)

            def do_GET(self):
                if self.path.split("?")[0] == "/state":
                    body = json.dumps(server.state(), ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

            def end_headers(self):
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def log_message(self, *a):  # 30 requests a second: keep the log quiet
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        log.info("HUD server at %s", self.url)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def set_game(self, pov_id: str | None, pov_name: str, label: str, pov_label: str = "") -> None:
        """pov_label, if given, is the name shown for the POV player instead of the in-game one."""
        with self._lock:
            self.game = {"pov_id": pov_id, "pov_name": pov_name, "label": label, "pov_label": pov_label}

    def show_intro(self, match: int, pov: str, blue: list[str], orange: list[str]) -> None:
        with self._lock:
            self._intro_time = time.time()
            self.intro = {"id": f"{match}-{self._intro_time:.3f}", "match": match, "pov": pov,
                          "blue": blue, "orange": orange}

    def state(self) -> dict:
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
            self._last_data = data
        except (OSError, ValueError):
            # The plugin replaces the file ~30 times a second, so a read now and then
            # lands mid-replace: use the last good data rather than "not in a replay"
            # (which hid the overlay for a frame: a flicker).
            data = self._last_data
        if time.time() - data.get("time", 0) > 3:
            data["in_replay"] = False  # plugin stopped writing (game closed)
        with self._lock:
            game, intro, intro_time = dict(self.game), self.intro, self._intro_time
        return build_state(data, game, intro, intro_time)


def build_state(data: dict, game: dict, intro: dict | None, intro_time: float, now: float | None = None) -> dict:
    """Plugin data + POV, stats and label + intro, in the shape hud.html's render() expects."""
    now = time.time() if now is None else now
    players = data.get("players")
    out = {"in_replay": bool(data.get("in_replay")), "label": game.get("label", "")}
    if players:
        pov = None
        for p in players:
            p["pov"] = bool(game.get("pov_id") and p.get("id") == game["pov_id"]) or (
                not game.get("pov_id") and p.get("name", "").lower() == game.get("pov_name", "").lower())
            pov = pov or (p if p["pov"] else None)
        label = game.get("pov_label", "")
        if pov and label and pov.get("name", "").lower() != label.lower():
            pov["name"] = label
        out.update(score=data.get("score", {"blue": 0, "orange": 0}), clock=data.get("clock", 0),
                   overtime=bool(data.get("overtime")), players=players)
        if pov:
            out["stats"] = {k: pov.get(k, 0) for k in ("score", "goals", "assists", "saves", "shots")}
            out["stats"]["name"] = pov.get("name", "")
    if intro:
        out["intro"] = dict(intro, fresh=now - intro_time < 2)
    return out
