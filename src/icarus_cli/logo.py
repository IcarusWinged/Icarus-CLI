"""The ICARUS logo: an SVG that is rasterised to terminal half-blocks at whatever width fits."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

SVG_PATH = Path(__file__).with_name("logo.svg")
MAX_COLS = 110
_VIEWBOX = re.compile(r'viewBox="0 0 (\d+) (\d+)"')
_RECT = re.compile(r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)" fill="#([0-9a-fA-F]{6})"')

Pixel = tuple[int, int, int] | None


@lru_cache(maxsize=1)
def _load() -> tuple[int, int, list[list[Pixel]]]:
    svg = SVG_PATH.read_text(encoding="utf-8")
    width, height = map(int, _VIEWBOX.search(svg).groups())
    grid: list[list[Pixel]] = [[None] * width for _ in range(height)]
    for x, y, w, h, hexcolor in _RECT.findall(svg):
        rgb = tuple(int(hexcolor[i : i + 2], 16) for i in (0, 2, 4))
        for yy in range(int(y), min(int(y) + int(h), height)):
            for xx in range(int(x), min(int(x) + int(w), width)):
                grid[yy][xx] = rgb
    return width, height, grid


def _sample(grid: list[list[Pixel]], x0: float, x1: float, y0: float, y1: float) -> Pixel:
    """Average the opaque pixels inside a box; transparent if most of it is empty."""
    xs = range(int(x0), max(int(x0) + 1, int(x1 + 0.999)))
    ys = range(int(y0), max(int(y0) + 1, int(y1 + 0.999)))
    hits = [grid[y][x] for y in ys for x in xs if y < len(grid) and x < len(grid[0]) and grid[y][x]]
    total = len(xs) * len(ys)
    if not hits or len(hits) * 2 < total:
        return None
    return tuple(sum(c[i] for c in hits) // len(hits) for i in range(3))


@lru_cache(maxsize=16)
def render(cols: int) -> Text:
    """Rasterise the SVG to `cols` terminal columns (two pixel rows per text row)."""
    src_w, src_h, grid = _load()
    cols = max(8, min(cols, MAX_COLS))
    scale = src_w / cols
    pix_rows = max(2, round(src_h / scale))
    pix_rows += pix_rows % 2
    shades: list[list[Pixel]] = [
        [_sample(grid, c * scale, (c + 1) * scale, r * scale, (r + 1) * scale) for c in range(cols)]
        for r in range(pix_rows)
    ]
    text = Text(justify="center", no_wrap=True, overflow="crop")
    for r in range(0, pix_rows, 2):
        for top, bottom in zip(shades[r], shades[r + 1]):
            if top and bottom:
                text.append("▀", Style(color="#%02x%02x%02x" % top, bgcolor="#%02x%02x%02x" % bottom))
            elif top:
                text.append("▀", Style(color="#%02x%02x%02x" % top))
            elif bottom:
                text.append("▄", Style(color="#%02x%02x%02x" % bottom))
            else:
                text.append(" ")
        if r + 2 < pix_rows:
            text.append("\n")
    return text


class Logo(Static):
    """Scales itself to the width it is given."""

    DEFAULT_CSS = "Logo { width: 100%; height: auto; text-align: center; }"

    def on_resize(self, event) -> None:
        self.update(render(max(8, event.size.width - 2)))
