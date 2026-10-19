"""Build the bundled Winamp skin, in the app's own colours.

A .wsz is a zip of bitmaps at fixed sizes, sliced by coordinates the
format never wrote down — winampskin.py carries them, taken from Webamp.
So this draws each sheet at the size the slicer expects rather than
designing freely: get a sheet's dimensions wrong and the buttons come out
as pieces of their neighbours.

Run it from the project root:

    python3 tools/make_skin.py

It writes src/skins/base.wsz, which classic.py falls back to when no
skin has been chosen.
"""

import os
import sys
import zipfile

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient,
                         QPainter, QPen)

# The app's palette, so the skinned window is recognisably the same
# program rather than a generic dark skin.
BG = "#141417"
PANEL = "#1c1c21"
EDGE = "#2c2c33"
TEXT = "#d6d6d8"
DIM = "#8a8a92"
ACCENT = "#d99a2b"
ACCENT_DIM = "#8a6218"


def image(width, height, fill=BG):
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor(fill))
    return img


def painter(img):
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, False)
    return p


def plate(p, x, y, w, h, fill=PANEL, edge=EDGE):
    p.fillRect(x, y, w, h, QColor(fill))
    p.setPen(QPen(QColor(edge), 1))
    p.drawRect(x, y, w - 1, h - 1)


def label(p, x, y, w, h, text, colour=TEXT, size=7, bold=False):
    font = QFont("DejaVu Sans", size)
    font.setBold(bold)
    p.setFont(font)
    p.setPen(QColor(colour))
    p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)


# --------------------------------------------------------------------------
# The sheets
# --------------------------------------------------------------------------

def main_bmp():
    """275x116 — the player body."""
    img = image(275, 116)
    p = painter(img)

    # Title bar area is drawn from titlebar.bmp; this is everything else.
    plate(p, 0, 14, 275, 102, BG, EDGE)

    # The readout: a sunken panel where the song scrolls.
    plate(p, 24, 24, 200, 20, "#0e0e10", "#3a3a42")
    # Time display.
    plate(p, 34, 26, 64, 14, "#0e0e10", "#0e0e10")
    # No printed labels here.
    #
    # The widget draws the song title, the bitrate and the sample rate
    # into this same strip at runtime, so anything painted underneath
    # shows through the gaps between characters.

    # Mono / stereo lamps sit at 212,41 in the format; leave their plate.
    plate(p, 208, 24, 56, 12, "#0e0e10", "#2a2a31")

    # The visualiser well.
    plate(p, 24, 43, 76, 16, "#0e0e10", "#3a3a42")

    # Song title strip — a well for text the widget writes.
    plate(p, 108, 43, 156, 16, "#0e0e10", "#3a3a42")

    # Position bar groove.
    plate(p, 16, 72, 248, 10, "#0e0e10", "#3a3a42")

    # Transport well — the buttons themselves come from cbuttons.bmp.
    plate(p, 16, 88, 136, 18, PANEL, EDGE)

    # Volume and balance grooves.
    plate(p, 107, 57, 68, 13, "#0e0e10", "#3a3a42")
    plate(p, 177, 57, 38, 13, "#0e0e10", "#3a3a42")

    p.end()
    return img


def titlebar_bmp():
    """275x87 — active and inactive title bars, stacked."""
    img = image(275, 87)
    p = painter(img)
    for row, (fill, ink) in enumerate(((PANEL, TEXT), (BG, DIM))):
        y = row * 14
        p.fillRect(0, y, 275, 14, QColor(fill))
        p.setPen(QPen(QColor(EDGE), 1))
        p.drawLine(0, y + 13, 274, y + 13)
        label(p, 0, y, 275, 14, "FREQUENCIES", ink, 6, True)
        # Close and shade buttons, drawn as marks rather than sprites.
        p.setPen(QPen(QColor(ink), 1))
        p.drawLine(264, y + 5, 268, y + 9)
        p.drawLine(268, y + 5, 264, y + 9)
        p.drawLine(254, y + 9, 259, y + 9)
    # The easter-egg rows below are left as background.
    p.end()
    return img


def cbuttons_bmp():
    """136x36 — previous, play, pause, stop, next, eject; up then pressed."""
    img = image(136, 36, PANEL)
    p = painter(img)
    widths = [23, 23, 23, 23, 23, 22]
    glyphs = ["|<", ">", "||", "[]", ">|", "^"]
    for row in range(2):
        x = 0
        for width, glyph in zip(widths, glyphs):
            fill = EDGE if row else PANEL
            p.fillRect(x, row * 18, width, 18, QColor(fill))
            p.setPen(QPen(QColor("#3a3a42"), 1))
            p.drawRect(x, row * 18, width - 1, 17)
            label(p, x, row * 18, width, 18, glyph,
                  ACCENT if row else TEXT, 7, True)
            x += width
    p.end()
    return img


def shufrep_bmp():
    """92x85 — repeat and shuffle, in four states, plus the two small buttons."""
    img = image(92, 85, PANEL)
    p = painter(img)
    for row in range(4):
        on = row in (2, 3)
        pressed = row in (1, 3)
        y = row * 15
        for x, text in ((0, "REP"), (28, "SHUF")):
            width = 28 if text == "REP" else 47
            p.fillRect(x, y, width, 15, QColor(EDGE if pressed else PANEL))
            p.setPen(QPen(QColor("#3a3a42"), 1))
            p.drawRect(x, y, width - 1, 14)
            label(p, x, y, width, 15, text,
                  ACCENT if on else DIM, 6, on)
    # Equaliser and playlist toggles live at the bottom of this sheet.
    for i, text in enumerate(("EQ", "PL")):
        for state in range(2):
            x = i * 23
            y = 61 + state * 12
            p.fillRect(x, y, 23, 12, QColor(EDGE if state else PANEL))
            label(p, x, y, 23, 12, text, ACCENT if state else TEXT, 6, True)
    p.end()
    return img


def posbar_bmp():
    """307x10 — the groove, then the handle twice."""
    img = image(307, 10, "#0e0e10")
    p = painter(img)
    p.setPen(QPen(QColor("#3a3a42"), 1))
    p.drawRect(0, 0, 247, 9)
    for i in range(2):
        x = 248 + i * 29
        p.fillRect(x, 0, 29, 10, QColor(ACCENT if i else ACCENT_DIM))
        p.setPen(QPen(QColor("#0e0e10"), 1))
        p.drawLine(x + 14, 2, x + 14, 7)
    p.end()
    return img


def slider_bmp(width, height, steps):
    """A column of backgrounds with the handle at the bottom."""
    img = image(width, height, PANEL)
    p = painter(img)
    for i in range(steps):
        y = i * 15
        p.fillRect(0, y, width, 15, QColor(PANEL))
        filled = int((width - 10) * (i / max(1, steps - 1)))
        p.fillRect(3, y + 5, filled, 4, QColor(ACCENT))
        p.setPen(QPen(QColor("#3a3a42"), 1))
        p.drawRect(2, y + 4, width - 8, 5)
    # Two handle frames at the very bottom of the sheet.
    base = steps * 15
    for i in range(2):
        y = base + i * 11
        p.fillRect(0, y, 14, 11, QColor(EDGE if i else PANEL))
        p.setPen(QPen(QColor(ACCENT), 1))
        p.drawRect(0, y, 13, 10)
    p.end()
    return img


def monoster_bmp():
    """56x24 — mono and stereo lamps, lit row then unlit."""
    img = image(56, 24, BG)
    p = painter(img)
    for row in range(2):
        ink = ACCENT if row == 0 else DIM
        label(p, 0, row * 12, 28, 12, "MONO", ink, 6, row == 0)
        label(p, 28, row * 12, 28, 12, "STEREO", ink, 6, row == 0)
    p.end()
    return img


def playpaus_bmp():
    """42x9 — playing, paused, stopped, and the two work indicators."""
    img = image(42, 9, "#0e0e10")
    p = painter(img)
    marks = ((0, ">", ACCENT), (9, "||", ACCENT), (18, "#", DIM),
             (27, ".", DIM), (36, ".", DIM))
    for x, glyph, ink in marks:
        label(p, x, 0, 9, 9, glyph, ink, 6, True)
    p.end()
    return img


def numbers_bmp():
    """108x13 — the digits 0-9 plus a blank, for the clock."""
    img = image(108, 13, "#0e0e10")
    p = painter(img)
    for i in range(10):
        label(p, i * 9, 0, 9, 13, str(i), ACCENT, 8, True)
    p.end()
    return img


def text_bmp():
    """155x18 — the bitmap font, three rows of characters."""
    rows = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ\"@ ",
            "0123456789...:()-'!_+\\/[]^&%.=$#",
            "\u00c5\u00d6\u00c4?*  <>[]  ")
    img = image(155, 18, "#0e0e10")
    p = painter(img)
    font = QFont("DejaVu Sans Mono", 6)
    p.setFont(font)
    p.setPen(QColor(TEXT))
    for row, line in enumerate(rows):
        for col, ch in enumerate(line[:31]):
            p.drawText(QRectF(col * 5, row * 6, 5, 6), Qt.AlignCenter, ch)
    p.end()
    return img


def eqmain_bmp():
    """275x315 — the equaliser window and its slider sheet."""
    img = image(275, 315)
    p = painter(img)
    plate(p, 0, 0, 275, 116, BG, EDGE)
    p.fillRect(0, 0, 275, 14, QColor(PANEL))
    label(p, 0, 0, 275, 14, "EQUALIZER", TEXT, 6, True)
    # The ten band grooves.
    for i in range(10):
        x = 78 + i * 18
        plate(p, x, 38, 14, 62, "#0e0e10", "#3a3a42")
    plate(p, 21, 38, 14, 62, "#0e0e10", "#3a3a42")      # preamp
    # The slider background strip the format reads from y=164.
    for i in range(14):
        y = 164 + i * 0
        break
    p.fillRect(13, 164, 209, 129, QColor(PANEL))
    for i in range(14):
        p.fillRect(13 + i * 15, 164, 14, 63, QColor("#0e0e10"))
    p.end()
    return img


def eq_ex_bmp():
    """275x18 — the equaliser's own title bar."""
    img = image(275, 18, PANEL)
    p = painter(img)
    label(p, 0, 0, 275, 14, "EQUALIZER", TEXT, 6, True)
    p.setPen(QPen(QColor(EDGE), 1))
    p.drawLine(0, 13, 274, 13)
    p.end()
    return img


def pledit_bmp():
    """280x186 — the playlist window's frame pieces."""
    img = image(280, 186, BG)
    p = painter(img)
    p.fillRect(0, 0, 280, 20, QColor(PANEL))
    label(p, 0, 0, 280, 20, "PLAYLIST", TEXT, 6, True)
    p.fillRect(0, 20, 280, 166, QColor("#0e0e10"))
    p.setPen(QPen(QColor(EDGE), 1))
    p.drawRect(0, 0, 279, 185)
    p.end()
    return img


SHEETS = {
    "main.bmp": main_bmp,
    "titlebar.bmp": titlebar_bmp,
    "cbuttons.bmp": cbuttons_bmp,
    "shufrep.bmp": shufrep_bmp,
    "posbar.bmp": posbar_bmp,
    "volume.bmp": lambda: slider_bmp(68, 433, 28),
    "balance.bmp": lambda: slider_bmp(47, 433, 28),
    "monoster.bmp": monoster_bmp,
    "playpaus.bmp": playpaus_bmp,
    "numbers.bmp": numbers_bmp,
    "nums_ex.bmp": numbers_bmp,
    "text.bmp": text_bmp,
    "eqmain.bmp": eqmain_bmp,
    "eq_ex.bmp": eq_ex_bmp,
    "pledit.bmp": pledit_bmp,
}

PLEDIT_TXT = f"""[Text]
Normal={TEXT}
Current={ACCENT}
NormalBG=#0e0e10
SelectedBG={EDGE}
Font=DejaVu Sans
"""

# Twenty-four colours: background, dots, then the analyser gradient and
# the oscilloscope. Winamp reads this as "r,g,b" per line.
VISCOLOR_TXT = "\n".join([
    "14,14,16,255",          # background
    "44,44,50,255",          # dot grid
] + [
    f"{217 - i * 6},{154 - i * 4},{43 + i * 2},255" for i in range(16)
] + [
    "214,214,216,255" for _ in range(6)
]) + "\n"


def build(out_path):
    folder = os.path.dirname(out_path)
    os.makedirs(folder, exist_ok=True)
    written = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, maker in SHEETS.items():
            temp = os.path.join(folder, name)
            img = maker()
            if not img.save(temp, "BMP"):
                raise RuntimeError(f"could not render {name}")
            bundle.write(temp, name)
            written.append((name, img.width(), img.height()))
            os.remove(temp)
        bundle.writestr("pledit.txt", PLEDIT_TXT)
        bundle.writestr("viscolor.txt", VISCOLOR_TXT)
    return written


def main():
    # QGuiApplication, because QImage needs one before it will render text.
    app = QGuiApplication(sys.argv[:1])          # noqa: F841
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "src", "skins", "base.wsz")
    out = os.path.normpath(out)
    for name, w, h in build(out):
        print(f"  {name:16} {w}x{h}")
    print(f"wrote {out} ({os.path.getsize(out)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
