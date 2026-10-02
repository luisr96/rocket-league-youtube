"""URL of the team-names overlay (overlay/overlay.html) for one game."""
from pathlib import Path
from urllib.parse import urlencode

from .metadata import target_team
from .search import Match

PAGE = Path(__file__).resolve().parent.parent / "overlay" / "overlay.html"


def sides(m: Match, players: list[dict] | None = None, target: str = "") -> tuple[list[str], list[str]]:
    """(blue, orange) names, with the camera player first on their side.

    players is the plugin's list of {"name", "team", "id"} as the game shows
    them (name tags); those names are used when the team sizes match the match
    data, otherwise the ballchasing names are.

    target, if given, is shown for the camera player (first on their side)
    unless it's the same name in another case (a player can use another name
    in a game, e.g. dralii as "gg").
    """
    blue, orange = _sides(m, players)
    if target:
        team = blue if target_team(m) == "blue" else orange
        if team and team[0].lower() != target.lower():
            team[0] = target
    return blue, orange


def _sides(m: Match, players: list[dict] | None) -> tuple[list[str], list[str]]:
    if players:
        blue = [p for p in players if p.get("team") == 0]
        orange = [p for p in players if p.get("team") == 1]
        if len(blue) == len(m.blue_players) and len(orange) == len(m.orange_players):
            def is_camera(p: dict) -> bool:
                if m.camera_focus_id:
                    return p.get("id") == m.camera_focus_id
                return p.get("name", "").lower() == m.camera_player.lower()

            def order_game(team: list[dict]) -> list[str]:
                return [p["name"] for p in sorted(team, key=lambda p: not is_camera(p))]
            return order_game(blue), order_game(orange)

    def order(team: list[str]) -> list[str]:
        return [m.camera_player] + [p for p in team if p != m.camera_player] if m.camera_player in team else team
    return order(m.blue_players), order(m.orange_players)


def url(settings: dict, m: Match | None = None, players: list[dict] | None = None, target: str = "") -> str:
    """Overlay URL showing the names of m (in-game names from players when usable),
    or an empty overlay if m is None."""
    q: list[tuple[str, str]] = [("style", settings.get("style", "band")),
                                ("hold", str(settings.get("hold_seconds", 4))),
                                ("fade", str(settings.get("fade_out_seconds", 2))),
                                ("fadein", str(settings.get("fade_in_seconds", 0)))]
    if m:
        blue, orange = sides(m, players, target)
        q += [("blue", n) for n in blue] + [("orange", n) for n in orange]
    return PAGE.as_uri() + "?" + urlencode(q)
