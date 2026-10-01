"""Leaderboard reading, refresh/fallback, and combining/matching players. Run: python -m unittest"""
import json
import random
import tempfile
from datetime import date, datetime
import unittest
from pathlib import Path
from unittest import mock

from rlvid import leaderboard, schedule
from rlvid.leaderboard import Entry
from rlvid.config import RosterEntry, parse_roster_line
from rlvid.search import Pair, Player, _featured, combine, to_match


def page(n: int = 25) -> str:
    """An rlstats.net-like page: an Epic table first, then the Steam Doubles one (rows out of order)."""
    rows = "".join(f'<tr class="even" data-page="1"><td>{i}</td><td><a href="/profile/Steam/{76561198000000000 + i}" '
                   f'title="Stats for Player{i}">Player{i}{" &amp; co" if i == 2 else ""}</a></td><td>{2200 - i}</td></tr>'
                   for i in reversed(range(1, n + 1)))
    epic = ('<table data-platform="Epic" data-skill="11"><tr><td>1</td><td><a href="/profile/Epic/abc">E</a></td>'
            '<td>1700</td></tr></table>')
    return (f'<html>{epic}<table data-platform="Steam" data-skill="11" hidden><tr class="even"><th>#</th><th>Name</th>'
            f'<th>Rating</th></tr>{rows}</table></html>')


class ParseTest(unittest.TestCase):
    def test_entries_in_rank_order(self):
        e = leaderboard.parse(page())
        self.assertEqual(len(e), 25)
        self.assertEqual(e[0], Entry(1, "Player1", "76561198000000001", None, 2199))
        self.assertEqual(e[1].name, "Player2 & co")  # HTML entities decoded

    def test_page_without_the_leaderboard(self):
        with self.assertRaises(leaderboard.LeaderboardError):
            leaderboard.parse("<html>Just a moment...</html>")
        with self.assertRaises(leaderboard.LeaderboardError):
            leaderboard.parse(page(), playlist="snowday")
        with self.assertRaises(leaderboard.LeaderboardError):
            leaderboard.parse(page(5))  # too few players: not the real board


class GetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "leaderboard.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_downloads_and_saves_then_reuses_within_a_day(self):
        with mock.patch.object(leaderboard, "fetch", return_value=leaderboard.parse(page())) as f:
            self.assertEqual(len(leaderboard.get({}, self.cache, now=1000)), 25)
            self.assertEqual(f.call_count, 1)
            fetched = json.loads(self.cache.read_text())["fetched"]
            leaderboard.get({}, self.cache, now=fetched + 3600)          # an hour later: cached
            self.assertEqual(f.call_count, 1)
            leaderboard.get({}, self.cache, now=fetched + 25 * 3600)     # next day: downloaded again
            self.assertEqual(f.call_count, 2)

    def test_failure_uses_saved_list_or_nothing(self):
        boom = leaderboard.LeaderboardError("HTTP 403")
        with mock.patch.object(leaderboard, "fetch", side_effect=boom), mock.patch("builtins.print"):
            self.assertEqual(leaderboard.get({}, self.cache), [])        # nothing saved: players.txt only
            leaderboard.save_cache(self.cache, leaderboard.parse(page()))
            self.assertEqual(len(leaderboard.get({}, self.cache, now=10 ** 12)), 25)  # old, but used

    def test_top_and_disabled(self):
        leaderboard.save_cache(self.cache, leaderboard.parse(page()))
        self.assertEqual(len(leaderboard.get({"top": 10}, self.cache)), 10)
        self.assertEqual(leaderboard.get({"enabled": False}, self.cache), [])


class CombineAndMatchTest(unittest.TestCase):
    BOARD = [Entry(1, "zen", "111"), Entry(2, "Atow", "222", "US"), Entry(3, "zen", "111")]

    def test_only_roster_players_with_ids_from_leaderboard(self):
        roster = [RosterEntry("atow", 3), RosterEntry("nass", 2), RosterEntry("vatira", 3, "999")]
        players = combine(roster, self.BOARD)  # zen is on the board but not in the roster: left out
        self.assertEqual([(p.name, p.steam_id, p.source, p.weight) for p in players],
                         [("atow", "222", "leaderboard", 3), ("nass", None, "players.txt", 2),
                          ("vatira", "999", "players.txt", 3)])
        self.assertEqual(players[0].query, {"player-id": "steam:222"})
        self.assertEqual(players[1].query, {"player-name": "nass"})

    def test_roster_lines(self):
        cases = {"zen 5": ("zen", 5, None), "atow 3 steam:76561198289610054": ("atow", 3, "76561198289610054"),
                 "justin.": ("justin.", 1, None), "	juicy 2  # popular": ("juicy", 2, None),
                 "my name 2": ("my name", 2, None), "trk511": ("trk511", 1, None), "# comment": None, "": None}
        for line, want in cases.items():
            e = parse_roster_line(line)
            self.assertEqual(None if e is None else (e.name, e.weight, e.steam_id), want, line)

    def test_featured_by_steam_id_even_when_renamed(self):
        wanted = [Player("zen", "111"), Player("nass")]
        ids = {"zen (new name)": "111", "nass": "999"}
        self.assertEqual(_featured(["zen (new name)", "nass", "other"], wanted, ids),
                         [("zen (new name)", "zen"), ("nass", "nass")])

    def test_same_name_different_id_is_not_matched(self):
        self.assertEqual(_featured(["zen"], [Player("zen", "111")], {"zen": "555"}), [])

    def test_to_match_uses_ids(self):
        def team(players, goals):
            return {"goals": goals, "players": [{"name": n, "id": {"platform": "steam", "id": i}} for n, i in players]}
        r = {"id": "r1", "date": "2026-01-01T12:00:00+00:00", "playlist_id": "ranked-doubles", "map_name": "m",
             "duration": 300, "blue": team([("ZEN!", "111"), ("x", "5")], 2), "orange": team([("y", "6"), ("z", "7")], 1)}
        m = to_match(r, [Player("zen", "111")])
        self.assertEqual((m.camera_player, m.camera_listed), ("ZEN!", "zen"))


class ScheduleTest(unittest.TestCase):
    TODAY = date(2026, 10, 10)

    def make_pair(self, player, day):
        p = mock.Mock(spec=Pair)
        p.player = player
        p.second = mock.Mock(date=datetime(2026, 10, day, 13))
        return p

    def setUp(self):
        self.players = [Player("zen", "1", weight=5), Player("atow", weight=3), Player("crr", weight=2)]
        self.pairs = [self.make_pair("zen", 9), self.make_pair("zen", 5), self.make_pair("atow", 8), self.make_pair("crr", 7)]

    def test_priority_is_weight_times_days_and_newest_pair(self):
        history = [{"player": "zen", "processed_date": "2026-10-06"}, {"player": "atow", "processed_date": "2026-10-02"}]
        c = {x.player: x for x in schedule.candidates(self.players, self.pairs, history, self.TODAY)}
        self.assertEqual((c["zen"].days, c["zen"].priority), (4, 20))
        self.assertEqual((c["atow"].days, c["atow"].priority), (8, 24))
        self.assertEqual((c["crr"].last_video, c["crr"].priority), (None, 28))  # never: 14 days x 2
        self.assertEqual(c["zen"].pair.second.date.day, 9)  # newest of zen's pairs

    def test_cooldown_skips_yesterdays_player(self):
        history = [{"player": "zen", "processed_date": "2026-10-09"}]
        cands = schedule.candidates(self.players, self.pairs, history, self.TODAY)
        zen = next(x for x in cands if x.player == "zen")
        self.assertTrue(zen.cooldown)
        rng = random.Random(0)
        self.assertNotIn("zen", {schedule.choose(cands, rng).player for _ in range(200)})

    def test_pick_is_random_and_proportional_to_priority(self):
        history = [{"player": "zen", "processed_date": "2026-10-06"}, {"player": "atow", "processed_date": "2026-10-02"},
                   {"player": "crr", "processed_date": "2026-10-03"}]  # priorities 20, 24, 14
        cands = schedule.candidates(self.players, self.pairs, history, self.TODAY)
        rng = random.Random(1)
        picks = [schedule.choose(cands, rng).player for _ in range(6000)]
        for name, pr in (("zen", 20), ("atow", 24), ("crr", 14)):
            self.assertAlmostEqual(picks.count(name) / len(picks), pr / 58, delta=0.03)

    def test_everyone_in_cooldown_still_picks_someone(self):
        history = [{"player": n, "processed_date": "2026-10-09"} for n in ("zen", "atow", "crr")]
        self.assertIsNotNone(schedule.choose(schedule.candidates(self.players, self.pairs, history, self.TODAY)))
        self.assertIsNone(schedule.choose([]))


if __name__ == "__main__":
    unittest.main()
