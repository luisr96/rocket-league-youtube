"""The <video>.json file saved next to each video: names, scores and goal times per game."""
import json
from datetime import datetime
from pathlib import Path

from .search import Match, Pair


def team_name(n: int) -> str:
    return "blue" if n == 0 else "orange"


def target_team(m: Match) -> str:
    return "blue" if m.camera_player in m.blue_players else "orange"


def goals(m: Match, plugin_goals: list[dict], video_start: float, release_wall: float) -> list[dict]:
    """Goals from the plugin, with their time in the video.

    video_start is the recording length and release_wall the clock time
    (time.time()) when the game was released. Each goal carries the clock time
    the plugin saw it; the video runs in real time, so the difference is the
    time since release. (The replay's own elapsed time does not advance
    steadily, so it is not used.)
    """
    out = []
    for g in plugin_goals:
        if g.get("wall", 0) < release_wall:
            continue
        team = team_name(g.get("team", 0))
        if g.get("scorer_id") and g["scorer_id"] == m.camera_focus_id:
            by = "target"
        elif not g.get("scorer_id") and not g.get("scorer"):
            by = "unknown"
        elif g.get("scorer", "").lower() == m.camera_player.lower() and not m.camera_focus_id:
            by = "target"
        else:
            by = "teammate" if team == target_team(m) else "opponent"
        out.append({
            "video_time": round(video_start + g["wall"] - release_wall, 3),
            "replay_time": round(g["elapsed"], 3),
            "frame": g.get("frame", -1),
            "team": team,
            "scorer": g.get("scorer", ""),
            "scorer_id": g.get("scorer_id", ""),
            "by": by,
            "speed": round(g.get("speed", 0), 1),
        })
    return out


STAT_KEYS = ("score", "goals", "assists", "saves", "shots")


def end_stats(m: Match, hud: dict | None) -> tuple[dict | None, bool | None]:
    """The target's end-of-game stats and whether the game went to overtime, from the
    plugin's live HUD data read as the game ended. (None, None) if not available.

    Stats: points (the in-game score), goals, assists, saves, shots.
    """
    if not hud or not hud.get("in_replay") or not hud.get("players"):
        return None, None
    me = next((p for p in hud["players"] if m.camera_focus_id and p.get("id") == m.camera_focus_id), None) or         next((p for p in hud["players"] if p.get("name", "").lower() == m.camera_player.lower()), None)
    stats = {("points" if k == "score" else k): int(me.get(k, 0)) for k in STAT_KEYS} if me else None
    return stats, bool(hud.get("overtime"))


def game(i: int, m: Match, overlay_sides: tuple[list[str], list[str]], game_goals: list[dict],
         video_start: float = 0, stats: dict | None = None, overtime: bool | None = None) -> dict:
    return {
        "game": i,
        "video_start": round(video_start, 3),  # where the game starts in the video (seconds)
        "replay_id": m.id,
        "date": m.date.isoformat(),
        "map": m.map,
        "score": {"blue": m.blue_goals, "orange": m.orange_goals},
        "duration": m.duration,
        "rank": m.rank,
        "season": m.season,
        "target": {"name": m.camera_player, "id": m.camera_focus_id, "team": target_team(m)},
        "overlay": {"blue": overlay_sides[0], "orange": overlay_sides[1]},
        "ballchasing": {"blue": m.blue_players, "orange": m.orange_players},
        "goals": game_goals,
        "stats": stats,          # target's points/goals/assists/saves/shots at the end (None if unknown)
        "overtime": overtime,    # None if unknown
    }


def write(video: Path, pair: Pair, games: list[dict], search: dict | None = None) -> Path:
    path = video.with_suffix(".json")
    search = search or {}
    # The replay's own rank when ballchasing has it, else the search's rank if it is a single one.
    rank = pair.first.rank or pair.second.rank or (
        search.get("min_rank", "") if search.get("min_rank") == search.get("max_rank") else "")
    data = {
        "video": video.name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "player": pair.player,
        "mode": pair.mode,
        "rank": rank,
        "season": pair.first.season,
        "pro": bool(search.get("pro")),
        "games": games,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
