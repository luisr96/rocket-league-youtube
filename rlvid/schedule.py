"""Which player gets the next video.

Each player in players.txt has a weight (how often you want to see them). A
player's priority is weight x days since their last video (a player who never
had one counts as never_days overdue), so over time players get videos in
proportion to their weights. Players in their cooldown (a video in the last
cooldown_days days) are skipped while anyone else has games. The pick is random,
weighted by priority: the most overdue player is the most likely, not certain.
"""
import random
from dataclasses import dataclass
from datetime import date

from .search import Pair, Player


@dataclass
class Candidate:
    player: str
    weight: float
    last_video: date | None
    days: float          # days since the last video (never_days if none)
    cooldown: bool
    priority: float
    pair: Pair           # the player's newest unrecorded pair


def last_videos(history_entries: list[dict]) -> dict[str, date]:
    """Player (lower-case) -> date of their latest video, from history."""
    out: dict[str, date] = {}
    for e in history_entries:
        if e.get("player") and e.get("processed_date"):
            d = date.fromisoformat(e["processed_date"])
            k = e["player"].lower()
            out[k] = max(out.get(k, d), d)
    return out


def candidates(players: list[Player], pairs: list[Pair], history_entries: list[dict], today: date,
               never_days: float = 14, cooldown_days: int = 1) -> list[Candidate]:
    """One candidate per player with at least one unrecorded pair, highest priority first."""
    weights = {p.name.lower(): p.weight for p in players}
    last = last_videos(history_entries)
    newest: dict[str, Pair] = {}
    for pair in sorted(pairs, key=lambda p: p.second.date, reverse=True):
        newest.setdefault(pair.player.lower(), pair)
    out = []
    for key, pair in newest.items():
        weight = weights.get(key, 1.0)
        lv = last.get(key)
        days = float((today - lv).days) if lv else float(never_days)
        cooldown = lv is not None and (today - lv).days <= cooldown_days
        out.append(Candidate(pair.player, weight, lv, days, cooldown, weight * days, pair))
    return sorted(out, key=lambda c: (c.cooldown, -c.priority, -c.pair.second.date.timestamp()))


def choose(cands: list[Candidate], rng=random) -> Candidate | None:
    """A random candidate weighted by priority, skipping those in cooldown if anyone else
    is available. None if there are no candidates."""
    if not cands:
        return None
    pool = [c for c in cands if not c.cooldown and c.priority > 0] or [c for c in cands if c.priority > 0]
    if not pool:  # everyone had a video today: just the highest weight
        return max(cands, key=lambda c: c.weight)
    return rng.choices(pool, weights=[c.priority for c in pool])[0]
