"""Titles, descriptions, tags and the upload flow (YouTube replaced by a fake). Run: python -m unittest"""
import json
import os
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

    def test_title_uses_in_game_name_in_capitals_and_rotates(self):
        self.assertEqual(describe.title(data(), 0, self.TITLES), "ATOW is OVERPOWERED in Rocket League! (SSL 2v2)")
        titles = {describe.title(data(), i, self.TITLES) for i in range(len(self.TITLES))}
        self.assertEqual(len(titles), len(self.TITLES))
        self.assertEqual(describe.title(data(), len(self.TITLES), self.TITLES), describe.title(data(), 0, self.TITLES))

    def test_title_without_rank(self):
        self.assertEqual(describe.title(data(rank=""), 0, self.TITLES), "ATOW is OVERPOWERED in Rocket League! (2v2)")
        for i in range(len(self.TITLES)):
            t = describe.title(data(rank=""), i, self.TITLES)
            self.assertNotIn("  ", t)
            self.assertNotIn("( ", t)

    def test_titles_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "titles.txt"
            f.write_text("# comment\n\n{PLAYER} wins in {MODE}\n{PLAYR} typo line\n", encoding="utf-8")
            self.assertEqual(describe.load_titles(f), ["{PLAYER} wins in {MODE}"])
            f.write_text("# only comments\n", encoding="utf-8")
            self.assertEqual(describe.load_titles(f), describe.DEFAULT_TITLES)
        self.assertEqual(describe.load_titles(Path("missing.txt")), describe.DEFAULT_TITLES)
        self.assertEqual(len(self.TITLES), 5)

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
            uploader.upload_one(object(), v, {}, self.history, 0)
        title = up.call_args.args[2]
        self.assertEqual(title, "ATOW is OVERPOWERED in Rocket League! (SSL 2v2)")
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
            self.assertEqual(uploader.upload_one(object(), v, {}, self.history, 0), "VID")
        self.assertFalse(v.exists())

    def test_failed_upload_keeps_files(self):
        v = self.video("a")
        with mock.patch.object(youtube, "upload", side_effect=youtube.YouTubeError("boom")):
            with self.assertRaises(youtube.YouTubeError):
                uploader.upload_one(object(), v, {}, self.history, 0)
        self.assertTrue(v.exists())
        self.assertTrue(v.with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
