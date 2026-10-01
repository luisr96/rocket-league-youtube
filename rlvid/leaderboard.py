"""Top players from the rlstats.net skill leaderboards (Steam).

https://rlstats.net/leaderboards/skills has one table per platform and playlist
(e.g. Steam ranked Doubles); each row has the rank, the player's name, a profile
link with their Steam ID, and their rating. The page is downloaded at most once
per refresh_hours and saved to leaderboard.json; if a download fails (offline,
page changed), the last saved list is used, and with none, only the hand-picked
players from players.txt. The site's robots.txt allows this; the script says who
it is in its User-Agent and downloads at most once a day.
"""
import html as htmllib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DEFAULT_URL = "https://rlstats.net/leaderboards/skills"
HEADERS = {"User-Agent": "RLUploader/1.0 (personal YouTube uploader; +https://luisr96.github.io/rl-uploader/)"}
PLAYLISTS = {"duel": 10, "doubles": 11, "standard": 13}  # rlstats "data-skill" ids (ranked)
MIN_ENTRIES = 20  # fewer than this means the page did not have the leaderboard


class LeaderboardError(Exception):
    pass


@dataclass
class Entry:
    rank: int
    name: str
    steam_id: str
    country: str | None = None
    mmr: int | None = None


def parse(page: str, playlist: str = "doubles") -> list[Entry]:
    """The Steam table for the playlist, in rank order."""
    skill = PLAYLISTS.get(playlist)
    if skill is None:
        raise LeaderboardError(f"unknown playlist {playlist!r} (use {', '.join(PLAYLISTS)})")
    table = BeautifulSoup(page, "html.parser").find("table", attrs={"data-platform": "Steam", "data-skill": str(skill)})
    if table is None:
        raise LeaderboardError("Steam leaderboard table not found in the page")
    out = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        link = row.find("a", href=re.compile(r"^/profile/Steam/\d+"))
        if len(cells) < 3 or not link or not cells[0].get_text(strip=True).isdigit():
            continue
        rating = cells[-1].get_text(strip=True)
        out.append(Entry(rank=int(cells[0].get_text(strip=True)),
                         name=htmllib.unescape(link.get_text(strip=True)),
                         steam_id=link["href"].rsplit("/", 1)[-1],
                         mmr=int(rating) if rating.isdigit() else None))
    out.sort(key=lambda e: e.rank)
    if len(out) < MIN_ENTRIES:
        raise LeaderboardError(f"only {len(out)} players found in the Steam table")
    return out


def fetch(url: str = DEFAULT_URL, playlist: str = "doubles", timeout: float = 30) -> list[Entry]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise LeaderboardError(f"download failed: {e}") from e
    if r.status_code != 200:
        raise LeaderboardError(f"download failed: HTTP {r.status_code}")
    return parse(r.text, playlist)


def load_cache(path: Path) -> tuple[list[Entry], float]:
    """(entries, time saved); ([], 0) if there is no usable cache."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Entry(**e) for e in data["entries"]], float(data["fetched"])
    except (OSError, ValueError, KeyError, TypeError):
        return [], 0.0


def save_cache(path: Path, entries: list[Entry]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched": time.time(), "entries": [asdict(e) for e in entries]},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def get(settings: dict, cache: Path, now: float | None = None) -> list[Entry]:
    """The top players: from the cache if fresh, else downloaded (falling back to the
    cache on failure). Returns [] if disabled or nothing is available."""
    if not settings.get("enabled", True):
        return []
    now = time.time() if now is None else now
    cached, fetched = load_cache(cache)
    top = int(settings.get("top", 100))
    if cached and now - fetched < float(settings.get("refresh_hours", 24)) * 3600:
        return cached[:top]
    try:
        entries = fetch(settings.get("url", DEFAULT_URL), settings.get("playlist", "doubles"))
        save_cache(cache, entries)
        log.info("leaderboard: downloaded %d players", len(entries))
        return entries[:top]
    except LeaderboardError as e:
        if cached:
            log.warning("leaderboard: %s; using the list saved %.0f hours ago", e, (now - fetched) / 3600)
            print(f"WARNING: could not update the leaderboard ({e}); using the saved list.")
            return cached[:top]
        log.warning("leaderboard: %s; using players.txt only", e)
        print(f"WARNING: could not get the leaderboard ({e}); using players.txt only.")
        return []
