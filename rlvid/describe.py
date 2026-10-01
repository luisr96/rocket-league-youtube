"""YouTube title, description and tags for a video, from its <video>.json data file.

Modelled on popular Rocket League POV channels: the player's name in capitals
and a hype phrase, the rank and mode in brackets, a short description with a
timestamp per game, and many tag variations of "<player> rocket league <mode>".
"""
import random
import re
from pathlib import Path

# Used when titles.txt is missing or has no usable lines.
DEFAULT_TITLES = ["{PLAYER} {RANK} {MODE} Gameplay | Rocket League"]  # neutral: fits any result

# Conditions a title line can start with, e.g. "[wins=2, goals>=4] ...": all are
# for the target player over both games.
CONDITION_VARS = {"wins", "losses", "goals", "assists", "saves", "shots", "points", "overtime", "fastest",
                  "comebacks"}
# Placeholders: text ones, and number ones (which need that number to be known).
NUMBER_PLACEHOLDERS = {"GOALS": "goals", "ASSISTS": "assists", "SAVES": "saves", "SHOTS": "shots",
                       "POINTS": "points", "WINS": "wins", "FASTEST": "fastest"}
PLACEHOLDERS = {"PLAYER", "RANK", "MODE"} | set(NUMBER_PLACEHOLDERS)
_COND = re.compile(r"^\s*(\w+)\s*(>=|<=|!=|=|>|<)\s*(\d+)\s*$")
_OPS = {"=": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def parse_title(line: str) -> tuple[list[tuple[str, str, int]], str]:
    """'[wins=2, goals>=4] {PLAYER} ...' -> ([("wins", "=", 2), ("goals", ">=", 4)], '{PLAYER} ...').

    Raises ValueError for an unknown condition or placeholder.
    """
    conds = []
    m = re.match(r"^\[([^\]]*)\]\s*(.*)$", line)
    if m:
        for part in m.group(1).split(","):
            c = _COND.match(part)
            if not c or c.group(1).lower() not in CONDITION_VARS:
                raise ValueError(f"unknown condition {part.strip()!r}")
            conds.append((c.group(1).lower(), c.group(2), int(c.group(3))))
        line = m.group(2)
    unknown = set(re.findall(r"\{(\w+)\}", line)) - PLACEHOLDERS
    if unknown:
        raise ValueError(f"unknown placeholder(s) {', '.join(sorted(unknown))}")
    if not line.strip():
        raise ValueError("no title after the conditions")
    return conds, line


def load_titles(path: Path) -> list[str]:
    """Title lines from titles.txt: blank lines and # comments ignored. A line with an
    unknown condition or placeholder is skipped (with a warning), so a typo never
    breaks an upload."""
    if not path.exists():
        return DEFAULT_TITLES
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parse_title(line)
        except ValueError as e:
            import logging
            logging.getLogger(__name__).warning("titles.txt: skipping %r (%s)", line, e)
            continue
        out.append(line)
    return out or DEFAULT_TITLES


def facts(data: dict) -> dict:
    """The numbers conditions and placeholders use, for the target over both games.
    None when unknown (e.g. stats of a video recorded before they were saved)."""
    games = data.get("games", [])

    def won(g):
        team = g["target"]["team"]
        other = "orange" if team == "blue" else "blue"
        return g["score"][team] > g["score"][other]

    stats = [g.get("stats") for g in games]
    have_stats = bool(games) and all(stats)
    total = lambda k: sum(s[k] for s in stats) if have_stats else None
    target_goals = [x for g in games for x in g.get("goals", []) if x.get("by") == "target"]
    ot = [g.get("overtime") for g in games]

    def comeback(g):
        """Won after being behind at some point (from the order of the goals)."""
        team = g["target"]["team"]
        us = them = 0
        behind = False
        for x in g.get("goals", []):
            us, them = (us + 1, them) if x.get("team") == team else (us, them + 1)
            behind = behind or them > us
        return won(g) and behind

    return {
        "wins": sum(1 for g in games if won(g)),
        "losses": sum(1 for g in games if not won(g)),
        "goals": total("goals") if have_stats else len(target_goals),
        "assists": total("assists"), "saves": total("saves"), "shots": total("shots"), "points": total("points"),
        "overtime": sum(1 for o in ot if o) if games and all(o is not None for o in ot) else None,
        "fastest": round(max((x.get("speed", 0) for x in target_goals), default=0)),
        "comebacks": sum(1 for g in games if comeback(g)),
    }


def eligible(line: str, f: dict) -> bool:
    """True if all the line's conditions hold, and every number it shows is known."""
    conds, pattern = parse_title(line)
    for var, op, num in conds:
        if f.get(var) is None or not _OPS[op](f[var], num):
            return False
    return all(f.get(var) is not None for ph, var in NUMBER_PLACEHOLDERS.items() if "{" + ph + "}" in pattern)


RANK_NAMES = {"supersonic-legend": ("SSL", "Supersonic Legend")}


def rank_names(rank_id: str) -> tuple[str, str]:
    """(short, long) rank names: "supersonic-legend" -> ("SSL", "Supersonic Legend")."""
    if not rank_id:
        return "", ""
    if rank_id in RANK_NAMES:
        return RANK_NAMES[rank_id]
    m = re.fullmatch(r"(grand-champion|champion|diamond|platinum|gold|silver|bronze)-(\d)", rank_id)
    long = rank_id.replace("-", " ").title()
    if m and m.group(1) == "grand-champion":
        return f"GC{m.group(2)}", long
    if m:
        return f"{m.group(1).title()} {m.group(2)}", long
    return long, long


def player_name(data: dict) -> str:
    """The target's name as the game shows it (first on their side in game 1)."""
    g1 = data["games"][0]
    side = g1["overlay"].get(g1["target"]["team"]) or []
    return side[0] if side else g1["target"]["name"]


def _clean(s: str) -> str:
    """Tidy a template after an empty field: double spaces, "( 2v2)"."""
    s = re.sub(r"\(\s+", "(", re.sub(r"\s+", " ", s))
    return s.replace("( ", "(").strip()


def fill(line: str, data: dict) -> str:
    """A title line (conditions dropped) with its {placeholders} filled in."""
    _, t = parse_title(line)
    short, _ = rank_names(data.get("rank", ""))
    f = facts(data)
    values = {"PLAYER": player_name(data).upper(), "RANK": short, "MODE": data.get("mode", "")}
    values.update({ph: str(f[var]) for ph, var in NUMBER_PLACEHOLDERS.items() if f.get(var) is not None})
    for key, value in values.items():
        t = t.replace("{" + key + "}", value)
    return _clean(t)[:100]


def title(data: dict, titles: list[str] | None = None, recent: list[str] = (), rng=random,
          special_weight: float = 3) -> str:
    """A random title among the lines whose conditions fit this video (lines without
    conditions always fit), avoiding titles used in recent uploads when possible.

    A fitting line with conditions counts special_weight times in the draw, so
    titles about what actually happened (a sweep, a comeback, 5 goals) come up
    more often than the general ones.
    """
    f = facts(data)
    lines = [ln for ln in (titles or DEFAULT_TITLES) if eligible(ln, f)] or DEFAULT_TITLES
    options = {}  # title -> weight (two lines can give the same title)
    for ln in lines:
        t = fill(ln, data)
        options[t] = max(options.get(t, 0), special_weight if parse_title(ln)[0] else 1)
    fresh = {t: w for t, w in options.items() if t not in set(recent)} or options
    return rng.choices(list(fresh), weights=list(fresh.values()))[0]


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _hashtag(name: str) -> str:
    return re.sub(r"[^0-9a-z]", "", name.lower())


def description(data: dict) -> str:
    p = player_name(data)
    short, long = rank_names(data.get("rank", ""))
    mode = data.get("mode", "")
    bracket = _clean(f"({short} {mode})")
    lines = [f"{p} Rocket League gameplay {bracket}. Two {mode} games back to back from "
             f"{p}'s point of view. Enjoy!", ""]
    for g in data["games"]:
        team = g["target"]["team"]
        other = "orange" if team == "blue" else "blue"
        us, them = " & ".join(g["overlay"][team]), " & ".join(g["overlay"][other])
        # Timestamps (clickable on YouTube); older data files have no start time for game 2.
        start = "0:00 " if g["game"] == 1 else (_clock(g["video_start"]) + " " if "video_start" in g else "")
        lines.append(f"{start}Game {g['game']}: {us} vs {them}")  # no score: no spoilers
    lines += ["", f"What did you think of {p}'s gameplay? Let us know in the comments, and which player "
                  f"you'd like to see next. Subscribe for more {short or 'high level'} content!", ""]
    tag = _hashtag(p)
    hashtags = ["#rocketleague", "#gameplay"] + ([f"#{tag}"] if tag else [])
    lines.append(" ".join(hashtags))
    return "\n".join(lines)[:5000]


def tags(data: dict) -> list[str]:
    p = player_name(data).lower()
    short, _ = rank_names(data.get("rank", ""))
    r, m = short.lower(), data.get("mode", "")
    candidates = [
        "rocket league", f"{p} rocket league", f"{p} rocket league gameplay", f"rocket league {p}",
        f"{p} {m}", f"{p} {r} {m}", f"{p} ranked {m}", f"{p} gameplay", f"rocket league {r} {m}",
        f"rocket league {m} gameplay", f"{r} {m}", "rocket league gameplay", "rocket league pro gameplay",
        f"{p} rocket league {m} replay",
    ]
    out, total = [], 0
    for t in candidates:
        t = re.sub(r"\s+", " ", t).strip()
        if t and t not in out and total + len(t) + 1 <= 480:  # YouTube allows 500 characters of tags
            out.append(t)
            total += len(t) + 1
    return out
