"""Find unseen replays for the players in players.txt."""
import logging
from dataclasses import dataclass
from datetime import datetime

from .api import Ballchasing

log = logging.getLogger(__name__)


@dataclass
class Match:
    id: str
    date: datetime
    playlist: str
    map: str
    duration: int
    blue_players: list[str]
    orange_players: list[str]
    blue_goals: int
    orange_goals: int
    featured: list[str]  # listed players in this match, in players.txt order

    @property
    def score(self) -> str:
        return f"{self.blue_goals}-{self.orange_goals}"

    @property
    def fingerprint(self) -> tuple:
        """Same game uploaded twice gets different ids but identical date/players/score."""
        return (self.date, tuple(sorted(p.lower() for p in self.blue_players + self.orange_players)), self.score)

    @property
    def camera_player(self) -> str:
        return self.featured[0]

    @property
    def length(self) -> str:
        return f"{self.duration // 60}:{self.duration % 60:02d}"


def _team(t: dict) -> tuple[list[str], int]:
    return [p.get("name", "?") for p in t.get("players", [])], int(t.get("goals") or 0)


def _featured(players: list[str], wanted: list[str]) -> list[str]:
    """In-game names of listed players, ordered by players.txt priority."""
    out: list[str] = []
    for w in wanted:
        wl = w.lower()
        # Exact (case-insensitive) only: ballchasing's name search is a substring
        # match, which also returns names like "I Destroy ZEN".
        hit = next((p for p in players if p.lower() == wl), None)
        if hit and hit not in out:
            out.append(hit)
    return out


def to_match(r: dict, wanted: list[str]) -> Match | None:
    blue, bg = _team(r.get("blue", {}))
    orange, og = _team(r.get("orange", {}))
    featured = _featured(blue + orange, wanted)
    if not featured:
        return None
    return Match(
        id=r["id"],
        date=datetime.fromisoformat(r["date"]),
        playlist=r.get("playlist_id") or r.get("playlist_name", "?"),
        map=r.get("map_name") or r.get("map_code", "?"),
        duration=int(r.get("duration") or 0),
        blue_players=blue, orange_players=orange,
        blue_goals=bg, orange_goals=og, featured=featured,
    )


def find_unseen(api: Ballchasing, cfg, seen: set[str]) -> list[Match]:
    s = cfg.search
    base = {
        "playlist": s["playlists"],
        "min-rank": s["min_rank"],
        "max-rank": s["max_rank"],
        "sort-by": s["sort_by"],
        "sort-dir": "desc",
        "count": s["count_per_player"],
    }
    if s.get("pro"):
        base["pro"] = "true"
    # One query per player: repeating player-name may require *all* names to be
    # in the same replay, so query each separately and merge.
    found: dict[str, Match] = {}
    fingerprints: set[tuple] = set()
    for name in cfg.players:
        replays = api.list_replays(**base, **{"player-name": name})
        log.info("player %s: %d replays", name, len(replays))
        for r in replays:
            if r["id"] in seen or r["id"] in found:
                continue
            m = to_match(r, cfg.players)
            if m and m.fingerprint not in fingerprints:
                found[m.id] = m
                fingerprints.add(m.fingerprint)
    return sorted(found.values(), key=lambda m: m.date, reverse=True)
