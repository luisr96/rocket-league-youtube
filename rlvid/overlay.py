"""URL of the team-names overlay (overlay/overlay.html) for one game."""
from pathlib import Path
from urllib.parse import urlencode

from .search import Match

PAGE = Path(__file__).resolve().parent.parent / "overlay" / "overlay.html"


def sides(m: Match) -> tuple[list[str], list[str]]:
    """(blue, orange) names, with the camera player first on their side."""
    def order(team: list[str]) -> list[str]:
        return [m.camera_player] + [p for p in team if p != m.camera_player] if m.camera_player in team else team
    return order(m.blue_players), order(m.orange_players)


def url(settings: dict, m: Match | None = None) -> str:
    """Overlay URL showing the names of m, or an empty overlay if m is None."""
    q: list[tuple[str, str]] = [("style", settings.get("style", "band")),
                                ("hold", str(settings.get("hold_seconds", 4))),
                                ("fade", str(settings.get("fade_out_seconds", 2))),
                                ("fadein", str(settings.get("fade_in_seconds", 0)))]
    if m:
        blue, orange = sides(m)
        q += [("blue", n) for n in blue] + [("orange", n) for n in orange]
    return PAGE.as_uri() + "?" + urlencode(q)
