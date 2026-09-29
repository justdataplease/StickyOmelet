from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class IconTests(unittest.TestCase):
    def test_icon_contains_windows_taskbar_sizes(self) -> None:
        with Image.open(ROOT / "assets" / "stickyomelet.ico") as icon:
            sizes = set(icon.info.get("sizes", set()))
        self.assertTrue({(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)}.issubset(sizes))

    def test_small_taskbar_icon_has_transparent_corners_and_visible_dot(self) -> None:
        with Image.open(ROOT / "assets" / "stickyomelet.ico") as icon:
            small = icon.ico.getimage((32, 32)).convert("RGBA")
        corners = (small.getpixel((0, 0)), small.getpixel((31, 0)), small.getpixel((0, 31)), small.getpixel((31, 31)))
        self.assertTrue(all(pixel[3] <= 2 for pixel in corners))
        visible_pixels = sum(1 for pixel in small.get_flattened_data() if pixel[3] > 32)
        self.assertGreater(visible_pixels, 90)
        self.assertLess(visible_pixels, 700)

    def test_shared_header_mark_is_high_resolution_rgba_art(self) -> None:
        with Image.open(ROOT / "assets" / "dot-mark.png") as mark:
            self.assertEqual((22, 22), mark.size)
            self.assertEqual("RGBA", mark.mode)
            self.assertEqual(0, mark.getpixel((0, 0))[3])

    def test_bubble_art_is_a_clean_transparent_circle(self) -> None:
        with Image.open(ROOT / "assets" / "dot-bubble.png") as bubble:
            self.assertEqual((64, 64), bubble.size)
            self.assertEqual("RGBA", bubble.mode)
            self.assertTrue(all(bubble.getpixel(point)[3] == 0 for point in ((0, 0), (63, 0), (0, 63), (63, 63))))
            self.assertTrue(all(channel >= 254 for channel in bubble.getpixel((32, 10))))
