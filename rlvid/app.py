"""Shared run logic for pick.py and auto.py."""
import logging
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from . import game, obs
from .api import ApiError, Ballchasing
from .bakkes import BakkesError
from .config import ConfigError, load_config
from .history import History
from .search import Match, Pair, find_pairs, find_unseen

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


def _fmt_gap(p: Pair) -> str:
    mins = int(p.gap.total_seconds() // 60)
    return f"{mins}m" if mins < 120 else f"{mins // 60}h" if mins < 48 * 60 else f"{mins // 1440}d"


def print_pairs(pairs: list[Pair]) -> None:
    print(f"{'#':>2}  {'player':10}  {'mode':4}  {'first game':16}  {'gap':>4}  {'scores':9}  {'lengths':11}  opponents")
    for i, p in enumerate(pairs, 1):
        opp = " | ".join(", ".join(m.orange_players if m.camera_player in m.blue_players else m.blue_players)
                         for m in p.matches)
        print(f"{i:>2}  {p.player:10}  {p.mode:4}  {p.first.date:%Y-%m-%d %H:%M}  {_fmt_gap(p):>4}  "
              f"{p.first.score + ' ' + p.second.score:9}  {p.first.length + ' ' + p.second.length:11}  {opp}")


def video_path(cfg, pair: Pair) -> Path:
    """<output_dir>/YYYY-MM-DD_<player>_<mode>.mp4, with _2, _3, ... if that name is taken."""
    out_dir = cfg.path("output_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today():%Y-%m-%d}_{_safe(pair.player)}_{pair.mode}"
    path, n = out_dir / f"{stem}.mp4", 2
    while path.exists():
        path, n = out_dir / f"{stem}_{n}.mp4", n + 1
    return path


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "-", name).strip("-.") or "player"


def download(cfg, api, match: Match) -> Path:
    dest = cfg.path("demos_dir") / f"{match.id}.replay"
    if dest.exists():
        log.info("already downloaded: %s", dest)
    else:
        print(f"Downloading replay {match.id} ...")
        api.download_replay(match.id, dest)
    return dest


def process(pair: Pair, cfg, api, history) -> bool:
    """Record both games of the pair into one video; mark them done only on success."""
    log.info("selected pair: %s %s, %s then %s", pair.player, pair.mode, pair.first.id, pair.second.id)
    print(f"Selected {pair.player} {pair.mode}: {pair.first.id} + {pair.second.id}")
    o = cfg.raw["obs"]
    recorder = obs.Recorder(o["host"], o["port"])  # fail fast if OBS is not ready
    replays = [download(cfg, api, m) for m in pair.matches]

    g = cfg.raw["game"]
    kickoff = cfg.raw.get("camera", {}).get("kickoff_director_seconds", 0)
    buffer = cfg.raw["recording"]["buffer_seconds"]
    rcon = game.ensure_game(g)
    try:
        for i, (m, replay) in enumerate(zip(pair.matches, replays), 1):
            print(f"Game {i}/2: playing {m.score} ({m.length}), camera on {m.camera_player} ...")
            game.start_replay(rcon, replay, m.camera_focus_id or m.camera_player,
                              g["replay_start_timeout_seconds"], kickoff)
            recorder.start() if i == 1 else recorder.resume()
            reason = game.wait_for_end(m.duration + buffer)
            if reason == "timeout":
                log.warning("game %d did not report its end; stopped after duration + %ds buffer", i, buffer)
            if i == 1:
                recorder.pause()  # keep the loading screen of game 2 out of the video
        raw = recorder.stop()
    except BaseException:
        recorder.abort()
        raise
    finally:
        rcon.close()

    final = video_path(cfg, pair)
    shutil.move(str(raw), final)
    print(f"Saved video: {final}")
    today = date.today().isoformat()
    for m in pair.matches:
        history.add({
            "id": m.id,
            "processed_date": today,
            "game_date": m.date.isoformat(),
            "player": pair.player,
            "players": {"blue": m.blue_players, "orange": m.orange_players},
            "map": m.map,
            "score": m.score,
            "playlist": m.playlist,
            "video": str(final),
        })
    log.info("done: %s", final)
    return True


def find_pairs_for(cfg, api, history) -> list[Pair]:
    matches = find_unseen(api, cfg, history.ids())
    return find_pairs(matches, cfg.raw["pairs"]["max_gap_days"])


def cmd_pick(cfg, api, history) -> int:
    if history.has_entry_for(date.today()):
        if input("A video was already made today. Continue anyway? [y/N] ").strip().lower() != "y":
            return 0
    print(f"Searching ballchasing for: {', '.join(cfg.players)} ...")
    pairs = find_pairs_for(cfg, api, history)[: cfg.search["pick_list_size"]]
    if not pairs:
        print("No unseen pairs of games found.")
        return 0
    print_pairs(pairs)
    choice = input("\nNumber to record (empty to quit): ").strip()
    if not choice:
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(pairs):
        print("Invalid choice.")
        return 1
    return 0 if process(pairs[int(choice) - 1], cfg, api, history) else 1


def cmd_auto(cfg, api, history) -> int:
    if history.has_entry_for(date.today()):
        log.info("already made a video today, exiting")
        return 0
    pairs = find_pairs_for(cfg, api, history)
    if not pairs:
        log.info("no unseen pairs of games")
        return 0
    return 0 if process(pairs[0], cfg, api, history) else 1


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
    except obs.ObsError as e:
        log.error("OBS error: %s", e)
        return 1
    except (game.GameError, BakkesError) as e:
        log.error("game error: %s", e)
        return 1
    except Exception:
        log.exception("unexpected failure")
        return 1
