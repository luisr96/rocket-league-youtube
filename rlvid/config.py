"""Load config.toml, players.txt and the API key from .env."""
import ctypes
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


@dataclass
class Config:
    raw: dict
    api_key: str
    players: list[str] = field(default_factory=list)
    roster: list = field(default_factory=list)  # RosterEntry per players.txt line

    def path(self, key: str) -> Path:
        value = self.raw["paths"][key]
        if key == "demos_dir" and value == "auto":
            return documents_dir() / "My Games" / "Rocket League" / "TAGame" / "Demos"
        return project_path(value)

    @property
    def search(self) -> dict:
        return self.raw["search"]

    @property
    def api(self) -> dict:
        return self.raw["api"]


def project_path(value: str) -> Path:
    """A path from the config: environment variables expanded, relative paths under the
    project folder (not the current folder, which is System32 under Task Scheduler)."""
    p = Path(os.path.expandvars(value))
    return p if p.is_absolute() else ROOT / p


def documents_dir() -> Path:
    """The real Documents folder, which may be redirected (e.g. into OneDrive)."""
    buf = ctypes.create_unicode_buffer(260)
    # CSIDL_PERSONAL = 5 (Documents), SHGFP_TYPE_CURRENT = 0
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) != 0:
        raise ConfigError("could not look up the Documents folder; set demos_dir explicitly")
    return Path(buf.value)


@dataclass
class RosterEntry:
    name: str
    weight: float = 1.0
    steam_id: str | None = None


def parse_roster_line(line: str) -> RosterEntry | None:
    """'zen 5', 'atow 3 steam:76561198289610054', 'justin.' -> entry (weight 1 if none given).

    The name is everything before the optional weight and steam:ID, so names with
    spaces work too.
    """
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    tokens = line.split()
    steam_id = None
    weight = 1.0
    while len(tokens) > 1:
        t = tokens[-1]
        if t.lower().startswith("steam:") and t[6:].isdigit():
            steam_id = t[6:]
        elif re.fullmatch(r"\d+(\.\d+)?", t) and weight == 1.0:
            weight = float(t)
        else:
            break
        tokens.pop()
    return RosterEntry(" ".join(tokens), weight, steam_id)


def load_roster(path: Path) -> list[RosterEntry]:
    if not path.exists():
        raise ConfigError(f"players file not found: {path}")
    out: list[RosterEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        e = parse_roster_line(line)
        if e and e.name.lower() not in (x.name.lower() for x in out):
            out.append(e)
    if not out:
        raise ConfigError(f"no player names in {path}")
    return out


def load_players(path: Path) -> list[str]:
    return [e.name for e in load_roster(path)]


def load_config() -> Config:
    cfg_path = ROOT / "config.toml"
    if not cfg_path.exists():
        raise ConfigError(f"config not found: {cfg_path}")
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    load_dotenv(ROOT / ".env")
    key = os.getenv("BALLCHASING_API_KEY", "").strip()
    if not key or key == "paste-your-key-here":
        raise ConfigError("BALLCHASING_API_KEY missing: copy .env.example to .env and set it")
    cfg = Config(raw=raw, api_key=key)
    cfg.roster = load_roster(cfg.path("players_file"))
    cfg.players = [e.name for e in cfg.roster]
    return cfg
