#!/usr/bin/env python3
"""Build a Java Instagram Reel (1080x1920, 30 fps, MP4) from a JSON spec.

Usage:
    python3 tools/build_reel.py spec.json out.mp4
    python3 tools/build_reel.py spec.json out.mp4 --preview 1.6 6.6 11.4   # PNG stills only

The video has animated text/code scenes, an original synthesized background
track and soft typing clicks on the code scenes. Every build also writes the
thumbnail (cover image) next to the video as <out>_cover.png; --preview writes
it as <out>_cover.png too. The spec must contain a "cover" block. See
tools/spec_example.json and REEL_WORKFLOW.md for the spec format.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, sosfilt

W, H, FPS = 1080, 1920, 30
SR = 44100
TOP_OFFSET = 130          # keep content inside Instagram's safe zone
MAX_CODE_LINES = 9
MAX_CODE_COLS = 34

INTER_B = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"
INTER_SB = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

BG_TOP, BG_BOT = (14, 16, 22), (22, 25, 40)
CARD = (24, 27, 36)
TXT = (245, 245, 247)
MUTED = (138, 143, 152)
ORANGE = (248, 152, 32)
RED = (255, 107, 107)
GREEN = (110, 231, 160)
DARK = (14, 16, 22)
C_TXT = (230, 232, 238)
C_KW = (255, 138, 91)
C_TY = (125, 211, 252)
C_STR = (134, 239, 172)
C_NUM = (253, 230, 138)
C_OP = (196, 181, 253)
COLORS = {"white": TXT, "orange": ORANGE, "red": RED, "green": GREEN, "muted": MUTED}
HL = {"red": (64, 34, 40), "orange": (64, 46, 24)}

KW = {
    "switch", "case", "default", "break", "yield", "return", "if", "else", "for", "while",
    "do", "try", "catch", "finally", "throw", "throws", "new", "class", "interface",
    "extends", "implements", "public", "private", "protected", "static", "final",
    "void", "import", "package", "record", "var", "instanceof", "this", "super",
    "abstract", "enum", "sealed", "permits", "synchronized", "volatile",
}
TY = {
    "String", "int", "long", "double", "float", "boolean", "char", "byte", "short",
    "System", "List", "Map", "Set", "Optional", "Stream", "Integer", "Object",
    "ArrayList", "HashMap", "HashSet", "Thread", "Runnable", "Math", "Arrays",
    "Collectors", "Collections", "Objects",
}

_fonts = {}


def F(path, size):
    k = (path, int(size))
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(path, int(size))
    return _fonts[k]


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    return 1 - (1 - clamp(x)) ** 3


def color(name, default=TXT):
    return COLORS.get(name, default) if isinstance(name, str) else default


# ----------------------------------------------------------------- validation
def validate(spec):
    scenes = spec.get("scenes")
    if not scenes:
        raise SystemExit("spec needs a non-empty 'scenes' list")
    total = 0.0
    for i, sc in enumerate(scenes):
        typ = sc.get("type")
        if typ not in ("hook", "code", "rules", "cta"):
            raise SystemExit(f"scene {i}: unknown type {typ!r}")
        if not isinstance(sc.get("dur"), (int, float)) or sc["dur"] <= 0:
            raise SystemExit(f"scene {i}: needs a positive 'dur'")
        total += sc["dur"]
        if typ == "code":
            code = sc.get("code") or []
            if not 1 <= len(code) <= MAX_CODE_LINES:
                raise SystemExit(f"scene {i}: code must have 1-{MAX_CODE_LINES} lines (has {len(code)})")
            for ln in code:
                if len(ln) > MAX_CODE_COLS:
                    raise SystemExit(f"scene {i}: code line too long ({len(ln)}>{MAX_CODE_COLS}): {ln!r}")
            if sc.get("type_dur", 3.0) >= sc["dur"]:
                raise SystemExit(f"scene {i}: type_dur must be shorter than dur")
        if typ == "rules" and not 2 <= len(sc.get("items", [])) <= 4:
            raise SystemExit(f"scene {i}: rules needs 2-4 items")
    if not 18 <= total <= 35:
        raise SystemExit(f"total duration {total:.1f}s must be between 18 and 35 seconds")
    cover = spec.get("cover")
    if not cover:
        raise SystemExit("spec needs a 'cover' block (thumbnail): kicker, title lines, optional snippet")
    if not 1 <= len(cover.get("title", [])) <= 3:
        raise SystemExit("cover.title needs 1-3 lines")
    snip = cover.get("snippet", [])
    if len(snip) > 4:
        raise SystemExit("cover.snippet can have at most 4 lines")
    for ln in snip:
        if len(ln) > MAX_CODE_COLS:
            raise SystemExit(f"cover.snippet line too long ({len(ln)}>{MAX_CODE_COLS}): {ln!r}")
    return total


# ------------------------------------------------------------------- drawing
bg = Image.new("RGBA", (W, H))
_g = ImageDraw.Draw(bg)
for _y in range(H):
    _f = _y / (H - 1)
    _g.line([(0, _y), (W, _y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * _f) for i in range(3)) + (255,))

CODE_SIZE, LINE_H = 42, 62
CHAR_W = F(MONO, CODE_SIZE).getlength("M")


def colorize(line):
    cols = [C_TXT] * len(line)
    for m in re.finditer(r'"[^"]*"|->|\b\w+\b', line):
        s = m.group()
        col = None
        if s.startswith('"'):
            col = C_STR
        elif s == "->":
            col = C_OP
        elif s == "break":
            col = RED
        elif s in KW:
            col = C_KW
        elif s in TY:
            col = C_TY
        elif s.isdigit():
            col = C_NUM
        if col:
            for i in range(m.start(), m.end()):
                cols[i] = col
    return cols


def fit_font(path, size, text, max_w=940):
    size = int(size)
    while size > 36 and F(path, size).getlength(text) > max_w:
        size -= 4
    return F(path, size)


def ctext(d, y, text, size, fill, path=INTER_B):
    d.text((W // 2, y), text, font=fit_font(path, size, text), fill=fill, anchor="mm")


def chip(d, x, y, text, col):
    f = F(INTER_B, 30)
    tw = f.getlength(text)
    d.rounded_rectangle([x, y, x + tw + 56, y + 62], radius=31, fill=col)
    d.text((x + 28, y + 31), text, font=f, fill=DARK, anchor="lm")


def code_card(d, y, lines, p, hl_words=None, hl_col=HL["red"], x=70, w=940):
    h = len(lines) * LINE_H + 80
    d.rounded_rectangle([x, y, x + w, y + h], radius=36, fill=CARD)
    total = sum(len(l) + 1 for l in lines)
    typed = int(clamp(p) * total)
    tx, ty = x + 40, y + 40
    cursor = None
    left = typed
    for i, line in enumerate(lines):
        cols = colorize(line)
        n = min(len(line), left)
        if hl_words:
            for m in re.finditer(r"\b\w+\b", line):
                if m.group() in hl_words and m.end() <= n:
                    x0 = tx + m.start() * CHAR_W - 6
                    d.rounded_rectangle(
                        [x0, ty + i * LINE_H - 2, x0 + (m.end() - m.start()) * CHAR_W + 12, ty + i * LINE_H + LINE_H - 12],
                        radius=10, fill=hl_col)
        for j in range(n):
            if line[j] != " ":
                d.text((tx + j * CHAR_W, ty + i * LINE_H), line[j], font=F(MONO, CODE_SIZE), fill=cols[j])
        if left <= len(line):
            cursor = (tx + n * CHAR_W, ty + i * LINE_H)
            break
        left -= len(line) + 1
    if cursor is not None and p < 1.0:
        d.rectangle([cursor[0] + 2, cursor[1] + 6, cursor[0] + 6, cursor[1] + 48], fill=ORANGE)
    return y + h


def scene_hook(d, sc, t):
    lines = sc["lines"]
    heights = [l.get("size", 88) * 1.2 for l in lines]
    y = 900 - sum(heights) / 2
    for i, l in enumerate(lines):
        yc = y + heights[i] / 2
        y += heights[i]
        if t >= 0.1 + i * 0.5:
            ctext(d, yc, l["text"], l.get("size", 88), color(l.get("color", "white")))


def scene_code(d, sc, t):
    chip_col = color(sc.get("chip_color", "green"), GREEN)
    if sc.get("chip"):
        chip(d, 70, 290, sc["chip"], chip_col)
    hl_col = HL.get(sc.get("highlight_color", "red"), HL["red"])
    bottom = code_card(d, 380, sc["code"], t / sc.get("type_dur", 3.0), set(sc.get("highlight", [])), hl_col)
    cursor = bottom + 60
    at = sc.get("after_at", sc.get("type_dur", 3.0) + 0.3)
    for i, it in enumerate(sc.get("after", [])):
        size = it.get("size", 70)
        yc = cursor + size * 0.65
        cursor += size * 1.3
        if t >= at + i * 0.5:
            ctext(d, yc, it["text"], size, color(it.get("color", "white")))


def scene_rules(d, sc, t):
    ctext(d, 340, sc.get("title", "KEY POINTS").upper(), 36, MUTED, INTER_SB)
    items = sc["items"]
    gap = 210 if len(items) <= 3 else 180
    for i, text in enumerate(items):
        if t >= 0.3 + i * 1.1:
            cy = 620 + i * gap
            d.ellipse([90, cy - 38, 166, cy + 38], fill=ORANGE)
            d.line([(110, cy + 2), (123, cy + 15), (148, cy - 14)], fill=DARK, width=8, joint="curve")
            d.text((205, cy), text, font=fit_font(INTER_B, 62, text, 780), fill=TXT, anchor="lm")


def scene_cta(d, sc, t):
    ctext(d, 760, sc.get("title", "Save this."), 100, TXT)
    if t >= 0.5:
        ctext(d, 900, sc.get("sub", "Follow for daily Java."), 64, MUTED, INTER_SB)
    if t >= 1.0:
        ctext(d, 1020, sc.get("handle", "@javaprograminglanguage"), 52, ORANGE)


SCENE_FN = {"hook": scene_hook, "code": scene_code, "rules": scene_rules, "cta": scene_cta}
FADE = 0.25


def make_frame_fn(spec, total):
    scenes = spec["scenes"]

    def frame_at(T):
        t0 = 0.0
        sc, t = scenes[-1], T - sum(s["dur"] for s in scenes[:-1])
        for s in scenes:
            if T < t0 + s["dur"]:
                sc, t = s, T - t0
                break
            t0 += s["dur"]
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        SCENE_FN[sc["type"]](d, sc, t)
        a = min(clamp(t / FADE), clamp((sc["dur"] - t) / FADE))
        if a < 1:
            layer.putalpha(layer.getchannel("A").point([int(v * a) for v in range(256)]))
        dy = TOP_OFFSET + int((1 - ease(t / 0.4)) * 40)
        fr = bg.copy()
        fr.alpha_composite(layer.crop((0, 0, W, H - dy)), dest=(0, dy))
        d2 = ImageDraw.Draw(fr)
        d2.rounded_rectangle([70, 270, 1010, 276], radius=3, fill=(40, 44, 58))
        d2.rounded_rectangle([70, 270, 70 + 940 * clamp(T / total), 276], radius=3, fill=ORANGE)
        d2.text((70, 325), "@javaprograminglanguage", font=F(INTER_SB, 30), fill=MUTED, anchor="lm")
        return fr.convert("RGB")

    return frame_at


# --------------------------------------------------------------------- cover
def build_cover(spec, path):
    """Thumbnail for the Reel: big hook title, optional code snippet, kept inside
    the centre area that survives Instagram's 3:4 profile-grid crop."""
    c = spec["cover"]
    img = bg.copy()
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([560, 180, 1220, 840], fill=(248, 152, 32, 95))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(130)))
    d = ImageDraw.Draw(img)
    chip(d, 70, 440, c.get("kicker", "JAVA TIP"), color(c.get("kicker_color", "orange"), ORANGE))
    y = 580
    for ln in c["title"]:
        size = ln.get("size", 140)
        f = fit_font(INTER_B, size, ln["text"])
        d.text((70, y + size * 0.55), ln["text"], font=f, fill=color(ln.get("color", "white")), anchor="lm")
        y += size * 1.18
    snippet = c.get("snippet", [])
    if snippet:
        code_card(d, int(y + 50), snippet, 1.0, set(c.get("highlight", [])), HL.get(c.get("highlight_color", "orange")))
        y += 50 + len(snippet) * LINE_H + 80
    d.text((70, int(y + 70)), "@javaprograminglanguage", font=F(INTER_SB, 34), fill=MUTED, anchor="lm")
    img.convert("RGB").save(path)
    return path


# --------------------------------------------------------------------- audio
def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


QUAL = {"maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "6": [0, 4, 7, 9], "7": [0, 4, 7, 10]}
PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PROGS = [
    [("C", "maj7"), ("A", "m7"), ("F", "maj7"), ("G", "6")],
    [("A", "m7"), ("F", "maj7"), ("C", "maj7"), ("G", "6")],
    [("F", "maj7"), ("G", "6"), ("E", "m7"), ("A", "m7")],
    [("C", "maj7"), ("E", "m7"), ("F", "maj7"), ("G", "7")],
]
PATTERNS = [
    [0, 1, 2, 3, 2, 1, 2, 3, 0, 1, 2, 3, 2, 1, 3, 2],
    [0, 2, 1, 3, 0, 2, 1, 3, 1, 2, 3, 2, 1, 3, 2, 1],
]
BPM = 75
BEAT = 60 / BPM
CHORD_LEN = 8 * BEAT


def build_music(spec, total, rng):
    n = int(SR * total)
    seed = int(hashlib.md5(spec.get("slug", "reel").encode()).hexdigest(), 16)
    prog = PROGS[seed % len(PROGS)]
    pattern = PATTERNS[(seed // 7) % len(PATTERNS)]

    def add(buf, start, sig):
        i = int(start * SR)
        j = min(n, i + len(sig))
        if 0 <= i < n:
            buf[i:j] += sig[: j - i]

    def adsr(m, a, dd, s, r):
        e = np.ones(m) * s
        na, nd, nr = int(a * SR), int(dd * SR), int(r * SR)
        e[:na] = np.linspace(0, 1, na, endpoint=False)
        e[na:na + nd] = np.linspace(1, s, nd, endpoint=False)
        e[-nr:] = e[-nr - 1] * np.linspace(1, 0, nr)
        return e

    pad, pluck, bass, blips = (np.zeros(n) for _ in range(4))
    n_chords = int(np.ceil(total / CHORD_LEN))
    for ci in range(n_chords):
        name, qual = prog[ci % len(prog)]
        base = 57 + ((PCS[name] - 57) % 12)
        notes = [base + iv for iv in QUAL[qual]]
        t0 = ci * CHORD_LEN
        m = int((CHORD_LEN + 1.2) * SR)
        tt = np.arange(m) / SR
        e = adsr(m, 1.4, 0.5, 0.8, 1.6)
        for nt in notes:
            for det in (-0.12, 0.12):
                add(pad, t0, 0.05 * e * np.sin(2 * np.pi * (midi(nt) + det) * tt))
        mb = int(CHORD_LEN * SR)
        tb = np.arange(mb) / SR
        add(bass, t0, 0.16 * adsr(mb, 0.05, 0.4, 0.6, 0.6) * np.sin(2 * np.pi * midi(base - 24) * tb))
        for k, idx in enumerate(pattern):
            f = midi(notes[idx] + 12)
            mp = int(1.4 * SR)
            tp = np.arange(mp) / SR
            s = (np.sin(2 * np.pi * f * tp) + 0.25 * np.sin(2 * np.pi * 2 * f * tp)) * np.exp(-tp * 4.2)
            s[: int(0.004 * SR)] *= np.linspace(0, 1, int(0.004 * SR))
            add(pluck, t0 + k * BEAT / 2, (0.07 if k % 4 == 0 else 0.045) * s)

    # soft blips on scene changes and on each rule item
    t0 = 0.0
    for sc in spec["scenes"]:
        if t0 > 0:
            tp = np.arange(int(0.35 * SR)) / SR
            add(blips, t0, 0.05 * np.sin(2 * np.pi * midi(84) * tp) * np.exp(-tp * 12))
        if sc["type"] == "rules":
            for i in range(len(sc["items"])):
                tp = np.arange(int(0.25 * SR)) / SR
                add(blips, t0 + 0.3 + i * 1.1 + 0.3, 0.045 * np.sin(2 * np.pi * midi(79) * tp) * np.exp(-tp * 16))
        t0 += sc["dur"]

    dry = pad + bass + pluck + blips
    ir_len = int(1.6 * SR)
    ir = rng.standard_normal(ir_len) * np.exp(-np.arange(ir_len) / SR * 3.2)
    ir /= np.sqrt(np.sum(ir ** 2))
    mix = dry + 0.35 * fftconvolve(pad + pluck + blips, ir)[:n]
    fi, fo = int(0.8 * SR), int(1.8 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)
    mix[-fo:] *= np.linspace(1, 0, fo)
    return mix / np.max(np.abs(mix)) * 0.5


def build_clicks(spec, total, rng):
    n = int(SR * total)
    sos = butter(2, [1800, 7000], btype="band", fs=SR, output="sos")

    def key_click(enter=False):
        m = int(0.045 * SR)
        t = np.arange(m) / SR
        noise = sosfilt(sos, rng.standard_normal(m) * np.exp(-t * (300 if enter else 420)))
        f = rng.uniform(150, 230) * (0.8 if enter else 1.0)
        s = noise * 0.9 + np.sin(2 * np.pi * f * t) * np.exp(-t * 95) * (0.55 if enter else 0.35)
        return s / np.max(np.abs(s))

    clicks = np.zeros(n)
    start = 0.0
    for sc in spec["scenes"]:
        if sc["type"] == "code":
            flat = "\n".join(sc["code"]) + "\n"
            dur = sc.get("type_dur", 3.0)
            line_start = True
            for k, ch in enumerate(flat, start=1):
                indent = ch == " " and line_start
                if ch != " ":
                    line_start = ch == "\n"
                if indent:
                    continue
                ts = start + k * dur / len(flat)
                if ts >= total - 0.05:
                    continue
                s = key_click(ch == "\n") * rng.uniform(0.55, 1.0) * (0.9 if ch == " " else 1.0)
                i = int(ts * SR)
                j = min(n, i + len(s))
                clicks[i:j] += s[: j - i]
        start += sc["dur"]
    return clicks * 0.16


# ---------------------------------------------------------------------- main
def main():
    args = [a for a in sys.argv[1:]]
    if len(args) < 2:
        raise SystemExit(__doc__)
    spec_path, out = args[0], args[1]
    spec = json.load(open(spec_path))
    total = validate(spec)
    frame_at = make_frame_fn(spec, total)

    if "--preview" in args:
        times = [float(x) for x in args[args.index("--preview") + 1:]]
        base = os.path.splitext(out)[0]
        print(build_cover(spec, f"{base}_cover.png"))
        for T in times:
            path = f"{base}_{T:.1f}.png"
            frame_at(T).save(path)
            print(path)
        return

    with tempfile.TemporaryDirectory() as tmp:
        silent = os.path.join(tmp, "silent.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-crf", "18", "-preset", "medium", silent]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        frames = int(round(total * FPS))
        for i in range(frames):
            p.stdin.write(frame_at(i / FPS).tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise SystemExit("ffmpeg video encode failed")

        rng = np.random.default_rng(int(hashlib.md5(spec.get("slug", "reel").encode()).hexdigest(), 16) % (2 ** 32))
        music = build_music(spec, total, rng)
        clicks = build_clicks(spec, total, rng)
        mix = np.clip(np.stack([music + clicks, np.roll(music, int(0.004 * SR)) + clicks], axis=1), -0.98, 0.98)
        wav = os.path.join(tmp, "mix.wav")
        wavfile.write(wav, SR, (mix * 32767).astype(np.int16))

        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", wav, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-16:TP=-1.5:LRA=7",
                        "-shortest", "-movflags", "+faststart", out], check=True)
    cover_path = build_cover(spec, os.path.splitext(out)[0] + "_cover.png")
    print(f"wrote {out} ({total:.1f}s, {frames} frames) and cover {cover_path}")


if __name__ == "__main__":
    main()
