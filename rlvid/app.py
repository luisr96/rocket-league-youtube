"""Shared run logic for pick.py and auto.py."""
import json
import logging
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

from . import describe, game, leaderboard, metadata, obs, overlay, schedule, thumbnail, video, youtube
from .hudserver import HudServer
from .api import ApiError, Ballchasing
from .bakkes import BakkesError, read_status
from .config import ConfigError, load_config
from .history import History
from .search import Match, Pair, Player, combine, find_pairs, find_unseen

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
        opp = " | ".join(", ".join(m.opponents) for m in p.matches)
        print(f"{i:>2}  {p.player:10}  {p.mode:4}  {p.first.date:%Y-%m-%d %H:%M}  {_fmt_gap(p):>4}  "
              f"{p.first.score + ' ' + p.second.score:9}  {p.first.length + ' ' + p.second.length:11}  {opp}")


def video_path(cfg, pair: Pair, suffix: str = "") -> Path:
    """<output_dir>/<today>_<player>_<mode>_<first game date>_<gap>_<teammates>_<opponents>.mp4,
    with _2, _3, ... if that name is taken.

    Teammates and opponents are one bracketed group per game, e.g. (A+B)(C+D),
    even when both games had the same people; the teammates part is left out in 1v1.
    """
    out_dir = cfg.path("output_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = [f"{date.today():%Y-%m-%d}", _safe(pair.player), pair.mode,
             f"{pair.first.date:%Y-%m-%d}", _fmt_gap(pair)]
    if any(pair.teammates):
        parts.append(_fmt_groups(pair.teammates))
    parts.append(_fmt_groups(pair.opponents))
    stem = "_".join(parts) + suffix
    path, n = out_dir / f"{stem}.mp4", 2
    while path.exists():
        path, n = out_dir / f"{stem}_{n}.mp4", n + 1
    return path


def _fmt_groups(groups: list[list[str]]) -> str:
    return "".join("(" + "+".join(_safe(n) for n in g) + ")" for g in groups)


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*()+\s]+', "-", name).strip("-.") or "player"


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
    ffmpeg = video.find_ffmpeg(cfg.raw.get("video", {}).get("ffmpeg", "ffmpeg"))  # fail before recording
    replays = [download(cfg, api, m) for m in pair.matches]

    g = cfg.raw["game"]
    o = cfg.raw["obs"]
    obs.stop_leftover(o["host"], o["port"])  # from a killed run; we hold the run lock
    if g.get("fresh_start", True):
        # A recording OBS asks for confirmation instead of closing; close_all
        # then times out with an error rather than cutting that recording off
        # (a leftover one from a killed run was stopped above).
        game.close_all()
    recorder = obs.connect(o["host"], o["port"], o["exe"])  # OBS first, so it can't steal focus from the game
    kickoff = cfg.raw.get("camera", {}).get("kickoff_director_seconds", 0)
    buffer = cfg.raw["recording"]["buffer_seconds"]
    debug = cfg.raw["recording"].get("debug_seconds", 0)
    fade_ms = o.get("transition_ms", 0)
    ov = cfg.raw.get("overlay", {})
    h = cfg.raw.get("hud", {})
    hud = start_hud(int(h.get("port", 8765))) if h.get("enabled", True) else None
    try:
        if hud:
            # The broadcast overlay (scoreboard, players, stats, intro) replaces the names overlay.
            recorder.setup_overlay(h.get("source", "RLVid HUD"), hud_url(hud, h))
            recorder.hide_source(ov.get("source", "RLVid Names"))
        elif ov.get("enabled", True):
            recorder.setup_overlay(ov["source"], overlay.url(ov))
        rcon = game.ensure_game(g)
        rcon.send(f"rlvid_kickoff_keep_focus {int(bool(cfg.raw.get('camera', {}).get('kickoff_keep_focus', False)))}")
        rcon.send(f"rlvid_hide_scoreboard {int(bool(hud and h.get('hide_game_scoreboard', False)))}")
    except BaseException:  # e.g. the game did not start: don't leave the HUD server running
        if hud:
            hud.stop()
        raise
    games = []  # per-game data for the <video>.json file
    try:
        for i, (m, replay) in enumerate(zip(pair.matches, replays), 1):
            print(f"Game {i}/2: playing {m.score} ({m.length}), camera on {m.camera_player} ...")
            # The replay is held on its first frame (camera already set) until
            # recording runs, so the video starts at the kickoff countdown.
            game.start_replay(rcon, replay, m.camera_focus_id or m.camera_player,
                              g["replay_start_timeout_seconds"], kickoff, hold=True)
            game.bring_to_front()  # a minimized full-screen game records as black
            if i == 1:
                # No black video after a fresh game launch. Not for game 2: the
                # black scene is showing then, so the capture reads black anyway.
                recorder.wait_for_capture(o["capture_source"])
                recorder.start()
            else:
                recorder.resume()
                if fade_ms:
                    time.sleep(0.3)  # a moment of black between the games
                    recorder.fade_in(fade_ms)  # onto the held kickoff frame
            players = (read_status() or {}).get("players")  # names as the game shows them
            if hud:
                # The selected player's name for the target, even if they used another in this game.
                blue, orange = overlay.sides(m, players, pair.player)
                pov = (blue if metadata.target_team(m) == "blue" else orange)[0]
                hud.set_game(m.camera_focus_id, m.camera_player, hud_label(cfg, m), pov)
                hud.show_intro(i, pov.upper(), blue, orange)
            elif ov.get("enabled", True):
                recorder.show_overlay(ov["source"], overlay.url(ov, m, players, pair.player))  # fades out by itself
            time.sleep(0.5)
            # Where this game starts in the video: the recording length and the
            # clock time just before release; goal times build on these.
            video_start = recorder.record_seconds()
            release_wall = time.time()
            game.release(rcon)
            # Keep the last HUD reading taken during the replay: if the game ends by leaving
            # the replay, the data read afterwards no longer has the players' final stats.
            last_hud = {}

            def remember_hud():
                h = read_hud()
                if h and h.get("in_replay") and h.get("players"):
                    last_hud["data"] = h

            if debug:
                print(f"  debug: recording only {debug:g}s of this game")
                game.wait_for_end(debug, remember_hud)
            elif game.wait_for_end(m.duration + buffer, remember_hud) == "timeout":
                log.warning("game %d did not report its end; stopped after duration + %ds buffer", i, buffer)
            game_goals = metadata.goals(m, (read_status() or {}).get("goals", []), video_start, release_wall)
            log.info("game %d goals: %s", i, game_goals)
            # The replay is still on its last frames here, so the HUD data has the final stats.
            now = read_hud()
            stats, overtime = metadata.end_stats(m, now if now and now.get("in_replay") else last_hud.get("data"))
            log.info("game %d stats: %s overtime: %s", i, stats, overtime)
            games.append(metadata.game(i, m, overlay.sides(m, players, pair.player), game_goals, video_start, stats, overtime))
            if fade_ms:  # to black after each game, including the end of the video
                recorder.fade_out(o.get("transition_scene", "RLVid Black"), fade_ms)
            if i == 1:
                recorder.pause()  # keep the loading screen of game 2 out of the video
        raw = recorder.stop()
        if fade_ms:
            recorder.restore_scene()  # don't leave OBS on the black scene
    except BaseException:
        recorder.abort()
        raise
    finally:
        rcon.close()
        if hud:
            hud.stop()

    final = video_path(cfg, pair, suffix="_debug" if debug else "")
    print("Finalizing video ...")
    video.finalize(ffmpeg, raw, final)
    print(f"Saved video: {final}")
    log.info("saved %s", metadata.write(final, pair, games, cfg.search))
    make_thumbnails(cfg, ffmpeg, final)
    if debug:
        print("Debug run: games not marked as done.")
        log.info("debug run done: %s", final)
        game.close_game()
        return True
    today = date.today().isoformat()
    for m, info in zip(pair.matches, games):
        history.add({
            "id": m.id,
            "processed_date": today,
            "game_date": m.date.isoformat(),
            "player": pair.player,
            "players": {"blue": m.blue_players, "orange": m.orange_players},
            "overlay": info["overlay"],  # names as shown in the game (and the overlay)
            "map": m.map,
            "score": m.score,
            "playlist": m.playlist,
            "video": str(final),
        })
    log.info("done: %s", final)
    game.close_game()
    return True


def start_hud(port: int) -> HudServer | None:
    """The HUD server on port, or the next free one of the 9 after it; None (record
    without the HUD) if all are taken."""
    for p in range(port, port + 10):
        hud = HudServer(p)
        try:
            hud.start()
            if p != port:
                log.warning("HUD port %d was busy; using %d", port, p)
            return hud
        except OSError:
            continue
    log.warning("HUD ports %d-%d are all busy; recording without the HUD", port, port + 9)
    print(f"WARNING: ports {port}-{port + 9} are busy; recording without the HUD overlay.")
    return None


def enough_disk_space(cfg) -> bool:
    """False (and a warning) if the video drive has less than [recording] min_free_gb free."""
    import shutil
    need = float(cfg.raw["recording"].get("min_free_gb", 20))
    out = cfg.path("output_dir")
    out.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out).free / 2**30
    if free < need:
        log.warning("only %.1f GB free on %s (need %g GB); not recording", free, out.anchor, need)
        print(f"WARNING: only {free:.1f} GB free on {out.anchor} (need {need:g} GB); not recording.")
        return False
    return True


def read_hud() -> dict | None:
    """The plugin's live HUD data (rlvid_hud.json), or None. Retried: the plugin
    replaces the file ~30 times a second, so a read can land mid-replace."""
    from .hudserver import HUD_DATA
    for _ in range(5):
        try:
            return json.loads(HUD_DATA.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.02)
    return None


def hud_url(hud: HudServer, h: dict) -> str:
    from urllib.parse import urlencode
    q = {"brand": h.get("brand", "Like, comment & subscribe!"), "scale": h.get("scale", 1.0), "top": h.get("top", 0),
         "meter": h.get("meter", "off"), "hold": h.get("intro_seconds", 4)}
    return hud.url + "?" + urlencode(q)


def hud_label(cfg, m: Match) -> str:
    """The line under the scoreboard, e.g. "SSL 2v2"."""
    s = cfg.search
    rank = m.rank or (s.get("min_rank", "") if s.get("min_rank") == s.get("max_rank") else "")
    return f"{describe.rank_names(rank)[0]} {m.mode}".strip()


def make_thumbnails(cfg, ffmpeg: str, final: Path) -> None:
    """Thumbnail candidates next to the video. A failure is only a warning: the video is already saved."""
    t = cfg.raw.get("thumbnail", {})
    if not t.get("enabled", True):
        return
    print("Making thumbnails ...")
    try:
        paths = thumbnail.make(ffmpeg, final, t)
        print(f"Saved {len(paths)} thumbnail(s) next to the video.")
    except (thumbnail.ThumbnailError, OSError, KeyError, ValueError) as e:
        log.warning("thumbnails failed: %s", e)
        print(f"WARNING: thumbnails failed ({e}); the video is saved. Retry with: python thumbnails.py \"{final}\"")


def players_for(cfg) -> list[Player]:
    """The players from players.txt, with Steam IDs from the leaderboard where it has
    them. Worked out once per run; the leaderboard is downloaded at most once a day."""
    if getattr(cfg, "_players", None) is None:
        board = leaderboard.get(cfg.raw.get("leaderboard", {}), cfg.path("leaderboard_file"))
        cfg._players = combine(cfg.roster or cfg.players, board)
        log.info("players: %d (%d searched by Steam ID)", len(cfg._players),
                 sum(1 for p in cfg._players if p.steam_id))
    return cfg._players


def find_pairs_for(cfg, api, history) -> list[Pair]:
    matches = find_unseen(api, cfg, history.ids(), players_for(cfg), history.fingerprints())
    return find_pairs(matches, cfg.raw["pairs"]["max_gap_days"])


def player_candidates(cfg, api, history) -> list[schedule.Candidate]:
    """One candidate per player with unrecorded games, by priority (see rlvid/schedule.py)."""
    s = cfg.raw.get("schedule", {})
    return schedule.candidates(players_for(cfg), find_pairs_for(cfg, api, history), history.entries, date.today(),
                               float(s.get("never_days", 14)), int(s.get("cooldown_days", 1)))


def print_candidates(cands: list[schedule.Candidate]) -> None:
    print(f"{'':>2}  {'weight':>6}  {'last video':10}  {'priority':>8}")
    for c in cands:
        last = f"{c.last_video:%Y-%m-%d}" if c.last_video else "never"
        note = "  (cooldown)" if c.cooldown else ""
        print(f"{'':>2}  {c.weight:>6g}  {last:10}  {c.priority:>8.0f}  {c.player}{note}")


def cmd_pick(cfg, api, history) -> int:
    print(f"Searching ballchasing for {len(players_for(cfg))} players ...")
    # One row per player (their newest pair), most due first.
    cands = player_candidates(cfg, api, history)[: cfg.search["pick_list_size"]]
    pairs = [c.pair for c in cands]
    if not pairs:
        print("No unseen pairs of games found.")
        return 0
    print_pairs(pairs)
    print("\nMost due first (priority = weight x days since their last video):")
    print_candidates(cands)
    choice = input("\nNumber to record (empty to quit): ").strip()
    if not choice:
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(pairs):
        print("Invalid choice.")
        return 1
    return 0 if process(pairs[int(choice) - 1], cfg, api, history) else 1


def cmd_auto(cfg, api, history) -> int:
    if not enough_disk_space(cfg):
        return 1
    print(f"Searching ballchasing for {len(players_for(cfg))} players ...")
    cands = player_candidates(cfg, api, history)
    chosen = schedule.choose(cands)
    if not chosen:
        log.info("no unseen pairs of games")
        print("No unseen pairs of games found.")
        return 0
    log.info("chose %s (weight %g, priority %.0f) among %d players", chosen.player, chosen.weight, chosen.priority,
             len(cands))
    print(f"Chose {chosen.player} (priority {chosen.priority:.0f}; {len(cands)} players had games).")
    return 0 if process(chosen.pair, cfg, api, history) else 1


def run(command) -> int:
    """Run command, unless another run is in progress (see rlvid/runlock.py)."""
    from .config import ROOT
    from .runlock import RunLock
    with RunLock(ROOT / "run.lock") as lock:
        if lock.f is None:
            print(f"Another run is in progress ({lock.holder}); exiting.")
            return 3
        return _run(command)


def _run(command) -> int:
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
    except (obs.ObsError, video.VideoError) as e:
        log.error("OBS/video error: %s", e)
        return 1
    except youtube.YouTubeError as e:
        log.error("YouTube error: %s", e)
        return 1
    except (game.GameError, BakkesError) as e:
        log.error("game error: %s", e)
        return 1
    except Exception:
        log.exception("unexpected failure")
        return 1
