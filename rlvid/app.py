"""Shared run logic for pick.py and auto.py."""
import logging
import sys
from datetime import date, datetime

from .api import ApiError, Ballchasing
from .config import ConfigError, load_config
from .history import History
from .search import Match, find_unseen

log = logging.getLogger("rlvid")


def setup_logging(log_dir) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    sh.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)


def print_table(matches: list[Match]) -> None:
    print(f"{'#':>2}  {'date':16}  {'playlist':16}  {'score':5}  {'len':5}  {'featured':14}  teams")
    for i, m in enumerate(matches, 1):
        teams = f"{', '.join(m.blue_players)}  vs  {', '.join(m.orange_players)}"
        print(f"{i:>2}  {m.date:%Y-%m-%d %H:%M}  {m.playlist:16}  {m.score:5}  "
              f"{m.length:5}  {', '.join(m.featured):14}  {teams}")


def process(match: Match, cfg, api, history) -> bool:
    """Download + record. Recording is implemented in later phases."""
    log.info("selected %s (%s, cam=%s)", match.id, match.score, match.camera_player)
    print(f"Selected {match.id}  camera on {match.camera_player}")
    dest = cfg.path("demos_dir") / f"{match.id}.replay"
    if dest.exists():
        print(f"Already downloaded: {dest}")
    else:
        print("Downloading replay ...")
        api.download_replay(match.id, dest)
        print(f"Saved to {dest}")
    print("(Phase 2: recording not implemented yet, nothing marked as done.)")
    return False


def cmd_pick(cfg, api, history) -> int:
    if history.has_entry_for(date.today()):
        if input("A replay was already processed today. Continue anyway? [y/N] ").strip().lower() != "y":
            return 0
    print(f"Searching ballchasing for: {', '.join(cfg.players)} ...")
    matches = find_unseen(api, cfg, history.ids())[: cfg.search["pick_list_size"]]
    if not matches:
        print("No unseen matches found.")
        return 0
    print_table(matches)
    choice = input("\nNumber to process (empty to quit): ").strip()
    if not choice:
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(matches):
        print("Invalid choice.")
        return 1
    return 0 if process(matches[int(choice) - 1], cfg, api, history) else 1


def cmd_auto(cfg, api, history) -> int:
    if history.has_entry_for(date.today()):
        log.info("already processed a replay today, exiting")
        return 0
    matches = find_unseen(api, cfg, history.ids())
    if not matches:
        log.info("no unseen matches")
        return 0
    return 0 if process(matches[0], cfg, api, history) else 1


def run(command) -> int:
    """Load config, set up logging and the API client, then run command(cfg, api, history)."""
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg.path("log_dir"))
    api = Ballchasing(cfg.api_key, cfg.api["base_url"], cfg.api["min_delay_seconds"], cfg.api["max_retries"])
    history = History(cfg.path("history_file"))
    log.info("run started: %s players=%s", command.__name__, cfg.players)
    try:
        return command(cfg, api, history)
    except ApiError as e:
        log.error("API error: %s", e)
        return 1
    except Exception:
        log.exception("unexpected failure")
        return 1
