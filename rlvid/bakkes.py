"""Talk to BakkesMod: rcon websocket for commands, status file from the RLVid plugin."""
import json
import logging
import os
import re
from pathlib import Path

import websocket

log = logging.getLogger(__name__)

BAKKES_DIR = Path(os.path.expandvars(r"%APPDATA%\bakkesmod\bakkesmod"))
STATUS_FILE = BAKKES_DIR / "data" / "rlvid_status.json"


class BakkesError(Exception):
    pass


def rcon_settings() -> tuple[int, str]:
    cfg = BAKKES_DIR / "cfg" / "config.cfg"
    if not cfg.exists():
        raise BakkesError(f"BakkesMod config not found at {cfg}; has BakkesMod run once?")
    text = cfg.read_text(encoding="utf-8", errors="replace")
    port = re.search(r'^rcon_port "(\d+)"', text, re.M)
    pw = re.search(r'^rcon_password "([^"]*)"', text, re.M)
    if not pw:
        raise BakkesError("rcon_password not found in BakkesMod config.cfg")
    return int(port[1]) if port else 9002, pw[1]


class Rcon:
    def __init__(self):
        self.ws = None

    def connect(self, timeout: float = 5) -> None:
        port, pw = rcon_settings()
        try:
            self.ws = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=timeout)
        except OSError as e:
            raise BakkesError(f"cannot reach BakkesMod rcon on port {port} (is the game running with BakkesMod injected?): {e}")
        self.ws.send(f"rcon_password {pw}")
        if self.ws.recv() != "authyes":
            raise BakkesError("BakkesMod rcon rejected the password")

    def send(self, command: str) -> None:
        if self.ws is None:
            self.connect()
        log.info("rcon> %s", command)
        self.ws.send(command)

    def close(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws = None


def read_status() -> dict | None:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
