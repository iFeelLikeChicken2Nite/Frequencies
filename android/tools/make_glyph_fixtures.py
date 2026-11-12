"""Draw the desktop's glyphs (src/shell.py _make_icon) as test fixtures.

core GlyphsTest draws the phone's copies (core Glyphs) and compares them
with these, frame by frame. Run from the desktop's src folder's parent:

    QT_QPA_PLATFORM=offscreen python3 tools/make_glyph_fixtures.py <desktop src> core/src/test/resources/glyphs

Each glyph is drawn at the desktop's 22 px size, four times finer
(device pixel ratio 4), without the desktop's whole-pixel rounding, in
white on transparency: only the shape is compared, not the color.
"""
import builtins
import json
import os
import sys

src, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.abspath(src))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QLineF, QPointF, Qt          # noqa: E402
from PyQt5.QtGui import QPainter, QPixmap              # noqa: E402
from PyQt5.QtWidgets import QApplication               # noqa: E402

app = QApplication([])
import shell                                            # noqa: E402

SCALE = 4
SIZE = 22


def _int(v, *a):
    # Only the drawing's coordinates stay fractional; names still parse.
    return builtins.int(v, *a) if isinstance(v, (str, bytes)) or a else v


class _Painter(QPainter):
    def drawLine(self, *a):
        if len(a) == 4:
            return super().drawLine(QLineF(*a))
        return super().drawLine(*a)


class _Pixmap(QPixmap):
    def __init__(self, *a):
        if len(a) == 2 and all(isinstance(v, builtins.int) for v in a):
            super().__init__(a[0] * SCALE, a[1] * SCALE)
            self.setDevicePixelRatio(SCALE)
        else:
            super().__init__(*a)


shell.int = _int
shell.QPoint = QPointF
shell.QPainter = _Painter
shell.QPixmap = _Pixmap
shell.T["text2"] = "#808080"           # the gray under shazam_fill
shell.T.setdefault("bar_bg", "#000000")

FRAMES = {
    "play": [""], "pause": [""], "stop": [""], "prev": [""], "next": [""],
    "playpause": [str(i) for i in range(5)],
    "star": [""], "star_on": [""], "star_shine": [str(i) for i in range(1, 5)],
    "volume": ["3"],
    "eq": ["", "_25", "_50", "_75", "_100"],
    "timer": [""] + ["_%d" % i for i in range(10, 101, 10)],
    "search": [""], "stations": [""], "discover": [""], "history": [""], "trash": [""], "info": [""],
    "menu": [""], "expand": [""], "shazam": [""], "shazam_on": [""],
    "shazam_fill": [str(i) for i in range(5)],
    "gear": [""], "schedule": [""], "help": [""], "tag": [""], "back": [""], "folder": [""], "link": [""],
    "youtube": [""], "twitch": [""], "shared": [""],
    "thumb_up": [""], "thumb_down": [""], "thumb_up_on": [""], "thumb_down_on": [""],
    "fullscreen": ["", "1", "2"], "plus": [""], "edit": [""],
    "shuffle": ["_%d" % i for i in range(5)],
    "next_shuffle": [str(i) for i in range(1, 5)],
}

os.makedirs(out, exist_ok=True)
index = []
for base, tails in FRAMES.items():
    for tail in tails:
        name = base + tail
        shell._icon_files[name] = ""          # the drawn version, never a file
        pm = shell._make_icon(name, SIZE, "#ffffff").pixmap(SIZE * SCALE, SIZE * SCALE)
        img = pm.toImage()
        path = os.path.join(out, name + ".png")
        if not img.save(path):
            sys.exit("could not write " + path)
        index.append(name)
with open(os.path.join(out, "index.json"), "w") as f:
    json.dump({"size": SIZE, "scale": SCALE, "names": index}, f, indent=1)
print(len(index), "glyphs drawn")
