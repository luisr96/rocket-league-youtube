"""YouTube title, description and tags for a video, from its <video>.json data file.

Modelled on popular Rocket League POV channels: the player's name in capitals
and a hype phrase, the rank and mode in brackets, a short description with a
timestamp per game, and many tag variations of "<player> rocket league <mode>".
"""
import re
from pathlib import Path

# Used when titles.txt is missing or has no usable lines.
DEFAULT_TITLES = ["{PLAYER} is INSANE at Rocket League! ({RANK} {MODE})"]
PLACEHOLDERS = {"PLAYER", "RANK", "MODE"}


def load_titles(path: Path) -> list[str]:
    """Title patterns from titles.txt: one per line; blank lines and # comments ignored.

    A line using an unknown {placeholder} is skipped (with a warning), so a typo
    never breaks an upload.
    """
    if not path.exists():
        return DEFAULT_TITLES
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        unknown = set(re.findall(r"\{(\w+)\}", line)) - PLACEHOLDERS
        if unknown:
            import logging
            logging.getLogger(__name__).warning("titles.txt: skipping %r (unknown %s)", line, unknown)
            continue
        out.append(line)
    return out or DEFAULT_TITLES

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


def title(data: dict, index: int = 0, titles: list[str] | None = None) -> str:
    titles = titles or DEFAULT_TITLES
    short, _ = rank_names(data.get("rank", ""))
    t = titles[index % len(titles)]
    for key, value in (("PLAYER", player_name(data).upper()), ("RANK", short), ("MODE", data.get("mode", ""))):
        t = t.replace("{" + key + "}", value)
    return _clean(t)[:100]


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
