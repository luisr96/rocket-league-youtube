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
