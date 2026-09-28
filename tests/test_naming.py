"""Teammate/opponent grouping and video file names. Run: python -m unittest"""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from rlvid import app
from rlvid.search import Match, Pair

PLAYLISTS = {1: "ranked-duels", 2: "ranked-doubles", 3: "ranked-standard"}


def match(hour: int, blue: list[str], orange: list[str], camera: str = "nass") -> Match:
    return Match(id=f"id{hour}", date=datetime(2026, 9, 27, hour), playlist=PLAYLISTS[len(blue)],
                 map="map", duration=300, blue_players=blue, orange_players=orange,
                 blue_goals=1, orange_goals=0, featured=[camera], listed=[camera], focus_ids={})


class FakeConfig:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir

    def path(self, key: str) -> Path:
        assert key == "output_dir"
        return self.out_dir


class MatchSidesTest(unittest.TestCase):
    def test_camera_on_blue(self):
        m = match(1, ["nass", "Tom"], ["Al", "Bo"])
        self.assertEqual(m.teammates, ["Tom"])
        self.assertEqual(m.opponents, ["Al", "Bo"])

    def test_camera_on_orange(self):
        m = match(1, ["Al", "Bo"], ["Tom", "nass"])
        self.assertEqual(m.teammates, ["Tom"])
        self.assertEqual(m.opponents, ["Al", "Bo"])

    def test_1v1_has_no_teammates(self):
        self.assertEqual(match(1, ["nass"], ["Zed"]).teammates, [])


class PairGroupsTest(unittest.TestCase):
    def test_different_people_give_one_group_per_game_in_game_order(self):
        p = Pair(match(1, ["nass", "Tom"], ["Zed", "Yan"]), match(2, ["nass", "Tom"], ["Cy", "Al"]))
        self.assertEqual(p.opponents, [["Yan", "Zed"], ["Al", "Cy"]])

    def test_same_people_are_repeated(self):
        p = Pair(match(1, ["nass", "Tom"], ["Bo", "Al"]), match(2, ["nass", "Tom"], ["Al", "Bo"]))
        self.assertEqual(p.teammates, [["Tom"], ["Tom"]])
        self.assertEqual(p.opponents, [["Al", "Bo"], ["Al", "Bo"]])

    def test_sorting_ignores_case(self):
        p = Pair(match(1, ["nass"], ["bo"]), match(2, ["nass", "Tom"], ["bo", "Al"]))
        self.assertEqual(p.opponents[1], ["Al", "bo"])


class VideoPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = FakeConfig(Path(self.tmp.name))
        self.today = f"{date.today():%Y-%m-%d}"

    def tearDown(self):
        self.tmp.cleanup()

    def name(self, pair: Pair) -> str:
        return app.video_path(self.cfg, pair).name

    def test_1v1_leaves_out_teammates(self):
        p = Pair(match(1, ["nass"], ["Zed"]), match(2, ["nass"], ["Amy"]))
        self.assertEqual(self.name(p), f"{self.today}_nass_1v1_2026-09-27_60m_(Zed)(Amy).mp4")

    def test_2v2(self):
        p = Pair(match(1, ["nass", "Tom"], ["Bo", "Al"]), match(3, ["Cy", "Di"], ["Tom", "nass"]))
        self.assertEqual(self.name(p), f"{self.today}_nass_2v2_2026-09-27_2h_(Tom)(Tom)_(Al+Bo)(Cy+Di).mp4")

    def test_3v3_teammate_changed_same_opponents(self):
        p = Pair(match(1, ["nass", "Tom", "Ann"], ["X", "W", "V"]),
                 match(2, ["nass", "Kim", "Ann"], ["V", "W", "X"]))
        self.assertEqual(self.name(p),
                         f"{self.today}_nass_3v3_2026-09-27_60m_(Ann+Tom)(Ann+Kim)_(V+W+X)(V+W+X).mp4")

    def test_1v1_same_opponent_is_repeated(self):
        p = Pair(match(1, ["nass"], ["Zed"]), match(2, ["nass"], ["Zed"]))
        self.assertEqual(self.name(p), f"{self.today}_nass_1v1_2026-09-27_60m_(Zed)(Zed).mp4")

    def test_2v2_same_teammate_same_opponents_are_repeated(self):
        p = Pair(match(1, ["nass", "Tom"], ["Bo", "Al"]), match(2, ["nass", "Tom"], ["Al", "Bo"]))
        self.assertEqual(self.name(p), f"{self.today}_nass_2v2_2026-09-27_60m_(Tom)(Tom)_(Al+Bo)(Al+Bo).mp4")

    def test_2v2_different_teammates(self):
        p = Pair(match(1, ["nass", "Tom"], ["Bo", "Al"]), match(4, ["nass", "Kim"], ["Cy", "Di"]))
        self.assertEqual(self.name(p), f"{self.today}_nass_2v2_2026-09-27_3h_(Tom)(Kim)_(Al+Bo)(Cy+Di).mp4")

    def test_3v3_same_teammates_different_opponents(self):
        p = Pair(match(1, ["nass", "Tom", "Ann"], ["X", "W", "V"]),
                 match(2, ["nass", "Tom", "Ann"], ["R", "S", "T"]))
        self.assertEqual(self.name(p),
                         f"{self.today}_nass_3v3_2026-09-27_60m_(Ann+Tom)(Ann+Tom)_(V+W+X)(R+S+T).mp4")

    def test_unsafe_characters_in_names_are_replaced(self):
        p = Pair(match(1, ["nass"], ['a (b)+c:d']), match(2, ["nass"], ["x/y z"]))
        self.assertEqual(self.name(p), f"{self.today}_nass_1v1_2026-09-27_60m_(a-b-c-d)(x-y-z).mp4")

    def test_taken_name_gets_a_number(self):
        p = Pair(match(1, ["nass"], ["Zed"]), match(2, ["nass"], ["Amy"]))
        first = app.video_path(self.cfg, p)
        first.touch()
        self.assertEqual(app.video_path(self.cfg, p).name, first.stem + "_2.mp4")


class GapTest(unittest.TestCase):
    def gap(self, hours: float) -> str:
        a = match(0, ["nass"], ["Zed"])
        b = match(0, ["nass"], ["Amy"])
        b.date = a.date + (datetime(2026, 1, 1, 1) - datetime(2026, 1, 1)) * hours
        return app._fmt_gap(Pair(a, b))

    def test_minutes_below_two_hours(self):
        self.assertEqual(self.gap(0.5), "30m")
        self.assertEqual(self.gap(1.99), "119m")

    def test_hours_below_two_days(self):
        self.assertEqual(self.gap(2), "2h")
        self.assertEqual(self.gap(47.9), "47h")

    def test_days(self):
        self.assertEqual(self.gap(72), "3d")


if __name__ == "__main__":
    unittest.main()
