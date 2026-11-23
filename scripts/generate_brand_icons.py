#!/usr/bin/env python3
"""Render SlicerFlow's icons from the Talk2View logomark (the V-mark speech bubble).

The geometry is the logomark's own SVG path (the brand's
``Talk2View_Logomark_OnWhite.svg``, a 112 x 82 box), and the
colours are the brand's, so the icons follow the brand book:

- the app icon (``SlicerFlow.png`` and the module's ``Resources/Icons/SlicerFlow.png``):
  a white mark on a Teal rounded square, as the brand book's app icon (p.13);
- the in-app mark (``Resources/Icons/Talk2ViewLogomark.png``): the Teal mark on
  transparent, never the icon tile.

Standard library only: the polygons are filled scanline by scanline, 4 x 4 samples a
pixel, and written as 8-bit RGBA PNGs.

    python3 scripts/generate_brand_icons.py           write the icons
    python3 scripts/generate_brand_icons.py --check   exit 1 if a file differs
"""

import os
import struct
import sys
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEAL = (0x26, 0xC8, 0xB8)
WHITE = (0xFF, 0xFF, 0xFF)
#: The logomark's path: the bubble with its tail, and the counter with the V cut into it.
MARK_W, MARK_H = 112, 82
OUTER = [(112, 59), (76, 59), (69, 82), (43, 82), (36, 59), (0, 59), (0, 0), (112, 0)]
INNER = [(11, 11), (11, 48), (45, 48), (56, 80), (67, 48), (101, 48), (101, 11)]
#: The app icon: the tile's corner radius, and the mark's width, as fractions of its side.
TILE_RADIUS = 0.225
MARK_WIDTH = 0.625
SAMPLES = 4


def _crossings(polygon, y):
    """The x where ``polygon``'s edges cross the horizontal line at ``y``."""
    xs = []
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 <= y < y2) or (y2 <= y < y1):
            xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    return xs


def _spans(polygons, y):
    """The even-odd inside spans of ``polygons`` (together) on the line at ``y``."""
    xs = sorted(x for polygon in polygons for x in _crossings(polygon, y))
    return list(zip(xs[0::2], xs[1::2]))


def _coverage(width, height, spans_at):
    """Per-pixel coverage, 0..1, of the shape whose spans ``spans_at(y)`` gives."""
    cover = [[0.0] * width for _ in range(height)]
    weight = 1.0 / SAMPLES
    for row in range(height):
        line = cover[row]
        for sub in range(SAMPLES):
            for x0, x1 in spans_at(row + (sub + 0.5) / SAMPLES):
                x0, x1 = max(x0, 0.0), min(x1, float(width))
                for x in range(int(x0), min(int(x1) + 1, width)):
                    overlap = min(x1, x + 1) - max(x0, x)
                    if overlap > 0:
                        line[x] += overlap * weight
    return cover


def _mark_spans(scale, left, top):
    shapes = [[(left + x * scale, top + y * scale) for x, y in polygon] for polygon in (OUTER, INNER)]
    return lambda y: _spans(shapes, y)


def _rounded_square_spans(size, radius):
    def spans(y):
        if y < radius:
            dy = radius - y
        elif y > size - radius:
            dy = y - (size - radius)
        else:
            return [(0.0, float(size))]
        inset = radius - (max(radius * radius - dy * dy, 0.0)) ** 0.5
        return [(inset, size - inset)]
    return spans


def _png(width, height, pixels):
    """An 8-bit RGBA PNG of ``pixels`` (rows of (r, g, b, a))."""
    raw = b"".join(b"\x00" + bytes(c for pixel in row for c in pixel) for row in pixels)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _alpha(value):
    return int(round(min(max(value, 0.0), 1.0) * 255))


def app_icon(size):
    """The white mark on a Teal rounded square, ``size`` pixels a side."""
    tile = _coverage(size, size, _rounded_square_spans(size, size * TILE_RADIUS))
    scale = size * MARK_WIDTH / MARK_W
    mark = _coverage(size, size, _mark_spans(scale, (size - MARK_W * scale) / 2,
                                              (size - MARK_H * scale) / 2))
    pixels = []
    for tile_row, mark_row in zip(tile, mark):
        row = []
        for t, m in zip(tile_row, mark_row):
            m = min(m, 1.0)
            color = tuple(int(round(w * m + c * (1 - m))) for w, c in zip(WHITE, TEAL))
            row.append(color + (_alpha(t),))
        pixels.append(row)
    return _png(size, size, pixels)


def logomark(width):
    """The Teal mark on transparent, ``width`` pixels wide at the mark's proportions."""
    scale = width / MARK_W
    height = int(round(MARK_H * scale))
    mark = _coverage(width, height, _mark_spans(scale, 0.0, 0.0))
    return _png(width, height, [[TEAL + (_alpha(m),) for m in row] for row in mark])


def outputs():
    """``{path relative to the repo: PNG bytes}`` for every icon this script owns."""
    tile = app_icon(128)
    return {
        "SlicerFlow.png": tile,
        "SlicerFlow/Resources/Icons/SlicerFlow.png": tile,
        # Four times the header's 40 px width: sharp on a high-DPI screen.
        "SlicerFlow/Resources/Icons/Talk2ViewLogomark.png": logomark(160),
    }


def main(argv):
    stale = []
    for relative, png in outputs().items():
        path = os.path.join(REPO, relative)
        current = open(path, "rb").read() if os.path.exists(path) else None
        if current == png:
            continue
        if "--check" in argv:
            stale.append(relative)
        else:
            with open(path, "wb") as fh:
                fh.write(png)
            print("wrote", relative)
    for relative in stale:
        print("stale:", relative)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
