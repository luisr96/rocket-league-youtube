"""Goal choice and page URL for thumbnails. Run: python -m unittest"""
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rlvid import thumbnail


def g(t: float, by: str, speed: float) -> dict:
    return {"video_time": t, "by": by, "speed": speed, "team": "blue", "scorer": by}


def data(game1: list[dict], game2: list[dict]) -> dict:
    game = {"target": {"name": "nass", "team": "orange"},
            "overlay": {"blue": ["Al", "Bo"], "orange": ["nass (in game)", "Tom"]}}
    return {"games": [dict(game, goals=game1), dict(game, goals=game2)]}


class ChooseGoalTest(unittest.TestCase):
    def test_targets_fastest_goal_in_game_1(self):
        goal, why = thumbnail.choose_goal(data([g(10, "target", 80), g(20, "target", 120), g(30, "teammate", 150)],
                                               [g(400, "target", 200)]))
        self.assertEqual((goal["video_time"], why), (20, "target's goal, game 1"))

    def test_teammate_in_game_1_before_target_in_game_2(self):
        goal, why = thumbnail.choose_goal(data([g(10, "teammate", 80), g(20, "opponent", 150)],
                                               [g(400, "target", 200)]))
        self.assertEqual((goal["video_time"], why), (10, "teammate's goal, game 1"))

    def test_target_in_game_2(self):
        goal, why = thumbnail.choose_goal(data([g(20, "opponent", 150)], [g(400, "teammate", 90), g(420, "target", 70)]))
        self.assertEqual((goal["video_time"], why), (420, "target's goal, game 2"))

    def test_nothing_usable(self):
        self.assertIsNone(thumbnail.choose_goal(data([g(20, "opponent", 150)], [g(400, "teammate", 90)])))
        self.assertIsNone(thumbnail.choose_goal({"games": []}))


class PageUrlTest(unittest.TestCase):
    def q(self, settings: dict) -> dict:
        return parse_qs(urlparse(thumbnail.page_url(settings, Path(r"C:\frames\a b.png"), data([], []))).query)

    def test_game_1_names_and_in_game_target_name(self):
        q = self.q({})
        self.assertEqual(q["blue"], ["Al", "Bo"])
        self.assertEqual(q["orange"], ["nass (in game)", "Tom"])
        self.assertEqual(q["big"], ["nass (in game)"])
        self.assertEqual(q["bg"], ["file:///C:/frames/a%20b.png"])

    def test_defaults_and_settings(self):
        self.assertEqual((self.q({})["size"], self.q({})["pos"], self.q({})["zoom"], self.q({})["pop"]),
                         (["70"], ["bottom"], ["1"], ["1"]))
        q = self.q({"big_name": False, "zoom": False, "colours": False, "names_size": 90, "names_position": "top"})
        self.assertNotIn("big", q)  # empty value
        self.assertEqual((q["zoom"], q["pop"], q["size"], q["pos"]), (["0"], ["0"], ["90"], ["top"]))


if __name__ == "__main__":
    unittest.main()
