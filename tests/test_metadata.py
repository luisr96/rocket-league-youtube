"""Goal times and scorer labels for the <video>.json file. Run: python -m unittest"""
import json
import tempfile
import unittest
from pathlib import Path

from rlvid import metadata
from rlvid.search import Pair
from tests.test_naming import match

TARGET = "Player_Steam|111|0"


def goal(elapsed: float, team: int, scorer: str = "", scorer_id: str = "", speed: float = 90) -> dict:
    # wall = clock time; tests use elapsed + 1000 so wall and elapsed differ.
    return {"frame": int(elapsed * 30), "elapsed": elapsed, "wall": 1000 + elapsed, "team": team,
            "scorer": scorer, "scorer_id": scorer_id, "speed": speed}


class GoalsTest(unittest.TestCase):
    def setUp(self):
        self.m = match(1, ["nass", "Tom"], ["Al", "Bo"])  # target nass on blue
        self.m.focus_ids = {"nass": TARGET}

    def test_video_time_is_release_time_plus_clock_time_since_release(self):
        g = metadata.goals(self.m, [goal(40.0, 0, "nass", TARGET)], video_start=100.0, release_wall=1002.5)
        self.assertEqual(g[0]["video_time"], 137.5)
        self.assertEqual(g[0]["replay_time"], 40.0)

    def test_goals_before_release_are_dropped(self):
        self.assertEqual(metadata.goals(self.m, [goal(1.0, 0, "nass", TARGET)], 0, 1002.5), [])

    def test_scorer_labels(self):
        g = metadata.goals(self.m, [goal(10, 0, "nass", TARGET), goal(20, 0, "Tom", "Player_Steam|222|0"),
                                    goal(30, 1, "Al", "Player_Steam|333|0"), goal(40, 1)], 0, 1000)
        self.assertEqual([x["by"] for x in g], ["target", "teammate", "opponent", "unknown"])
        self.assertEqual([x["team"] for x in g], ["blue", "blue", "orange", "orange"])

    def test_target_matched_by_id_even_if_renamed_in_game(self):
        g = metadata.goals(self.m, [goal(10, 0, "nass (renamed)", TARGET)], 0, 1000)
        self.assertEqual(g[0]["by"], "target")

    def test_target_matched_by_name_without_focus_id(self):
        self.m.focus_ids = {}
        g = metadata.goals(self.m, [goal(10, 0, "NASS", "Player_Steam|999|0")], 0, 1000)
        self.assertEqual(g[0]["by"], "target")

    def test_target_on_orange(self):
        m = match(1, ["Al", "Bo"], ["nass", "Tom"])
        m.focus_ids = {"nass": TARGET}
        g = metadata.goals(m, [goal(10, 1, "Tom", "Player_Steam|222|0"), goal(20, 0, "Al", "x")], 0, 1000)
        self.assertEqual([x["by"] for x in g], ["teammate", "opponent"])


class WriteTest(unittest.TestCase):
    def test_file_next_to_video(self):
        m1, m2 = match(1, ["nass", "Tom"], ["Al", "Bo"]), match(2, ["nass", "Tom"], ["Cy", "Di"])
        m1.focus_ids = m2.focus_ids = {"nass": TARGET}
        games = [metadata.game(1, m1, (["nass", "Tom"], ["Al", "Bo"]), []),
                 metadata.game(2, m2, (["nass", "Tom"], ["Cy", "Di"]), [])]
        with tempfile.TemporaryDirectory() as d:
            video = Path(d) / "some video.mp4"
            path = metadata.write(video, Pair(m1, m2), games)
            self.assertEqual(path.name, "some video.json")
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["video"], "some video.mp4")
        self.assertEqual(data["games"][0]["overlay"], {"blue": ["nass", "Tom"], "orange": ["Al", "Bo"]})
        self.assertEqual(data["games"][1]["target"], {"name": "nass", "id": TARGET, "team": "blue"})


if __name__ == "__main__":
    unittest.main()
