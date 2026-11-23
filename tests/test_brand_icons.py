"""The brand icons are rendered from the Talk2View logomark's own path
(``scripts/generate_brand_icons.py``), and the files in the repo are that rendering."""

import os
import struct
import zlib

import generate_brand_icons as icons
from conftest import REPO

TEAL = (0x26, 0xC8, 0xB8)


def decode(png):
    """``(width, height, rows)`` of an 8-bit RGBA PNG; rows are lists of (r, g, b, a)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    data = b""
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + length]
        if kind == b"IDAT":
            data += body
        else:
            chunks[kind] = body
        pos += 12 + length
    width, height, depth, color = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, color) == (8, 6)
    raw = zlib.decompress(data)
    stride = 1 + 4 * width
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0  # no filter
        rows.append([tuple(line[1 + 4 * x:5 + 4 * x]) for x in range(width)])
    return width, height, rows


def test_the_app_icon_is_a_white_mark_on_a_teal_rounded_square():
    width, height, rows = decode(icons.app_icon(128))
    assert (width, height) == (128, 128)
    assert rows[0][0][3] == 0  # a rounded corner: transparent
    assert rows[64][4] == TEAL + (255,)  # the left edge, mid-height: tile
    assert rows[55][64] == TEAL + (255,)  # inside the bubble's counter: tile shows through
    white = [p for row in rows for p in row if p == (255, 255, 255, 255)]
    assert len(white) > 600  # the mark


def test_the_logomark_is_teal_on_transparent_at_the_marks_proportions():
    width, height, rows = decode(icons.logomark(112))
    assert (width, height) == (112, 82)
    assert rows[5][5] == TEAL + (255,)  # the bubble's frame
    assert rows[30][56][3] == 0  # its counter is see-through
    assert rows[81][0][3] == 0  # beside the tail
    assert rows[81][56] == TEAL + (255,)  # the tail's point


def test_the_repo_icons_are_the_rendered_ones():
    for relative, png in icons.outputs().items():
        with open(os.path.join(REPO, relative), "rb") as fh:
            assert fh.read() == png, relative + " is stale: run scripts/generate_brand_icons.py"
