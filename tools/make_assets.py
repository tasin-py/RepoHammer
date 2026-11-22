"""Generate RepoHammer's icon (a Norse war hammer with lightning) and its build-done chime.

Standard library only. Run:  python tools/make_assets.py
Writes assets/icon.ico, assets/icon-<size>.png and assets/done.wav.
"""
import math
import os
import struct
import wave
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SIZES = (16, 24, 32, 48, 64, 128, 256)
RENDER = 768  # every size divides this evenly, so downscaling is a clean box filter


# ------------------------------------------------------------------ geometry helpers
def hexc(h, a=1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def inside(poly, x, y):
    n, c = len(poly), False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def edge_dist(poly, x, y):
    return min(seg_dist(x, y, *poly[i], *poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def line_dist(pts, x, y):
    return min(seg_dist(x, y, *pts[i], *pts[i + 1]) for i in range(len(pts) - 1))


def over(dst, src):
    """Alpha-composite src over dst (straight alpha)."""
    sa = src[3]
    if sa <= 0:
        return dst
    da = dst[3]
    oa = sa + da * (1 - sa)
    if oa <= 0:
        return (0, 0, 0, 0)
    return tuple((src[i] * sa + dst[i] * da * (1 - sa)) / oa for i in range(3)) + (oa,)


# ------------------------------------------------------------------ the hammer
HEAD = [(.17, .15), (.83, .15), (.89, .21), (.89, .40), (.83, .46), (.17, .46), (.11, .40), (.11, .21)]
RUNE = [(.50, .215), (.565, .305), (.50, .395), (.435, .305)]
HANDLE = (.425, .46, .575, .775)     # x0, y0, x1, y1 (short, thick grip)
BOLTS = [
    [(.105, .20), (.035, .31), (.085, .335), (.005, .52)],
    [(.895, .20), (.965, .31), (.915, .335), (.995, .52)],
    [(.30, .135), (.355, .04), (.395, .085), (.445, .0)],
]
OUTLINE = hexc("#1E2530")


SCALE = 0.86  # leave a margin so the lightning glow isn't clipped


def pixel(x, y):
    x, y = .5 + (x - .5) / SCALE, .5 + (y - .5) / SCALE
    c = (0, 0, 0, 0)
    # lightning glow, behind everything
    d = min(line_dist(b, x, y) for b in BOLTS)
    if d < .06:
        c = over(c, hexc("#2FA8FF", 0.55 * (1 - d / .06) ** 2))
    # handle (leather-wrapped wood)
    x0, y0, x1, y1 = HANDLE
    if x0 - .012 <= x <= x1 + .012 and y0 <= y <= y1 + .012:
        if x0 <= x <= x1 and y <= y1:
            t = abs((x - (x0 + x1) / 2) / ((x1 - x0) / 2))
            col = mix(hexc("#B87A45"), hexc("#4A2E16"), t ** 1.6)
            if ((y * 1.0 + (x - x0) * 0.9) * 34) % 1 < 0.22:
                col = mix(col, hexc("#2E1C0C"), 0.55)
            c = over(c, col)
        else:
            c = over(c, OUTLINE)
    # pommel and wrist strap loop
    if math.hypot((x - .5) / .105, (y - .80) / .045) <= 1:
        inner = math.hypot((x - .5) / .088, (y - .80) / .032) <= 1
        c = over(c, mix(hexc("#DDE3EA"), hexc("#6B7686"), (y - .765) / .07) if inner else OUTLINE)
    r = math.hypot(x - .5, y - .885)
    if .024 <= r <= .048 and y > .84:
        c = over(c, hexc("#5A3A1E") if .030 <= r <= .042 else OUTLINE)
    # head
    if .095 <= x <= .905 and .135 <= y <= .475:
        if inside(HEAD, x, y):
            t = (y - .15) / .31
            col = mix(hexc("#F1F4F8"), hexc("#5E6979"), t ** 0.9)
            if x < .17 or x > .83:          # chamfered side faces
                col = mix(col, hexc("#3E4756"), 0.35)
            if y < .185:                    # top bevel highlight
                col = mix(col, hexc("#FFFFFF"), 0.45)
            if abs(y - .232) < .006 or abs(y - .378) < .006:  # engraved bands
                if .19 < x < .81 and not inside(RUNE, x, y):
                    col = mix(col, hexc("#2F3744"), 0.6)
            if inside(RUNE, x, y):          # rune plate
                col = hexc("#3A4352") if edge_dist(RUNE, x, y) > .012 else hexc("#8FD3FF")
            c = over(c, col)
            if edge_dist(HEAD, x, y) < .018:
                c = over(c, OUTLINE)
        elif edge_dist(HEAD, x, y) < .018:
            c = over(c, OUTLINE)
    # lightning cores, in front
    if d < .022:
        c = over(c, hexc("#E9F8FF") if d < .011 else hexc("#5CC4FF"))
    return c


def render(size):
    rows = []
    for j in range(size):
        y = (j + .5) / size
        rows.append([pixel((i + .5) / size, y) for i in range(size)])
    return rows


def downscale(img, factor):
    n = len(img) // factor
    out = []
    for j in range(n):
        row = []
        for i in range(n):
            r = g = b = a = 0.0
            for dy in range(factor):
                src = img[j * factor + dy]
                for dx in range(factor):
                    pr, pg, pb, pa = src[i * factor + dx]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            k = factor * factor
            row.append((r / a, g / a, b / a, a / k) if a else (0, 0, 0, 0))
        out.append(row)
    return out


def png_bytes(img):
    raw = b"".join(b"\x00" + bytes(int(max(0, min(1, v)) * 255 + .5) for p in row for v in p) for row in img)
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    n = len(img)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icon():
    print(f"rendering {RENDER}x{RENDER} hammer...")
    big = render(RENDER)
    pngs = {}
    for s in SIZES:
        data = png_bytes(downscale(big, RENDER // s))
        pngs[s] = data
        open(os.path.join(ASSETS, f"icon-{s}.png"), "wb").write(data)
    head = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries, blobs = b"", b""
    for s in SIZES:
        d = pngs[s]
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), offset + len(blobs))
        blobs += d
    open(os.path.join(ASSETS, "icon.ico"), "wb").write(head + entries + blobs)


# ------------------------------------------------------------------ the chime
def make_chime():
    """Two soft bell tones rising a fifth: pleasant, short, clearly 'done'."""
    rate, dur = 44100, 1.1
    notes = [(0.00, 1046.5), (0.13, 1568.0)]          # C6, then G6
    partials = [(1.0, 1.0), (2.0, 0.28), (3.01, 0.10), (4.16, 0.05)]
    frames = []
    for n in range(int(rate * dur)):
        t = n / rate
        v = 0.0
        for start, f in notes:
            tt = t - start
            if tt < 0:
                continue
            attack = min(1.0, tt / 0.006)
            for mult, amp in partials:
                v += amp * attack * math.exp(-tt * (3.2 + mult * 1.6)) * math.sin(2 * math.pi * f * mult * tt)
        frames.append(int(max(-1, min(1, v * 0.32)) * 32767))
    with wave.open(os.path.join(ASSETS, "done.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(frames)}h", *frames))


if __name__ == "__main__":
    os.makedirs(ASSETS, exist_ok=True)
    make_chime()
    make_icon()
    print("done:", ", ".join(sorted(os.listdir(ASSETS))))
