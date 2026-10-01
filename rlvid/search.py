"""Find unseen replays for the players in players.txt."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    featured: list[str]  # listed players in this match (in-game names), in players.txt order
    listed: list[str]    # the same players as written in players.txt
    focus_ids: dict[str, str]  # in-game camera focus id per player name, when known
    rank: str = ""             # ballchasing rank id, e.g. "supersonic-legend" ("" if unknown)
    season: int | None = None

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
    def camera_listed(self) -> str:
        """The camera player's name as written in players.txt (used for grouping and file names)."""
        return self.listed[0]

    @property
    def mode(self) -> str:
        """1v1 / 2v2 / 3v3 from the playlist, falling back to the team size."""
        return MODES.get(self.playlist) or f"{max(len(self.blue_players), len(self.orange_players))}v"             f"{max(len(self.blue_players), len(self.orange_players))}"

    @property
    def teammates(self) -> list[str]:
        """The camera player's teammates (in-game names), without the camera player."""
        team = self.blue_players if self.camera_player in self.blue_players else self.orange_players
        return [p for p in team if p != self.camera_player]

    @property
    def opponents(self) -> list[str]:
        return self.orange_players if self.camera_player in self.blue_players else self.blue_players

    @property
    def camera_focus_id(self) -> str | None:
        return self.focus_ids.get(self.camera_player)

    @property
    def length(self) -> str:
        return f"{self.duration // 60}:{self.duration % 60:02d}"


MODES = {"ranked-duels": "1v1", "ranked-doubles": "2v2", "ranked-standard": "3v3",
         "unranked-duels": "1v1", "unranked-doubles": "2v2", "unranked-standard": "3v3"}

# ballchasing platform -> platform name in the game's focus string
# ("Player_Epic|<id>|0"). Epic and Steam are verified in-game; the others are
# best guesses, and the plugin falls back to the player name if they fail.
PLATFORMS = {"epic": "Epic", "steam": "Steam", "ps4": "PS4", "xbox": "XboxOne", "switch": "Switch"}


def _focus_ids(*teams: dict) -> dict[str, str]:
    out = {}
    for t in teams:
        for p in t.get("players", []):
            pid = p.get("id") or {}
            plat = PLATFORMS.get(pid.get("platform", ""))
            if plat and pid.get("id"):
                out[p.get("name", "?")] = f"Player_{plat}|{pid['id']}|0"
    return out


def _team(t: dict) -> tuple[list[str], int]:
    return [p.get("name", "?") for p in t.get("players", [])], int(t.get("goals") or 0)


def _featured(players: list[str], wanted: list[str]) -> list[tuple[str, str]]:
    """(in-game name, players.txt name) of listed players, ordered by players.txt priority."""
    out: list[tuple[str, str]] = []
    for w in wanted:
        wl = w.lower()
        # Exact (case-insensitive) only: ballchasing's name search is a substring
        # match, which also returns names like "I Destroy ZEN".
        hit = next((p for p in players if p.lower() == wl), None)
        if hit and hit not in (h for h, _ in out):
            out.append((hit, w))
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
        blue_goals=bg, orange_goals=og,
        featured=[h for h, _ in featured], listed=[w for _, w in featured],
        focus_ids=_focus_ids(r.get("blue", {}), r.get("orange", {})),
        rank=(r.get("max_rank") or r.get("min_rank") or {}).get("id", ""),
        season=r.get("season"),
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


@dataclass
class Pair:
    """Two games of the same camera player in the same playlist, played close together."""
    first: Match   # the earlier game
    second: Match

    @property
    def matches(self) -> list[Match]:
        return [self.first, self.second]

    @property
    def player(self) -> str:
        return self.first.camera_listed

    @property
    def mode(self) -> str:
        return self.first.mode

    @property
    def gap(self) -> timedelta:
        return self.second.date - self.first.date

    @property
    def teammates(self) -> list[list[str]]:
        """Teammates per game (always two groups), alphabetical within each game."""
        return [sorted(m.teammates, key=str.lower) for m in self.matches]

    @property
    def opponents(self) -> list[list[str]]:
        """Opponents per game (always two groups), alphabetical within each game."""
        return [sorted(m.opponents, key=str.lower) for m in self.matches]


def find_pairs(matches: list[Match], max_gap_days: float) -> list[Pair]:
    """Non-overlapping pairs of consecutive games, newest first.

    Games are grouped by camera player (players.txt name) and playlist, sorted by
    date, and paired from the newest game backwards: each game is paired with the
    game right before it if that one is within max_gap_days.
    """
    groups: dict[tuple[str, str], list[Match]] = {}
    for m in matches:
        groups.setdefault((m.camera_listed.lower(), m.playlist), []).append(m)

    pairs: list[Pair] = []
    max_gap = timedelta(days=max_gap_days)
    for games in groups.values():
        games.sort(key=lambda m: m.date, reverse=True)
        i = 0
        while i + 1 < len(games):
            later, earlier = games[i], games[i + 1]
            if later.date - earlier.date <= max_gap:
                pairs.append(Pair(first=earlier, second=later))
                i += 2
            else:
                i += 1
    return sorted(pairs, key=lambda p: p.second.date, reverse=True)
