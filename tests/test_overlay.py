"""Overlay sides and URL. Run: python -m unittest"""
import unittest
from urllib.parse import parse_qs, urlparse

from rlvid import overlay
from tests.test_naming import match

SETTINGS = {"style": "band", "hold_seconds": 4, "fade_out_seconds": 2, "fade_in_seconds": 0}


class SidesTest(unittest.TestCase):
    def test_camera_on_blue_is_first_on_the_left(self):
        m = match(1, ["Bravo", "nass"], ["Charlie", "Delta"])
        self.assertEqual(overlay.sides(m), (["nass", "Bravo"], ["Charlie", "Delta"]))

    def test_camera_on_orange_is_first_on_the_right(self):
        m = match(1, ["Charlie", "Delta"], ["Bravo", "nass"])
        self.assertEqual(overlay.sides(m), (["Charlie", "Delta"], ["nass", "Bravo"]))

    def test_3v3(self):
        m = match(1, ["Echo", "Bravo", "nass"], ["X", "Y", "Z"])
        self.assertEqual(overlay.sides(m), (["nass", "Echo", "Bravo"], ["X", "Y", "Z"]))


def game_player(name: str, team: int, pid: str = "") -> dict:
    return {"name": name, "team": team, "id": pid or f"Player_Steam|{name}|0"}


class InGameNamesTest(unittest.TestCase):
    def setUp(self):
        # ballchasing says "He belongs to backrooms"; the game shows "Joyo".
        self.m = match(1, ["He belongs to backrooms", "nass"], ["Charlie", "Delta"])
        self.m.focus_ids = {"nass": "Player_Steam|111|0"}
        self.players = [game_player("Joyo", 0), game_player("nass", 0, "Player_Steam|111|0"),
                        game_player("Charlie", 1), game_player("Delta", 1)]

    def test_uses_in_game_names_with_camera_first(self):
        self.assertEqual(overlay.sides(self.m, self.players), (["nass", "Joyo"], ["Charlie", "Delta"]))

    def test_camera_found_by_id_even_if_renamed_in_game(self):
        self.players[1]["name"] = "nass (renamed)"
        self.assertEqual(overlay.sides(self.m, self.players)[0], ["nass (renamed)", "Joyo"])

    def test_camera_found_by_name_without_focus_id(self):
        self.m.focus_ids = {}
        self.assertEqual(overlay.sides(self.m, self.players)[0], ["nass", "Joyo"])

    def test_falls_back_to_ballchasing_names_when_team_sizes_differ(self):
        self.assertEqual(overlay.sides(self.m, self.players[:3]),
                         (["nass", "He belongs to backrooms"], ["Charlie", "Delta"]))

    def test_falls_back_without_players(self):
        self.assertEqual(overlay.sides(self.m, None), (["nass", "He belongs to backrooms"], ["Charlie", "Delta"]))
        self.assertEqual(overlay.sides(self.m, []), (["nass", "He belongs to backrooms"], ["Charlie", "Delta"]))

    def test_url_uses_in_game_names(self):
        q = parse_qs(urlparse(overlay.url(SETTINGS, self.m, self.players)).query)
        self.assertEqual(q["blue"], ["nass", "Joyo"])


class UrlTest(unittest.TestCase):
    def parse(self, u: str) -> dict:
        p = urlparse(u)
        self.assertEqual(p.scheme, "file")
        self.assertTrue(p.path.endswith("/overlay/overlay.html"))
        return parse_qs(p.query)

    def test_names_and_settings(self):
        q = self.parse(overlay.url(SETTINGS, match(1, ["Charlie", "Delta"], ["Bravo", "nass"])))
        self.assertEqual(q["blue"], ["Charlie", "Delta"])
        self.assertEqual(q["orange"], ["nass", "Bravo"])
        self.assertEqual((q["style"], q["hold"], q["fade"], q["fadein"]), (["band"], ["4"], ["2"], ["0"]))

    def test_special_characters_survive(self):
        q = self.parse(overlay.url(SETTINGS, match(1, ["nass"], ["a&b=c #1+ü"])))
        self.assertEqual(q["orange"], ["a&b=c #1+ü"])

    def test_empty_overlay_has_no_names(self):
        q = self.parse(overlay.url(SETTINGS))
        self.assertNotIn("blue", q)
        self.assertNotIn("orange", q)


if __name__ == "__main__":
    unittest.main()
