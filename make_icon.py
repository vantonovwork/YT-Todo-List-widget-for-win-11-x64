"""Рисует иконку app.ico (квадрат с градиентом в духе YouTrack и буквами «YT») без сторонних библиотек."""
import math
import os
import struct
import sys
import zlib


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def render(size):
    """PNG-картинка иконки размером size×size."""
    raw = b"".join(b"\0" + row for row in render_rgba(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# Буквы «YT» отрезками в координатах 256×256 (рисуются толстой линией со скруглёнными концами)
LETTERS = [
    ((60, 72), (96, 124)), ((132, 72), (96, 124)), ((96, 124), (96, 186)),   # Y
    ((140, 76), (210, 76)), ((175, 76), (175, 186)),                         # T
]
# Градиент в духе YouTrack: розовый → фиолетовый → голубой (из левого верхнего угла в правый нижний)
STOPS = [(0.0, (255, 45, 138)), (0.5, (124, 77, 255)), (1.0, (22, 196, 255))]


def _gradient(t):
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * k for a, b in zip(c0, c1))
    return STOPS[-1][1]


def render_rgba(size):
    """Пиксели иконки: список строк, в каждой size×(R,G,B,A) байт (для значка в трее)."""
    s = size / 256.0
    small = size <= 24
    pad = 0.5 if small else 8 * s               # на маленьких размерах — на весь квадрат, чтобы буквы были крупнее
    r = (48 if small else 56) * s
    thick = (19 if small else 15) * s           # на маленьких размерах линии жирнее — читается в трее
    segs = [((ax * s, ay * s), (bx * s, by * s)) for (ax, ay), (bx, by) in LETTERS]
    ss = 4 if size <= 64 else 2
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            bg_a = ink = 0
            for sy in range(ss):
                for sx in range(ss):
                    px, py = x + (sx + .5) / ss, y + (sy + .5) / ss
                    lo, hi = pad + r, size - pad - r
                    cx, cy = min(max(px, lo), hi), min(max(py, lo), hi)
                    if math.hypot(px - cx, py - cy) <= r and pad <= px <= size - pad and pad <= py <= size - pad:
                        bg_a += 1
                        if min(seg_dist(px, py, *a, *b) for a, b in segs) <= thick:
                            ink += 1
            if bg_a == 0:
                row += b"\0\0\0\0"
                continue
            br, bgc, bb = _gradient(min(1.0, (x * 0.55 + y * 0.45) / size))
            k = ink / bg_a
            row += bytes([int(br + (255 - br) * k), int(bgc + (255 - bgc) * k), int(bb + (255 - bb) * k),
                          int(255 * bg_a / (ss * ss))])
        rows.append(bytes(row))
    return rows


def main(out):
    sizes = [16, 24, 32, 48, 64, 256]
    pngs = [render(sz) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for sz, png in zip(sizes, pngs):
        b = 0 if sz == 256 else sz
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    with open(out, "wb") as f:
        f.write(header + entries + b"".join(pngs))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "app.ico"))
