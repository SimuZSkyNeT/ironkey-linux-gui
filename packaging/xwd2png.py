#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""
Convert an X11 window dump (xwd) to PNG.

Written because `xwd` is often the only capture tool present on a minimal
desktop, while Pillow cannot read its format. Handles the common case:
X11WDFileVersion 7, ZPixmap, 24- or 32-bit TrueColor — which is what a
modern X server produces.

    xwd -id <window> -out shot.xwd
    python3 xwd2png.py shot.xwd shot.png
"""

import struct
import sys

from PIL import Image

# The header is 100 bytes of big-endian 32-bit fields.
FIELDS = [
    "header_size", "file_version", "pixmap_format", "pixmap_depth",
    "pixmap_width", "pixmap_height", "x_offset", "byte_order",
    "bitmap_unit", "bitmap_bit_order", "bitmap_pad", "bits_per_pixel",
    "bytes_per_line", "visual_class", "red_mask", "green_mask",
    "blue_mask", "bits_per_rgb", "colormap_entries", "ncolors",
    "window_width", "window_height", "window_x", "window_y",
    "window_bdrwidth",
]


def convert(src, dst):
    with open(src, "rb") as f:
        raw = f.read()

    values = struct.unpack(">25I", raw[:100])
    h = dict(zip(FIELDS, values))

    if h["file_version"] != 7:
        raise SystemExit(f"Unsupported xwd version {h['file_version']}")
    if h["pixmap_format"] != 2:
        raise SystemExit("Only ZPixmap dumps are supported")

    width, height = h["pixmap_width"], h["pixmap_height"]
    bpp, stride = h["bits_per_pixel"], h["bytes_per_line"]

    # Header, then the window name, then the colour map, then the pixels.
    offset = h["header_size"] + h["ncolors"] * 12
    pixels = raw[offset:]

    if bpp not in (24, 32):
        raise SystemExit(f"Unsupported depth: {bpp} bits per pixel")

    # Work out channel order from the masks rather than assuming BGRA.
    masks = (h["red_mask"], h["green_mask"], h["blue_mask"])
    shifts = []
    for m in masks:
        s = 0
        while m and not (m >> s) & 0xFF:
            s += 8
        shifts.append(s // 8)

    out = bytearray(width * height * 3)
    step = bpp // 8
    for y in range(height):
        row = pixels[y * stride:(y + 1) * stride]
        base = y * width * 3
        for x in range(width):
            px = row[x * step:x * step + step]
            if len(px) < step:
                break
            if h["byte_order"] == 0:          # little-endian pixel
                px = px[::-1]
                idx = [step - 1 - s for s in shifts]
            else:
                idx = [step - 1 - s for s in shifts]
            o = base + x * 3
            out[o] = px[idx[0]]
            out[o + 1] = px[idx[1]]
            out[o + 2] = px[idx[2]]

    img = Image.frombytes("RGB", (width, height), bytes(out))
    img.save(dst)
    return width, height


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    w, h = convert(sys.argv[1], sys.argv[2])
    print(f"{sys.argv[2]}  {w}x{h}")
