"""S2-EC34: Aspect-Ratio Aware Tiled Slicer."""

from __future__ import annotations

from itertools import pairwise

from PIL import Image

from ai_pipeline.vision import tile_long_screenshot


def test_s2_ec34_long_screenshot_tiles_keep_1080_width_and_15pct_overlap() -> None:
    img = Image.new("RGB", (1080, 12000), (255, 255, 255))
    tiles = tile_long_screenshot(img)
    assert len(tiles) > 1
    assert all(t.image.width == 1080 and t.image.height <= 1800 for t in tiles)
    assert tiles[0].y0 == 0 and tiles[-1].y1 == 12000
    for a, b in pairwise(tiles):
        assert a.y1 - b.y0 >= int(1800 * 0.15)  # chồng lấp ≥ 15%
        assert b.y0 > a.y0


def test_s2_ec34_wide_long_image_scaled_to_1080_not_to_1280_height() -> None:
    img = Image.new("RGB", (1440, 9000), (255, 255, 255))
    tiles = tile_long_screenshot(img)
    assert {t.image.width for t in tiles} == {1080}
    assert tiles[-1].y1 == 6750  # 9000 * 1080/1440


def test_s2_ec34_normal_screenshot_not_sliced() -> None:
    img = Image.new("RGB", (1080, 2400), (255, 255, 255))  # tỉ lệ 2.22 ≤ 2.5
    tiles = tile_long_screenshot(img)
    assert len(tiles) == 1 and tiles[0].image.size == (1080, 2400)


def test_s2_ec34_last_tile_is_full_height_and_covers_end() -> None:
    img = Image.new("RGB", (1080, 4000), (255, 255, 255))
    tiles = tile_long_screenshot(img)
    assert tiles[-1].image.height == 1800 and tiles[-1].y1 == 4000
