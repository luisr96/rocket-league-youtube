"""Titles, descriptions, tags and the upload flow (YouTube replaced by a fake). Run: python -m unittest"""
import json
import os
import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rlvid import describe, uploader, youtube
from rlvid.history import History


def data(rank: str = "supersonic-legend", season=24) -> dict:
    def game(i, team, overlay, score, start):
        return {"game": i, "video_start": start, "target": {"name": "atow", "team": team},
                "overlay": overlay, "score": score, "goals": []}
    return {"mode": "2v2", "rank": rank, "season": season, "games": [
        game(1, "orange", {"blue": ["TempoH", "Joyo"], "orange": ["Atow", "Arsenal✟"]}, {"blue": 2, "orange": 4}, 1.5),
        game(2, "blue", {"blue": ["Atow", "zach"], "orange": ["TempoH", "Kiileerrz"]}, {"blue": 6, "orange": 3}, 371.2),
    ]}


class DescribeTest(unittest.TestCase):
    def test_rank_names(self):
        self.assertEqual(describe.rank_names("supersonic-legend"), ("SSL", "Supersonic Legend"))
        self.assertEqual(describe.rank_names("grand-champion-2"), ("GC2", "Grand Champion 2"))
        self.assertEqual(describe.rank_names("champion-3"), ("Champion 3", "Champion 3"))
        self.assertEqual(describe.rank_names(""), ("", ""))

    TITLES = describe.load_titles(Path(__file__).resolve().parent.parent / "titles.txt")

    def test_fill_uses_in_game_name_in_capitals(self):
        self.assertEqual(describe.fill("{PLAYER} is OVERPOWERED in Rocket League! ({RANK} {MODE})", data()),
                         "ATOW is OVERPOWERED in Rocket League! (SSL 2v2)")

    def eligible_titles(self, d):
        f = describe.facts(d)
        return [describe.fill(p, d) for p in self.TITLES if describe.eligible(p, f)]

    def test_title_is_random_and_covers_all_eligible_patterns(self):
        rng = random.Random(1)
        seen = {describe.title(data(), self.TITLES, rng=rng) for _ in range(500)}
        self.assertEqual(seen, set(self.eligible_titles(data())))

    def test_title_avoids_recent_titles(self):
        all_titles = self.eligible_titles(data())
        rng = random.Random(2)
        for _ in range(50):
            self.assertEqual(describe.title(data(), self.TITLES, all_titles[1:], rng), all_titles[0])
        # every title used recently: any is allowed again
        self.assertIn(describe.title(data(), self.TITLES, all_titles, rng), all_titles)

    def test_title_without_rank(self):
        self.assertEqual(describe.fill("[wins>=1] {PLAYER} is OVERPOWERED in Rocket League! ({RANK} {MODE})", data(rank="")),
                         "ATOW is OVERPOWERED in Rocket League! (2v2)")
        for p in self.TITLES:
            t = describe.fill(p, data(rank=""))
            self.assertNotIn("  ", t)
            self.assertNotIn("( ", t)

    def test_special_titles_are_weighted(self):
        titles = ["general one", "general two", "[wins=2] special"]
        rng = random.Random(3)
        picks = [describe.title(data(), titles, rng=rng) for _ in range(5000)]  # data(): won both
        share = picks.count("special") / len(picks)
        self.assertAlmostEqual(share, 3 / 5, delta=0.03)  # weights 1, 1, 3
        picks = [describe.title(data(), titles, rng=rng, special_weight=1) for _ in range(5000)]
        self.assertAlmostEqual(picks.count("special") / len(picks), 1 / 3, delta=0.03)

    def test_conditions(self):
        f = {"wins": 2, "losses": 0, "goals": 4, "assists": 1, "saves": None, "shots": 6, "points": 900,
             "overtime": 0, "fastest": 112}
        self.assertTrue(describe.eligible("{PLAYER} any", f))
        self.assertTrue(describe.eligible("[wins=2] x", f))
        self.assertFalse(describe.eligible("[wins=0] x", f))
        self.assertTrue(describe.eligible("[wins=2, goals>=4, fastest>110] x", f))
        self.assertFalse(describe.eligible("[wins=2, goals>4] x", f))
        self.assertTrue(describe.eligible("[overtime!=1, assists<2, points<=900] x", f))
        self.assertFalse(describe.eligible("[saves>=0] x", f))       # unknown: never true
        self.assertFalse(describe.eligible("{PLAYER} {SAVES} saves", f))  # shows an unknown number
        self.assertTrue(describe.eligible("{PLAYER} {GOALS} goals", f))
        with self.assertRaises(ValueError):
            describe.parse_title("[wnis=2] x")
        with self.assertRaises(ValueError):
            describe.parse_title("[wins=2] {GOLAS} x")

    def test_facts_and_number_placeholders(self):
        d = data()
        for g, (goals, saves, ot) in zip(d["games"], ((1, 3, False), (2, 4, True))):
            g["stats"] = {"points": 300, "goals": goals, "assists": 0, "saves": saves, "shots": 3}
            g["overtime"] = ot
        d["games"][1]["goals"] = [{"by": "target", "team": "blue", "speed": 101.6},
                                  {"by": "teammate", "team": "blue", "speed": 140}]
        self.assertEqual(describe.facts(d), {"wins": 2, "losses": 0, "goals": 3, "assists": 0, "saves": 7, "shots": 6,
                                             "points": 600, "overtime": 1, "fastest": 102, "comebacks": 0})
        self.assertEqual(describe.fill("[saves>=5] {PLAYER}: {SAVES} saves, {GOALS} goals, {FASTEST} kph", d),
                         "ATOW: 7 saves, 3 goals, 102 kph")

    def test_comebacks_need_a_three_goal_deficit(self):
        d = data()  # Atow is orange in game 1, blue in game 2; both won
        b, o = {"team": "blue"}, {"team": "orange"}
        d["games"][0]["goals"] = [b, b, b, o, o, o, o]          # orange (Atow) 0-3 down, won 4-3: comeback
        d["games"][1]["goals"] = [o, o, b, b, b]                 # blue (Atow) only 0-2 down: not a comeback
        self.assertEqual(describe.facts(d)["comebacks"], 1)
        d["games"][0]["goals"] = [b, b, o, o, o, o]              # only 0-2 down
        self.assertEqual(describe.facts(d)["comebacks"], 0)

    def test_fastest_unknown_without_a_goal(self):
        f = describe.facts(data())  # no goals by the target
        self.assertIsNone(f["fastest"])
        self.assertFalse(describe.eligible("{PLAYER} hits {FASTEST} KPH", f))

    def test_lost_both_never_gets_a_win_title(self):
        d = data()
        for g in d["games"]:  # Atow's team loses both
            team = g["target"]["team"]
            g["score"] = {team: 0, ("orange" if team == "blue" else "blue"): 3}
        for t in self.eligible_titles(d):
            for word in ("UNSTOPPABLE", "UNDEFEATED", "OVERPOWERED", "Sweeps", "2 WINS", "Bounces Back"):
                self.assertNotIn(word, t)

    def test_titles_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "titles.txt"
            f.write_text("# comment\n\n{PLAYER} wins in {MODE}\n{PLAYR} typo line\n[wnis=2] typo\n[wins=2] ok\n",
                         encoding="utf-8")
            self.assertEqual(describe.load_titles(f), ["{PLAYER} wins in {MODE}", "[wins=2] ok"])
            f.write_text("# only comments\n", encoding="utf-8")
            self.assertEqual(describe.load_titles(f), describe.DEFAULT_TITLES)
        self.assertEqual(describe.load_titles(Path("missing.txt")), describe.DEFAULT_TITLES)
        self.assertGreater(len(self.TITLES), 50)

    def test_description(self):
        d = describe.description(data())
        self.assertTrue(d.startswith("Atow Rocket League gameplay (SSL 2v2)."))
        self.assertNotIn("eason", d)  # no season anywhere
        self.assertIn("0:00 Game 1: Atow & Arsenal✟ vs TempoH & Joyo\n", d)
        self.assertIn("6:11 Game 2: Atow & zach vs TempoH & Kiileerrz\n", d)
        self.assertNotIn("4-2", d)  # no score spoilers
        self.assertIn("which player you'd like to see next. Subscribe for more SSL content!", d)
        self.assertTrue(d.endswith("#rocketleague #gameplay #atow"))

    def test_tags_fit_youtube_limit(self):
        t = describe.tags(data())
        self.assertIn("atow rocket league", t)
        self.assertFalse(any("season" in x for x in t))
        self.assertEqual(len(t), len(set(t)))
        self.assertLessEqual(sum(len(x) + 1 for x in t), 500)


class UploadFlowTest(unittest.TestCase):
    TITLES = describe.load_titles(Path(__file__).resolve().parent.parent / "titles.txt")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.history = History(self.dir / "history.json")

    def tearDown(self):
        self.tmp.cleanup()

    def video(self, name: str, with_json: bool = True, thumbs=("1s-before", "5s-after")) -> Path:
        v = self.dir / f"{name}.mp4"
        v.write_bytes(b"video")
        if with_json:
            v.with_suffix(".json").write_text(json.dumps(data()), encoding="utf-8")
        for t in thumbs:
            v.with_name(f"{name}_thumb_{t}.jpg").write_bytes(b"jpg")
        return v

    def test_pending_skips_debug_missing_data_and_uploaded(self):
        a = self.video("a")
        os.utime(a, (time.time() - 100, time.time() - 100))  # older, so first
        b = self.video("b")
        self.video("c_debug")
        self.video("d", with_json=False)
        e = self.video("e")
        self.history.add({"id": "x", "video": str(e), "youtube_id": "abc"})
        self.assertEqual(uploader.pending(self.dir, self.history), [a, b])

    def test_thumbnail_choice_and_fallback(self):
        v = self.video("a", thumbs=("5s-after",))
        self.assertEqual(uploader.thumbnail_for(v, "1s-before").name, "a_thumb_5s-after.jpg")
        self.assertIsNone(uploader.thumbnail_for(self.video("b", thumbs=()), "1s-before"))

    def test_upload_records_id_deletes_video_keeps_thumbnails(self):
        v = self.video("a")
        self.history.add({"id": "g1", "video": str(v)})
        self.history.add({"id": "g2", "video": str(v)})
        with mock.patch.object(youtube, "upload", return_value="VID123") as up, \
                mock.patch.object(youtube, "set_thumbnail") as thumb:
            uploader.upload_one(object(), v, {}, self.history)
        title = up.call_args.args[2]
        self.assertEqual(self.history.entries[0]["youtube_title"], title)
        self.assertIn(title, [describe.fill(p, data()) for p in UploadFlowTest.TITLES])
        self.assertEqual(up.call_args.args[5], "private")
        self.assertEqual(thumb.call_args.args[2].name, "a_thumb_1s-before.jpg")
        self.assertEqual([e["youtube_id"] for e in self.history.entries], ["VID123", "VID123"])
        self.assertFalse(v.exists())
        self.assertFalse(v.with_suffix(".json").exists())
        self.assertTrue(v.with_name("a_thumb_1s-before.jpg").exists())

    def test_failed_thumbnail_still_counts_as_uploaded(self):
        v = self.video("a")
        with mock.patch.object(youtube, "upload", return_value="VID"), \
                mock.patch.object(youtube, "set_thumbnail", side_effect=youtube.YouTubeError("not verified")):
            self.assertEqual(uploader.upload_one(object(), v, {}, self.history), "VID")
        self.assertFalse(v.exists())

    def test_thumbnail_quota_error_still_deletes_the_video(self):
        v = self.video("a")
        with mock.patch.object(youtube, "upload", return_value="VID"),                 mock.patch.object(youtube, "set_thumbnail", side_effect=youtube.QuotaError("quota")):
            self.assertEqual(uploader.upload_one(object(), v, {}, self.history), "VID")
        self.assertFalse(v.exists())

    def test_failed_upload_keeps_files(self):
        v = self.video("a")
        with mock.patch.object(youtube, "upload", side_effect=youtube.YouTubeError("boom")):
            with self.assertRaises(youtube.YouTubeError):
                uploader.upload_one(object(), v, {}, self.history)
        self.assertTrue(v.exists())
        self.assertTrue(v.with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
