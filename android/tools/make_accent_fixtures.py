#!/usr/bin/python3.12
"""Make ArtAccentTest's fixtures with the desktop's own color pick.

    python3 tools/make_accent_fixtures.py <desktop source>/src

Needs PyQt5. Every picture is saved as a PNG (lossless, so the phone's
test reads the very pixels Qt read) next to what the desktop's
theme.dominant_color(image, fallback=None) answers for it (None = the art
has no color, the accent falls back). Pictures: the 13 default stations'
own art, plus made-up ones for the edge cases (gray, a speck of color,
pale tints, a two-color split, odd sizes).
"""

import base64
import json
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "core/src/test/resources/accent"


def main():
    sys.path.insert(0, sys.argv[1])
    from PyQt5.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PyQt5.QtCore import Qt
    app = QGuiApplication([])
    import theme

    pictures = {}
    stations = json.loads((ROOT / "core/src/main/resources/com/frequencies/core/default_stations.json").read_text())
    for i, s in enumerate(stations):
        img = QImage()
        if not img.loadFromData(base64.b64decode(s["art_data"])):
            continue                    # a format this Qt can't read
        pictures[f"station{i:02d}"] = img.convertToFormat(QImage.Format_RGB32)

    def made(name, w, h, paint):
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor("#808080"))
        p = QPainter(img)
        paint(p, w, h)
        p.end()
        pictures[name] = img

    made("gray", 300, 300, lambda p, w, h: p.fillRect(0, 0, w, h, QColor("#7a7a7a")))
    made("white_speck", 400, 400, lambda p, w, h: (p.fillRect(0, 0, w, h, QColor("#f4f4f4")),
                                                   p.fillRect(10, 10, 8, 8, QColor("#ff0000"))))
    made("pale", 320, 320, lambda p, w, h: p.fillRect(0, 0, w, h, QColor(235, 225, 210)))
    made("split", 500, 333, lambda p, w, h: (p.fillRect(0, 0, w // 3, h, QColor("#1f4fd8")),
                                             p.fillRect(w // 3, 0, w, h, QColor("#e0a020"))))
    def stripes(p, w, h):
        for x in range(0, w, 7):
            p.fillRect(x, 0, 3, h, QColor.fromHsl((x * 3) % 360, 200, 110))
    made("stripes", 257, 199, stripes)
    def gradient(p, w, h):
        for y in range(h):
            p.fillRect(0, y, w, 1, QColor.fromHsl(300, 160, 20 + y * 200 // h))
    made("gradient", 600, 600, gradient)

    answers = {}
    for name, img in pictures.items():
        img.save(str(OUT / f"{name}.png"), "PNG")
        back = QImage(str(OUT / f"{name}.png"))
        colour = theme.dominant_color(back, fallback=None)
        answers[name] = colour.name() if colour is not None else None
    # The fade: AccentController._fade_tick's frames between a few pairs.
    fades = []
    frames = max(1, theme.AccentController.FADE_MS // theme.AccentController.FADE_STEP)
    for a, b in (("#d99a2b", "#2568d3"), ("#e2564b", "#c2ee70"), ("#808080", "#1ad6da"), ("#d61a20", "#b34db3")):
        steps = []
        for frame in range(1, frames + 1):
            t = 1.0 if frame >= frames else frame / frames
            steps.append(theme._blend(QColor(a), QColor(b), 1.0 - (1.0 - t) ** 3).name())
        fades.append({"from": a, "to": b, "frames": steps})
    readable = {c: theme.readable_accent(QColor(c)).name()
                for c in ("#000000", "#ffffff", "#7a7a7a", "#ffff00", "#0000a0", "#ffb3c1", "#12ff34", "#3a0000")}
    # Qt's own color math, spot-checked: RGB -> HSL and HSL -> RGB.
    import random
    rnd = random.Random(148)
    to_hsl = []
    for _ in range(4000):
        c = QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        to_hsl.append([c.name()] + list(c.getHsl()[:3]))
    from_hsl = []
    for _ in range(4000):
        h, s, l = rnd.randrange(-1, 360), rnd.randrange(256), rnd.randrange(256)
        c = QColor()
        c.setHsl(h, s, l)
        from_hsl.append([h, s, l, c.name()])
    (OUT / "desktop-answers.json").write_text(json.dumps(
        {"dominant": answers, "fades": fades, "readable": readable}, indent=1) + "\n")
    (OUT / "qt-hsl.json").write_text(json.dumps({"to_hsl": to_hsl, "from_hsl": from_hsl}) + "\n")
    for k, v in answers.items():
        print(k, v)


if __name__ == "__main__":
    main()
