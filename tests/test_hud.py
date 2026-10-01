"""HUD state (plugin data + POV, stats, label, intro) and the local server. Run: python -m unittest"""
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from rlvid.hudserver import HudServer, build_state

PLUGIN = {"in_replay": True, "score": {"blue": 1, "orange": 2}, "clock": 245, "overtime": False, "players": [
    {"name": "TempoH", "id": "Player_Steam|1|0", "team": 0, "boost": 40, "score": 100, "goals": 1, "assists": 0, "saves": 1, "shots": 2},
    {"name": "Atow", "id": "Player_Steam|9|0", "team": 1, "boost": -1, "score": 345, "goals": 2, "assists": 1, "saves": 3, "shots": 4},
]}


class BuildStateTest(unittest.TestCase):
    def test_pov_by_id_and_stats(self):
        s = build_state(json.loads(json.dumps(PLUGIN)), {"pov_id": "Player_Steam|9|0", "pov_name": "x", "label": "SSL 2v2"},
                        None, 0)
        self.assertEqual([p["pov"] for p in s["players"]], [False, True])
        self.assertEqual(s["stats"], {"name": "Atow", "score": 345, "goals": 2, "assists": 1, "saves": 3, "shots": 4})
        self.assertEqual((s["label"], s["clock"], s["score"]), ("SSL 2v2", 245, {"blue": 1, "orange": 2}))

    def test_pov_by_name_without_id(self):
        s = build_state(json.loads(json.dumps(PLUGIN)), {"pov_id": None, "pov_name": "atow"}, None, 0)
        self.assertEqual(s["stats"]["name"], "Atow")

    def test_not_in_replay(self):
        s = build_state({"in_replay": False}, {"label": "SSL 2v2"}, None, 0)
        self.assertEqual(s, {"in_replay": False, "label": "SSL 2v2"})

    def test_intro_is_fresh_only_briefly(self):
        intro = {"id": "1-5", "match": 1, "pov": "ATOW", "blue": ["A"], "orange": ["B"]}
        self.assertTrue(build_state({}, {}, intro, 100, now=101)["intro"]["fresh"])
        self.assertFalse(build_state({}, {}, intro, 100, now=105)["intro"]["fresh"])


class ServerTest(unittest.TestCase):
    def test_serves_state_and_page(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "rlvid_hud.json"
            f.write_text(json.dumps(dict(PLUGIN, time=time.time())), encoding="utf-8")
            hud = HudServer(port=0, data_file=f)  # port 0: any free port
            hud.start()
            try:
                port = hud._httpd.server_address[1]
                hud.set_game("Player_Steam|9|0", "Atow", "SSL 2v2")
                hud.show_intro(1, "ATOW", ["TempoH"], ["Atow"])
                st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state").read())
                self.assertTrue(st["in_replay"])
                self.assertEqual(st["stats"]["goals"], 2)
                self.assertEqual(st["intro"]["blue"], ["TempoH"])
                page = urllib.request.urlopen(f"http://127.0.0.1:{port}/hud.html").read()
                self.assertIn(b"render(state)", page)
                f.write_text("{not json", encoding="utf-8")  # read mid-replace: keep the last good data
                self.assertTrue(json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state").read())["in_replay"])
                f.unlink()
                self.assertTrue(json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state").read())["in_replay"])
                f.write_text(json.dumps(dict(PLUGIN, time=time.time() - 10)), encoding="utf-8")  # stale data
                self.assertFalse(json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state").read())["in_replay"])
            finally:
                hud.stop()


if __name__ == "__main__":
    unittest.main()
