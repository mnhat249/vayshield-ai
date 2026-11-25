"""S2-EC34: Aspect-Ratio Aware Tiled Slicer cho ảnh cuộn dài."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

TILE_WIDTH = 1080
TILE_HEIGHT = 1800
OVERLAP_RATIO = 0.15
LONG_RATIO = 2.5


@dataclass(frozen=True)
class Tile:
    index: int
    y0: int
    y1: int
    image: Image.Image


def tile_long_screenshot(
    image: Image.Image,
    *,
    tile_width: int = TILE_WIDTH,
    tile_height: int = TILE_HEIGHT,
    overlap: float = OVERLAP_RATIO,
    ratio_threshold: float = LONG_RATIO,
) -> list[Tile]:
    """Nếu height/width > 2.5: giữ chiều ngang chuẩn 1080px, cắt lát cao 1800px chồng 15%.

    Ảnh rộng hơn 1080px được thu về đúng 1080px (giữ tỉ lệ); ảnh hẹp hơn giữ nguyên.
    Ảnh không dài → 1 lát duy nhất (không cắt).
    """
    if not 0 <= overlap < 1:
        raise ValueError("overlap phải trong [0, 1)")
    img = image
    if img.width > tile_width:
        new_h = max(1, round(img.height * tile_width / img.width))
        img = img.resize((tile_width, new_h), Image.Resampling.LANCZOS)
    if img.height / img.width <= ratio_threshold or img.height <= tile_height:
        return [Tile(0, 0, img.height, img)]

    step = tile_height - int(tile_height * overlap)
    tiles: list[Tile] = []
    y0 = 0
    while True:
        y1 = min(y0 + tile_height, img.height)
        if y1 == img.height:
            y0 = max(0, y1 - tile_height)  # lát cuối luôn đủ cao, chồng ≥ 15%
        tiles.append(Tile(len(tiles), y0, y1, img.crop((0, y0, img.width, y1))))
        if y1 == img.height:
            break
        y0 += step
    return tiles
