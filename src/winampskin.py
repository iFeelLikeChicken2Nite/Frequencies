"""
winampskin.py - classic Winamp 2 skins (.wsz / .zip).

A skin is a zip of bitmaps at fixed, undocumented-but-universally-known
coordinates. There is no manifest and no metadata: `cbuttons.bmp` holds
the transport buttons at specific pixel offsets because that is where
Winamp 2.0 put them in 1998, and every skin made since has matched.
So this file is mostly a table of those offsets.

Supported: all three windows a classic skin dresses - the main player,
the equalizer (`eqmain.bmp`) and the playlist (`pledit.bmp`), the last
of which is built from tiles rather than one background because it is
the only resizable window Winamp had.

Windowshade - the collapsed one-line mode - is supported for all three
windows. Not supported: the visualizer.

The sprite coordinates below are the ones every skin has matched since
Winamp 2.0. They were taken from Webamp's skin sprite map rather than
reconstructed by eye, because a few of them are counter-intuitive - the
EQ's selected title bar sits *above* its unselected one in the sheet,
for instance.

Skins vary in what they include. Anything missing simply doesn't draw,
because a skin that omits `shufrep.bmp` should still give you its
background rather than an error.
"""
import configparser
import io
import os
import zipfile
from collections import Counter

from PyQt5.QtGui import QImage, QPainter, QPixmap, QColor
from PyQt5.QtCore import QRect, Qt


# The main window is a fixed size. Everything below is positioned
# inside it, in the skin's own coordinate space.
MAIN_WIDTH = 275
MAIN_HEIGHT = 116

# Transport buttons: where they sit in the window, and where their
# sprites sit inside cbuttons.bmp. The unpressed row is at y=0, the
# pressed row directly below it.
BUTTONS = {
    "previous": {"pos": (16, 88), "sprite": (0, 0, 23, 18), "pressed_dy": 18},
    "play":     {"pos": (39, 88), "sprite": (23, 0, 23, 18), "pressed_dy": 18},
    "pause":    {"pos": (62, 88), "sprite": (46, 0, 23, 18), "pressed_dy": 18},
    "stop":     {"pos": (85, 88), "sprite": (69, 0, 23, 18), "pressed_dy": 18},
    "next":     {"pos": (108, 88), "sprite": (92, 0, 22, 18), "pressed_dy": 18},
    # Eject is a different height, and its pressed row is 16 down rather
    # than 18 - a quirk of the original bitmap, not a mistake here.
    "eject":    {"pos": (136, 89), "sprite": (114, 0, 22, 16), "pressed_dy": 16},
}

# Shuffle and repeat live in shufrep.bmp with four states each.
SHUFREP = {
    "repeat": {"pos": (210, 89), "off": (0, 0, 28, 15), "on": (0, 30, 28, 15)},
    "shuffle": {"pos": (164, 89), "off": (28, 0, 47, 15), "on": (28, 30, 47, 15)},
}

# The balance slider, immediately right of the volume one.
BALANCE_POS = (177, 57)
BALANCE_TRACK_SIZE = (38, 13)

VOLUME_POS = (107, 57)
VOLUME_TRACK_SIZE = (68, 13)
POSBAR_POS = (16, 72)
POSBAR_SIZE = (248, 10)

# The bitmap font. Three rows of 5x6 characters; this is the layout
# every skin follows.
FONT_ROWS = (
    "abcdefghijklmnopqrstuvwxyz\"@ ",
    "0123456789\u2026.:()-'!_+\\/[]^&%,=$#",
    "\u00e5\u00f6\u00e4?* ",
)
CHAR_WIDTH = 5
CHAR_HEIGHT = 6

# The time display. Winamp put the elapsed time here; a live stream has
# no elapsed time worth showing, so it shows the clock instead - which
# is the one number you actually want on a radio.
DIGIT_WIDTH = 9
DIGIT_HEIGHT = 13
# Moved left from Winamp's 48 to make room for seconds at full size.
# The play-state glyph sits at x=24-33, so 36 is the first column that
# clears it.
TIME_POS = (36, 26)          # first digit; the rest follow
# Winamp's own digit positions: 48, 60, 78, 90. The wider gap in the
# middle is where the colon sits in the background bitmap, so digits
# spaced evenly would sit on top of it.
# Four digits at Winamp's own positions, then seconds squeezed in after
# them. The window has room to the right of the minutes; the seconds sit
# there rather than replacing anything.
# Six digits where Winamp put four, in three pairs. The gap inside a
# pair is one pixel; between pairs it is five, which is where the colons
# are drawn.
TIME_GAPS = (0, 10, 24, 34)
SECONDS_GAPS = (48, 58)
# Colons are drawn rather than taken from the bitmap: numbers.bmp has no
# colon glyph, and the one painted into the background sits where the
# original four-digit clock put it, so with six digits it lines up with
# nothing.
COLON_XS = (21, 45)
# The colon painted into main.bmp, in window coordinates. Covered with a
# patch of the panel's own dotted background rather than a flat fill.
PAINTED_COLON = (70, 30, 5, 6)
# Somewhere in the same panel that is clean dotted background, used as
# the source for that patch.
DOT_SOURCE = (150, 30)


def digit_sprite_rect(value):
    """Where digit `value` lives in numbers.bmp."""
    return (value * DIGIT_WIDTH, 0, DIGIT_WIDTH, DIGIT_HEIGHT)


# The clutterbar down the left of the visualizer, and the play state
# indicator beside it.
# Positions taken from Audacious, which has been rendering these skins
# correctly for twenty years - rather than measured off a screenshot.
CLUTTER_POS = (10, 22)
CLUTTER_SIZE = (8, 43)
# Each entry's vertical band within the strip, and the indicator sprite
# drawn when it is latched.
CLUTTER_BUTTONS = (
    ("o", 0, 10, None),
    # No indicator for A: it opens a link rather than holding a state.
    ("a", 10, 18, None),
    ("i", 18, 26, None),
    ("d", 26, 34, (328, 70, 8, 8, 26)),
    ("v", 34, 43, None),
)
PLAYSTATE_POS = (24, 28)

# The favourite star, in the empty panel right of the shuffle and
# repeat buttons where Winamp put nothing at all.
# Left of the transmitter mark, in the gap above the repeat button.
# Overlapping the mark made two things fight for the same corner.
STAR_RECT = (250, 43, 16, 15)

# The analyser panel: Winamp's own spectrum area, beside the clock.
# Measured off the skin: the dotted panel's usable area runs to x=108,
# not the 100 Winamp's own analyser used. Drawing 76 wide in an 84-wide
# space is why the waveform stopped three quarters of the way across.
# Measured off the skin: the dot grid ends at x=102 and the sunken
# border begins at 103, so 24..102 is the usable width. Going to 108
# painted straight over that border.
VIS_RECT = (24, 44, 78, 17)
VIS_BARS = 19

# The title bar's own controls, in main-window coordinates.
TITLEBAR_ICON = (6, 3, 9, 9)
TITLEBAR_MINIMIZE = (244, 3, 9, 9)
TITLEBAR_SHADE = (254, 3, 9, 9)
TITLEBAR_CLOSE = (264, 3, 9, 9)

# The bitrate and sample-rate readouts, and the logo hot spot.
KBPS_POS = (111, 43)
KHZ_POS = (156, 43)
# 20x25 at 247,83 - Audacious's "about" button, which is what the mark
# actually is. My own measurement was a few pixels out in both
# directions, which is why it overhung the window border.
# The mark's box, which is its hitbox too. Sized to the artwork with a
# couple of pixels of slack so it is easy to hit, and kept clear of the
# window's right border.
LOGO_RECT = (244, 88, 19, 18)

# Where the scrolling song title is drawn.
TITLE_POS = (111, 27)
TITLE_CHARS = 31


# -- windowshade (the collapsed one-line mode) -------------------------
#
# Every window could be rolled up to its title bar. The main window's
# shade is not just the title bar cropped: it has its own background
# with a slot for the time and a miniature position thumb, which is why
# it needs its own sprites rather than reusing MAIN_TITLE_BAR.
SHADE_SPRITES = {
    "main_background": ("titlebar.bmp", (27, 42, 275, 14)),
    "main_button": ("titlebar.bmp", (0, 18, 9, 9)),
    "main_button_pressed": ("titlebar.bmp", (9, 18, 9, 9)),
    "main_button_selected": ("titlebar.bmp", (0, 27, 9, 9)),
    "main_position_thumb": ("titlebar.bmp", (20, 36, 3, 7)),
    "playlist_background": ("pledit.bmp", (72, 57, 25, 14)),
    "playlist_collapse": ("pledit.bmp", (62, 42, 9, 9)),
    "playlist_expand": ("pledit.bmp", (150, 42, 9, 9)),
}

SHADE_HEIGHT = 14
# The shade button sits at the right of the title bar, next to close.
SHADE_BUTTON_POS = (244, 3)
# In shade mode the title text is shorter and sits inline.
SHADE_TITLE_POS = (79, 4)
SHADE_TITLE_CHARS = 29


# -- equaliser window (eqmain.bmp) -------------------------------------
EQ_WIDTH = 275
EQ_HEIGHT = 116

EQ_SPRITES = {
    "background": (0, 0, 275, 116),
    # Note the ordering: selected sits above unselected in the sheet.
    "titlebar": (0, 149, 275, 14),
    "titlebar_selected": (0, 134, 275, 14),
    "slider_background": (13, 164, 209, 129),
    "thumb": (0, 164, 11, 11),
    "thumb_pressed": (0, 176, 11, 11),
    "close": (0, 116, 9, 9),
    "close_active": (0, 125, 9, 9),
    "on": (10, 119, 26, 12),
    "on_selected": (69, 119, 26, 12),
    "auto": (36, 119, 32, 12),
    "auto_selected": (95, 119, 32, 12),
    "graph_background": (0, 294, 113, 19),
    # A one-pixel column: Winamp picked the curve's color by height
    # from this, which is how a skin tints its own EQ graph.
    "graph_line_colors": (115, 294, 1, 19),
    "presets": (224, 164, 44, 12),
}

# Where things sit in the EQ window. The preamp slider is separated
# from the ten bands, which start at x=78 and repeat every 18 pixels.
EQ_ON_POS = (14, 18)
EQ_AUTO_POS = (39, 18)
# From Audacious: the equaliser's own title-bar buttons.
EQ_CLOSE_POS = (264, 3)
EQ_SHADE_POS = (254, 3)
EQ_CLOSE_SPRITE = (0, 116, 9, 9)
EQ_SHADE_SPRITE = (254, 137, 9, 9)

EQ_PRESETS_POS = (217, 18)
EQ_GRAPH_POS = (86, 17)
EQ_PREAMP_POS = (21, 38)
EQ_BAND_X0 = 78
EQ_BAND_SPACING = 18
EQ_BAND_Y = 38
EQ_SLIDER_TRAVEL = 51          # thumb top at max gain .. min gain


# -- playlist window (pledit.bmp) --------------------------------------
#
# The only resizable window Winamp had, so it is built from tiles: the
# corners are fixed and the edges repeat to fill whatever width and
# height the user drags it to.
PLAYLIST_SPRITES = {
    "top_left": (0, 21, 25, 20),
    "top_left_selected": (0, 0, 25, 20),
    "top_right": (153, 21, 25, 20),
    "top_right_selected": (153, 0, 25, 20),
    "top_tile": (127, 21, 25, 20),
    # The active variant of the repeated tile. Without it the tiles
    # stayed dark while the corners and the title lit up, which is the
    # mismatched title bar - half of it active, half not.
    "top_tile_selected": (127, 0, 25, 20),
    "title_bar": (26, 21, 100, 20),
    "title_bar_selected": (26, 0, 100, 20),
    "left_tile": (0, 42, 12, 29),
    "right_tile": (31, 42, 20, 29),
    "bottom_tile": (179, 0, 25, 38),
    # The bottom corners carry the buttons and the resize grip, which is
    # why the window looked cut off without them.
    "bottom_left": (0, 72, 125, 38),
    "bottom_right": (126, 72, 150, 38),
    "scroll_handle": (52, 53, 8, 18),
    "scroll_handle_selected": (61, 53, 8, 18),
    "close": (52, 42, 9, 9),
    "close_selected": (52, 42, 9, 9),
}

# The close button and the scrollbar, in window coordinates.
PLAYLIST_CLOSE_POS = (-11, 3)        # from the right edge
PLAYLIST_SCROLL_X = -15              # from the right edge
PLAYLIST_SCROLL_TOP = 20
PLAYLIST_SCROLL_BOTTOM = 38

# The five button groups along the bottom of the playlist. Winamp drew
# each as a strip that popped a menu upwards; here each is a button that
# opens a normal menu, which behaves the same and is far easier to hit.
# Measured off the rendered corner sprites rather than guessed: the art
# sits at these columns, and my earlier figures were two pixels left and
# four short, so clicks landed beside the buttons instead of on them.
PLAYLIST_BUTTONS = {
    "add":  {"sprite": (0, 111, 22, 18), "x": 14},
    "rem":  {"sprite": (54, 111, 22, 18), "x": 43},
    "sel":  {"sprite": (104, 111, 22, 18), "x": 72},
    "misc": {"sprite": (154, 111, 22, 18), "x": 101},
    "list": {"sprite": (204, 111, 22, 18), "x": -44},   # from the right
}
PLAYLIST_BUTTON_SIZE = (21, 18)
# Measured up from the bottom edge: the art runs y 150-167 in a 180-tall
# window, so 30 from the bottom.
PLAYLIST_BUTTON_BOTTOM = 30

PLAYLIST_MIN_WIDTH = 275
PLAYLIST_MIN_HEIGHT = 116
PLAYLIST_TITLE_HEIGHT = 20
PLAYLIST_BOTTOM_HEIGHT = 38
# The bottom border of a window that wears the playlist's frame but has
# none of its buttons - the visualizer, lyrics and history (task 98a).
# Only the last rows of the bottom tile, which are the actual border,
# and the grip cropped from the corner. One number and one painter for
# all three, so they cannot drift apart again.
THIN_BOTTOM_HEIGHT = 14


def paint_thin_bottom(painter, skin, width, height, bottom=None):
    """The thin bottom border and resize grip, at the window's foot.

    `bottom` is the bottom tile to use (a lit variant, say); the skin's
    plain playlist tile otherwise.
    """
    size = THIN_BOTTOM_HEIGHT
    bottom = bottom or skin.playlist("bottom_tile")
    if bottom:
        edge = bottom.copy(0, max(0, bottom.height() - size),
                           bottom.width(), min(size, bottom.height()))
        x = 0
        while x < width:
            painter.drawPixmap(x, height - edge.height(), edge)
            x += max(1, edge.width())
    corner = skin.playlist("bottom_right")
    if corner:
        grip_size = min(size, corner.height())
        grip = corner.copy(corner.width() - grip_size,
                           corner.height() - grip_size,
                           grip_size, grip_size)
        painter.drawPixmap(width - grip_size, height - grip_size, grip)

# The little transport row and time readout along the bottom, measured
# from the window's right edge and bottom as Audacious does it. Each
# entry is (dx from right, dy from bottom, width).
PLAYLIST_TRANSPORT = {
    "previous": (-144, -16, 8),
    "play": (-138, -16, 10),
    "pause": (-128, -16, 10),
    "stop": (-118, -16, 9),
    "next": (-109, -16, 8),
    "eject": (-100, -16, 9),
}
PLAYLIST_TRANSPORT_HEIGHT = 8
# Where the elapsed time is written.
PLAYLIST_TIME_MIN = (-82, -15)
PLAYLIST_TIME_SEC = (-64, -15)
PLAYLIST_LEFT_WIDTH = 12
PLAYLIST_RIGHT_WIDTH = 20


# -- a different name in the playlist's title plate ---------------------
#
# A skin names each of Winamp's three windows in its own artwork: the
# playlist's title bar has a 100x20 plate in the middle with PLAYLIST -
# or whatever the skin calls it - painted in. The tags, lyrics and
# history windows borrow that frame, so they borrowed the word too, and
# every attempt to cover it (a black band, green song-title letters)
# made those title bars look like they came from another skin.
#
# So the plate is kept exactly as the skin drew it and only the letters
# change: found by colour, painted out with the plate's own background,
# and the new name written in the plate's own ink.
#
# In the 5x6 capitals of FreqAmp's main title bar since build 103 (task
# 98b): F, R, E, Q, A, M and P were copied off that title, the rest drawn
# to match. Before, these plates used the 3x5 letters skins paint on
# theirs, so FREQAMP on every other title bar was a size smaller than on
# the main window's.

PLATE_FONT = {
    'A': ('.###.', '#...#', '#...#', '#####', '#...#', '#...#'),
    'B': ('####.', '#...#', '####.', '#...#', '#...#', '####.'),
    'C': ('.###.', '#...#', '#....', '#....', '#...#', '.###.'),
    'D': ('####.', '#...#', '#...#', '#...#', '#...#', '####.'),
    'E': ('#####', '#....', '####.', '#....', '#....', '#####'),
    'F': ('#####', '#....', '####.', '#....', '#....', '#....'),
    'G': ('.###.', '#....', '#....', '#..##', '#...#', '.###.'),
    'H': ('#...#', '#...#', '#####', '#...#', '#...#', '#...#'),
    'I': ('###', '.#.', '.#.', '.#.', '.#.', '###'),
    'J': ('....#', '....#', '....#', '....#', '#...#', '.###.'),
    'K': ('#...#', '#..#.', '###..', '#..#.', '#...#', '#...#'),
    'L': ('#...', '#...', '#...', '#...', '#...', '####'),
    'M': ('#...#', '##.##', '#.#.#', '#...#', '#...#', '#...#'),
    'N': ('#...#', '##..#', '#.#.#', '#..##', '#...#', '#...#'),
    'O': ('.###.', '#...#', '#...#', '#...#', '#...#', '.###.'),
    'P': ('####.', '#...#', '#...#', '####.', '#....', '#....'),
    'Q': ('.###.', '#...#', '#...#', '#.#.#', '#..#.', '.##.#'),
    'R': ('####.', '#...#', '####.', '#.#..', '#..#.', '#...#'),
    'S': ('.###.', '#....', '.###.', '....#', '....#', '####.'),
    'T': ('#####', '..#..', '..#..', '..#..', '..#..', '..#..'),
    'U': ('#...#', '#...#', '#...#', '#...#', '#...#', '.###.'),
    'V': ('#...#', '#...#', '#...#', '#...#', '.#.#.', '..#..'),
    'W': ('#...#', '#...#', '#...#', '#.#.#', '##.##', '#...#'),
    'X': ('#...#', '.#.#.', '..#..', '.#.#.', '#...#', '#...#'),
    'Y': ('#...#', '#...#', '.#.#.', '..#..', '..#..', '..#..'),
    'Z': ('#####', '...#.', '..#..', '.#...', '#....', '#####'),
    '0': ('.###.', '#..##', '#.#.#', '##..#', '#...#', '.###.'),
    '1': ('.#.', '##.', '.#.', '.#.', '.#.', '###'),
    '2': ('.###.', '#...#', '...#.', '..#..', '.#...', '#####'),
    '3': ('####.', '....#', '.###.', '....#', '....#', '####.'),
    '4': ('#...#', '#...#', '#####', '....#', '....#', '....#'),
    '5': ('#####', '#....', '####.', '....#', '....#', '####.'),
    '6': ('.###.', '#....', '####.', '#...#', '#...#', '.###.'),
    '7': ('#####', '....#', '...#.', '..#..', '..#..', '..#..'),
    '8': ('.###.', '#...#', '.###.', '#...#', '#...#', '.###.'),
    '9': ('.###.', '#...#', '#...#', '.####', '....#', '.###.'),
    '-': ('...', '...', '###', '...', '...', '...'),
    '.': ('.', '.', '.', '.', '.', '#'),
    ':': ('.', '#', '.', '.', '#', '.'),
    '!': ('#', '#', '#', '#', '.', '#'),
    '?': ('.###.', '#...#', '...#.', '..#..', '.....', '..#..'),
    "'": ('#', '#', '.', '.', '.', '.'),
    '/': ('....#', '...#.', '..#..', '.#...', '#....', '#....'),
    '(': ('.#', '#.', '#.', '#.', '#.', '.#'),
    ')': ('#.', '.#', '.#', '.#', '.#', '#.'),
}
PLATE_GLYPH_HEIGHT = 6
# What a space adds, measured off the same plate: the words there sit
# three clear columns apart, one of which is the letter gap anyway.
PLATE_SPACE = 2
# How far a colour has to be from the plate's own to count as lettering,
# as a sum of RGB differences.
PLATE_INK = 60


def plate_text_width(text):
    """How wide `text` comes out in PLATE_FONT, in pixels."""
    x = right = 0
    for character in text.upper():
        glyph = PLATE_FONT.get(character)
        if glyph is None:
            x += PLATE_SPACE
            continue
        right = x + len(glyph[0])
        x = right + 1
    return right


def _rgb(value):
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def _apart(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _toward(background, ink, pixel):
    """How far `pixel` sits along the way from background to ink, and
    how far off that path it is.

    Lettering - solid or anti-aliased - lies on that path. A plate's
    stripes, bevels and shadows lie off it, even when they are just as
    far from the background, and those have to survive the swap.
    """
    span = [ink[i] - background[i] for i in range(3)]
    length = sum(s * s for s in span)
    if length == 0:
        return 0.0, 999.0
    offset = [pixel[i] - background[i] for i in range(3)]
    along = sum(offset[i] * span[i] for i in range(3)) / length
    miss = sum((offset[i] - along * span[i]) ** 2 for i in range(3)) ** 0.5
    return along, miss


def _is_letter(pixel, background, ink, strength=0.1):
    along, miss = _toward(background, ink, pixel)
    return strength <= along <= 1.4 and miss <= 50


def _plate_ink(grid, left, right):
    """The lettering's colour, or None if the plate has no lettering.

    The colour that is both plentiful and far from the plate's middle
    tone. Plentiful alone picks the background; far alone picks a
    single stray pixel; the two together pick the letters' solid ink,
    which anti-aliased fringes only surround.
    """
    cells = [c for line in grid for c in line[left:right]]
    middle = tuple(sorted(c[i] for c in cells)[len(cells) // 2]
                   for i in range(3))
    counts = Counter(cells)
    best, score = None, 0
    for colour, count in counts.items():
        distance = _apart(colour, middle)
        if distance > PLATE_INK * 1.5 and count * distance > score:
            best, score = colour, count * distance
    return best, middle


def _lettering_rows(grid, left, right, ink, middle):
    """First and last row of the plate's lettering, or None."""
    rows = [y for y, line in enumerate(grid)
            if any(_apart(c, ink) <= 48 for c in line[left:right])]
    if not rows:
        return None
    # The longest run, letting one blank row through.
    best, run = [], []
    for y in rows:
        if run and y - run[-1] > 2:
            best = run if len(run) > len(best) else best
            run = []
        run.append(y)
    best = run if len(run) > len(best) else best
    top, bottom = best[0], best[-1]
    # Take in anti-aliased fringe just outside the solid ink.
    for _ in range(2):
        if top > 0 and any(_is_letter(c, middle, ink, 0.3)
                           for c in grid[top - 1][left:right]):
            top -= 1
        if bottom < len(grid) - 1 and any(
                _is_letter(c, middle, ink, 0.3)
                for c in grid[bottom + 1][left:right]):
            bottom += 1
    return top, bottom


def _stripe_limits(grid, tile, top, bottom):
    """The plate columns clear of the title bar's stripes.

    Many skins run the stripes of the repeating title tile a few pixels
    into the plate, stopping short of the word. Those columns look just
    like the tile does on the same rows, and are left alone - one of the
    stripes can be the same white as the letters, so colour alone
    cannot tell them apart.
    """
    width = len(grid[0])
    if tile is None:
        return 0, width - 1
    image = tile.toImage().convertToFormat(QImage.Format_RGB32)
    if image.height() < bottom + 1 or image.width() < 1:
        return 0, width - 1
    column = [_rgb(image.pixel(0, y)) for y in range(top, bottom + 1)]
    # A plain tile has no stripes to protect, and treating it as one
    # would mark every plain column of the plate as a stripe.
    if max(_apart(a, b) for a in column for b in column) <= PLATE_INK:
        return 0, width - 1

    def stripe(x):
        hits = sum(1 for y, want in zip(range(top, bottom + 1), column)
                   if _apart(grid[y][x], want) <= 30)
        return hits * 2 >= len(column)

    first, last = 0, width - 1
    while first < width // 3 and stripe(first):
        first += 1
    while last > width - width // 3 and stripe(last):
        last -= 1
    return first, last


def widen_plate(pixmap, label, margin=6):
    """The plate, wide enough for `label` with `margin` either side.

    A skin's plate is 100 pixels; FREQAMP VISUALIZER in the 5x6 letters
    is 98. The plate's middle column is repeated to make room, so its
    rounded ends stay as drawn; the old word's letters in that column
    are painted out with the rest of the word by relabel_plate.
    """
    extra = plate_text_width(label) + 2 * margin - pixmap.width()
    if extra <= 0:
        return pixmap
    width, height = pixmap.width(), pixmap.height()
    middle = width // 2
    wide = QPixmap(width + extra, height)
    wide.fill(Qt.transparent)
    painter = QPainter(wide)
    painter.drawPixmap(0, 0, pixmap, 0, 0, middle, height)
    for x in range(extra):
        painter.drawPixmap(middle + x, 0, pixmap, middle, 0, 1, height)
    painter.drawPixmap(middle + extra, 0, pixmap, middle, 0,
                       width - middle, height)
    painter.end()
    return wide


def relabel_plate(pixmap, label, dim=False, tile=None):
    """The playlist's title plate with `label` written in place of the
    skin's own word. The plate's shape and colours are left alone.

    `tile` is the title bar's repeating tile, so stripes that run into
    the plate can be recognised and kept. `dim` only matters for a plate
    with nothing written on it, where there is no ink to copy: the name
    is then drawn a little fainter on the unfocused bar.
    """
    image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    width, height = image.width(), image.height()
    if width < 12 or height < PLATE_GLYPH_HEIGHT:
        return pixmap
    grid = [[_rgb(image.pixel(x, y)) for x in range(width)]
            for y in range(height)]
    left, right = width // 4, width - width // 4

    ink, middle = _plate_ink(grid, left, right)
    band = _lettering_rows(grid, left, right, ink, middle) if ink else None

    if band is None:
        # Nothing written on it: centre the name, in whatever contrasts
        # with the plate.
        towards = 255 if QColor(*middle).lightness() < 128 else 0
        mix = 0.45 if dim else 0.75
        ink = tuple(int(c + (towards - c) * mix) for c in middle)
        top = (height - PLATE_GLYPH_HEIGHT) // 2
        centre = width // 2
    else:
        top, bottom = band
        lo, hi = _stripe_limits(grid, tile, top, bottom)
        # The background behind the letters, one pixel at a time: blended
        # between the clean rows just above and below them, so a plate
        # that is shaded or textured stays that way where the old word
        # was.
        above = top - 1 if top > 0 else bottom + 1
        below = bottom + 1 if bottom < height - 1 else above
        above = max(0, min(height - 1, above))
        below = max(0, min(height - 1, below))

        def behind(x, y):
            if below == above:
                return grid[above][x]
            share = (y - above) / (below - above)
            a, b = grid[above][x], grid[below][x]
            return tuple(int(a[i] + (b[i] - a[i]) * share) for i in range(3))

        # Which columns hold lettering, grouped so that something further
        # out on the plate is not taken for part of the word. The
        # heaviest group is the word.
        counts = {}
        for y in range(top, bottom + 1):
            for x in range(lo, hi + 1):
                if _is_letter(grid[y][x], behind(x, y), ink, 0.4):
                    counts[x] = counts.get(x, 0) + 1
        groups, group = [], []
        for x in sorted(counts):
            if group and x - group[-1] > 8:
                groups.append(group)
                group = []
            group.append(x)
        if group:
            groups.append(group)
        word = max(groups, key=lambda g: sum(counts[x] for x in g),
                   default=[width // 2])
        first, last = word[0], word[-1]
        centre = (first + last + 1) // 2

        # Paint the old word out, and wherever the new one will go.
        # Only lettering is touched; anything else there is the skin's.
        needed = plate_text_width(label)
        first = max(lo, min(first, centre - needed // 2) - 1)
        last = min(hi, max(last, centre + needed // 2) + 1)
        for y in range(top, bottom + 1):
            for x in range(first, last + 1):
                under = behind(x, y)
                if _is_letter(grid[y][x], under, ink):
                    image.setPixel(x, y, QColor(*under).rgb())
        top += max(0, (bottom - top + 1 - PLATE_GLYPH_HEIGHT) // 2)

    top = max(0, min(height - PLATE_GLYPH_HEIGHT, top))
    colour = QColor(*ink).rgb()
    x = centre - plate_text_width(label) // 2
    for character in label.upper():
        glyph = PLATE_FONT.get(character)
        if glyph is None:
            x += PLATE_SPACE
            continue
        for dy, line in enumerate(glyph):
            for dx, bit in enumerate(line):
                if bit == "#" and 0 <= x + dx < width:
                    image.setPixel(x + dx, top + dy, colour)
        x += len(glyph[0]) + 1
    return QPixmap.fromImage(image)


class SkinError(Exception):
    pass


class WinampSkin:
    """One loaded skin: sprites, colors, and how to draw them."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self._images = {}
        self._cursor_cache = {}
        self._files = {}
        # Renamed title plates, by (name, lit). Working one out reads
        # every pixel of the plate, which is too slow to do per paint.
        self._plate_cache = {}
        self.playlist_colors = {}
        self._load()

    # -- loading ------------------------------------------------------

    def _load(self):
        if not zipfile.is_zipfile(self.path):
            raise SkinError("That isn't a Winamp skin (.wsz files are zips).")

        with self._open_archive() as archive:
            # Skin archives are wildly inconsistent about case and about
            # whether everything sits in a subfolder, so index by the
            # lowercased basename rather than trusting the paths.
            index = {}
            try:
                entries = archive.namelist()
            except LookupError as exc:
                raise SkinError(
                    "This skin's filenames use an encoding this build of "
                    f"Python doesn't have ({exc}). Re-zipping it fixes it."
                ) from exc
            for entry in entries:
                if entry.endswith("/"):
                    continue
                index[os.path.basename(entry).lower()] = entry
            # Kept for the cursors, which are read later, on demand.
            self._files = dict(index)

            for key in ("main.bmp", "cbuttons.bmp", "titlebar.bmp", "text.bmp",
                        "volume.bmp", "balance.bmp", "posbar.bmp",
                        "shufrep.bmp",
                        "numbers.bmp", "nums_ex.bmp", "monoster.bmp",
                        "playpaus.bmp", "eqmain.bmp", "eq_ex.bmp",
                        "pledit.bmp"):
                entry = index.get(key)
                if not entry:
                    continue
                image = QImage()
                if image.loadFromData(archive.read(entry)):
                    self._images[key] = image

            if "main.bmp" not in self._images:
                raise SkinError(
                    "No main.bmp in that skin - it may be a Winamp 3/5 skin, "
                    "which uses a different format."
                )

            entry = index.get("pledit.txt")
            if entry:
                self.playlist_colors = self._parse_pledit(archive.read(entry))

    def _open_archive(self):
        """Open the skin, tolerating old-DOS filename encodings.

        Zip entries written before UTF-8 flags were common are decoded
        as cp437 - and a stripped-down Python (which is what an AppImage
        ships) may not carry that codec, so opening a skin from 1999
        dies with "unknown encoding: cp437".

        Forcing UTF-8 for the metadata avoids the codec entirely.
        Filenames in these archives are ASCII in practice, so nothing is
        lost; only the odd accented one would differ, and those are
        matched case-insensitively by basename anyway.
        """
        try:
            return zipfile.ZipFile(self.path, metadata_encoding="utf-8")
        except (TypeError, ValueError):
            # metadata_encoding needs Python 3.11; older ones decode
            # with cp437 and will have it available.
            return zipfile.ZipFile(self.path)
        except LookupError as exc:
            raise SkinError(
                "This skin's filenames use an encoding this build of "
                f"Python doesn't have ({exc}). Re-zipping the skin fixes it."
            ) from exc

    @staticmethod
    def _parse_pledit(raw):
        """Colors for the playlist, as #rrggbb strings.

        pledit.txt is an INI file, but skins in the wild have stray
        characters and mixed encodings in it, so this is deliberately
        forgiving: anything unparseable just means the default colors.
        """
        try:
            text = raw.decode("utf-8", errors="replace")
            parser = configparser.ConfigParser(strict=False)
            parser.read_string(text)
        except Exception:
            return {}

        for section in parser.sections():
            if section.lower() == "text":
                out = {}
                for key, value in parser.items(section):
                    value = value.strip()
                    if value.startswith("#") and len(value) in (4, 7):
                        out[key.lower()] = value
                return out
        return {}

    # -- sprites ------------------------------------------------------

    def _sprite(self, source, rect):
        image = self._images.get(source)
        if image is None:
            return None
        x, y, w, h = rect
        if x + w > image.width() or y + h > image.height():
            return None
        return QPixmap.fromImage(image.copy(QRect(x, y, w, h)))

    def background(self):
        return self._sprite("main.bmp", (0, 0, MAIN_WIDTH, MAIN_HEIGHT))

    def titlebar(self, active=True):
        # Active bar at y=0, inactive at y=15.
        return self._sprite("titlebar.bmp", (27, 0 if active else 15, 275, 14))

    def button(self, name, pressed=False):
        spec = BUTTONS.get(name)
        if not spec:
            return None
        x, y, w, h = spec["sprite"]
        if pressed:
            y += spec["pressed_dy"]
        return self._sprite("cbuttons.bmp", (x, y, w, h))

    def button_rect(self, name):
        spec = BUTTONS.get(name)
        if not spec:
            return None
        x, y = spec["pos"]
        _sx, _sy, w, h = spec["sprite"]
        return QRect(x, y, w, h)

    def toggle(self, name, on=False):
        spec = SHUFREP.get(name)
        if not spec:
            return None
        return self._sprite("shufrep.bmp", spec["on"] if on else spec["off"])

    def toggle_rect(self, name):
        spec = SHUFREP.get(name)
        if not spec:
            return None
        x, y = spec["pos"]
        _sx, _sy, w, h = spec["off"]
        return QRect(x, y, w, h)

    def volume_track(self, fraction):
        """volume.bmp holds 28 frames of the groove, one per level.

        The frames run loud to quiet, not quiet to loud - frame 0 is the
        full bar and frame 27 is nearly empty. Reading them in file
        order made the slider show most color at silence and least at
        full volume, which is exactly backwards.
        """
        # Frame 0 is quiet, frame 27 is loud - green at the bottom of
        # the range and red at the top, which is what a level meter
        # means everywhere. Reading them backwards put red at silence
        # and green at full volume.
        frame = max(0, min(27, int(round(fraction * 27))))
        w, h = VOLUME_TRACK_SIZE
        return self._sprite("volume.bmp", (0, frame * 15, w, h))

    def balance_track(self, fraction):
        """balance.bmp holds the same 28 frames as volume.bmp.

        `fraction` is 0 at hard left, 0.5 centered, 1 at hard right; the
        frame is picked by distance from the middle, which is how the
        original showed how far off-centre it was.
        """
        # NOT the same ordering as volume, despite the identical sheet
        # layout. Balance's frame 0 is the neutral green bar and the
        # frames run outward from there, so distance from centre counts
        # up. Inverting it the way volume needs painted the middle red -
        # the one position where nothing is off-centre at all.
        offset = abs(fraction - 0.5) * 2
        frame = max(0, min(27, int(round(offset * 27))))
        w, h = BALANCE_TRACK_SIZE
        return self._sprite("balance.bmp", (9, frame * 15, w, h))

    def balance_knob(self, pressed=False):
        # Same ordering as volume: x=0 is the pressed knob.
        return self._sprite("balance.bmp", (0 if pressed else 15, 422, 14, 11))

    def volume_knob(self, pressed=False):
        # x=0 is the pressed (dark) knob and x=15 the normal one, not
        # the other way round - which is why both sliders sat there
        # looking permanently held down in black.
        return self._sprite("volume.bmp", (0 if pressed else 15, 422, 14, 11))

    def posbar(self):
        w, h = POSBAR_SIZE
        return self._sprite("posbar.bmp", (0, 0, w, h))

    def posbar_knob(self, pressed=False):
        """The dial's knob. 29x10, to the right of the groove."""
        return self._sprite("posbar.bmp",
                            (278 if pressed else 248, 0, 29, 10))

    def clutterbar(self, pushed=False):
        """The O A I D V strip, from titlebar.bmp.

        It is a sprite, not part of the background - which is why
        nothing appeared where it should have been.

        x=304 carries the letters; x=312 is the blank strip shown while
        the bar is pressed. Reading Audacious's two-step draw as
        "312 is the normal one" got these the wrong way round, so the
        blank one was on screen the whole time.
        """
        return self._sprite("titlebar.bmp",
                            (312 if pushed else 304, 0, 8, 43))

    def clutter_hover(self, index):
        """The strip with one entry lit, for hover feedback."""
        return self._sprite("titlebar.bmp", (304 + 8 * index, 44, 8, 43))

    def clutter_indicator(self, spec):
        x, y, w, h, _dy = spec
        return self._sprite("titlebar.bmp", (x, y, w, h))

    def play_state(self, state):
        """The little play / pause / stop glyph, from playpaus.bmp."""
        # Measured column by column off the bitmap.
        #
        # The play triangle occupies x=4..8 and nothing more; x=9..11 is
        # the pause glyph sitting immediately after it. An eight-wide
        # slice took the triangle plus the first bar of the pause icon,
        # which is the "line to the right of the triangle".
        rects = {"play": (4, 0, 5, 9), "pause": (9, 0, 5, 9),
                 "stop": (14, 0, 5, 9), "connecting": (39, 0, 5, 9)}
        rect = rects.get(state)
        return self._sprite("playpaus.bmp", rect) if rect else None

    def digit(self, value):
        """One clock digit, or None if the skin has no numbers.bmp."""
        if not 0 <= value <= 9:
            return None
        for sheet in ("numbers.bmp", "nums_ex.bmp"):
            sprite = self._sprite(sheet, digit_sprite_rect(value))
            if sprite is not None:
                return sprite
        return None

    def char(self, character):
        """One glyph from the bitmap font, or None.

        A space draws nothing at all rather than looking itself up. The
        blank cell in these sheets is not always blank - several skins
        put a stray mark there - and a title full of them reads as
        gibberish. Skipping it leaves the same gap either way.
        """
        if character in (" ", "\t"):
            return None
        lowered = character.lower()
        for row, characters in enumerate(FONT_ROWS):
            column = characters.find(lowered)
            if column >= 0:
                return self._sprite(
                    "text.bmp",
                    (column * CHAR_WIDTH, row * CHAR_HEIGHT,
                     CHAR_WIDTH, CHAR_HEIGHT),
                )
        return None

    # -- windowshade ---------------------------------------------------

    def shade(self, name):
        entry = SHADE_SPRITES.get(name)
        if not entry:
            return None
        sheet, rect = entry
        return self._sprite(sheet, rect)

    # -- equaliser ----------------------------------------------------

    def has_equalizer(self):
        return "eqmain.bmp" in self._images

    def eq(self, name):
        rect = EQ_SPRITES.get(name)
        return self._sprite("eqmain.bmp", rect) if rect else None

    def eq_slider_frame(self, index):
        """One of the 28 groove frames behind an EQ slider.

        The background sheet holds them as a 14-wide column grid: the
        first 14 columns are one set of frames, the rest the other, which
        is why the index wraps rather than simply scrolling down.
        """
        column = (index % 14) * 15
        row = 0 if index < 14 else 65
        return self._sprite("eqmain.bmp", (13 + column, 164 + row, 14, 63))

    # -- playlist -----------------------------------------------------

    def has_playlist(self):
        return "pledit.bmp" in self._images

    def playlist(self, name):
        rect = PLAYLIST_SPRITES.get(name)
        return self._sprite("pledit.bmp", rect) if rect else None

    def is_own(self):
        """Is this the Frequencies skin (FreqAmp)?"""
        return (os.path.basename(self.path).lower()
                == OWN_SKIN.lower())

    def playlist_title(self, label=None, active=True):
        """The playlist's title plate, lit or not, optionally renamed.

        No label: the plate exactly as the skin drew it, which is right
        for the playlist itself. With one: the same plate with that name
        in place of the skin's own - see relabel_plate.
        """
        plate = (self.playlist("title_bar_selected" if active
                               else "title_bar")
                 or self.playlist("title_bar"))
        # Only FreqAmp gets its own names on the plate (task 199): on any
        # other skin the Lyrics, History, Tags and Visualizer windows
        # keep that skin's own title bar, untouched.
        if not self.is_own():
            return plate
        if plate is None or not label:
            return plate
        key = (label, bool(active))
        if key not in self._plate_cache:
            try:
                plate = widen_plate(plate, label)
                tile = (self.playlist("top_tile_selected" if active
                                      else "top_tile")
                        or self.playlist("top_tile"))
                self._plate_cache[key] = relabel_plate(
                    plate, label, dim=not active, tile=tile)
            except Exception:                   # noqa: BLE001
                # A plate this cannot read is still a plate: better the
                # skin's own word than no title bar at all.
                self._plate_cache[key] = plate
        return self._plate_cache[key]

    def playlist_button(self, name):
        spec = PLAYLIST_BUTTONS.get(name)
        return self._sprite("pledit.bmp", spec["sprite"]) if spec else None

    # Which cursor file goes with which part of the window. Winamp skins
    # ship these as Windows .cur files inside the archive (some as
    # animated .ani). Several names are tried in order: a skin with no
    # playlist title-bar cursor (PTBAR) still has the main one (TITLEBAR),
    # and that is the right pointer for dragging any window by its title.
    CURSORS = {
        # The idle cursors, one per window. Plenty of skins leave them
        # out - the original Winamp base skin has none of them. Then the
        # plain parts wear the skin's own MAINMENU.CUR (task 53, build 62).
        "normal": ("normal.cur",),
        "eq_normal": ("eqnormal.cur",),
        "playlist_normal": ("pnormal.cur",),

        "titlebar": ("titlebar.cur",),
        "close": ("close.cur",),
        "volume": ("volbal.cur", "volbar.cur"),
        "posbar": ("posbar.cur",),
        "menu": ("mainmenu.cur", "mmenu.cur"),
        "buttons": ("winbut.cur",),
        "eq_slider": ("eqslid.cur",),
        "eq_title": ("eqtitle.cur", "titlebar.cur"),
        "playlist_title": ("ptbar.cur", "titlebar.cur"),
        "playlist_scroll": ("pvscroll.cur",),
        "playlist_resize": ("psize.cur",),
    }
    IDLE_CURSORS = ("normal", "eq_normal", "playlist_normal")
    # The skin's own default pointer, for any part it has no file for:
    # MAINMENU.CUR (the user, 2026-10-02: "use cursors of that file name
    # as the default"). Nothing of Winamp's own ships in the app.
    DEFAULT_CURSOR = "menu"

    def cursor_files(self):
        """The cursor files this skin really has, by lowercase name."""
        return sorted(name for name in self._files
                      if name.endswith((".cur", ".ani")))

    def describe_cursors(self):
        """One log line's worth: which file each part uses (tasks 43, 51, 53)."""
        files = self.cursor_files()
        parts = []
        for name in self.CURSORS:
            idle = ("eq_normal" if name.startswith("eq_") else
                    "playlist_normal" if name.startswith("playlist_") else
                    "normal")
            cursor, source = self.pick_cursor(name, idle)
            parts.append(f"{name}={source}")
        known = {f[:-4] for names in self.CURSORS.values() for f in names}
        unused = [f for f in files if f[:-4] not in known]
        text = (f"pointers: {', '.join(parts)}"
                f" | files: {', '.join(files) or 'none'}")
        if unused:
            text += f" | not used: {', '.join(unused)}"
        return text

    def _cursor_data(self, filename):
        """(bytes, file name as in the skin) of one cursor, from .cur or
        else the .ani beside it; (None, None) if the skin has neither."""
        stem = filename[:-4]
        try:
            with self._open_archive() as archive:
                for candidate in (stem + ".cur", stem + ".ani"):
                    entry = self._files.get(candidate)
                    if not entry:
                        continue
                    data = archive.read(entry)
                    if candidate.endswith(".ani"):
                        data = first_ani_frame(data)
                    if data:
                        shown = str(entry).replace("\\", "/").rsplit("/", 1)[-1]
                        return data, shown
        except Exception:  # noqa: BLE001
            return None, None
        return None, None

    def own_cursor(self, name):
        """(QCursor, file name) from the skin's OWN file for `name`, or
        (None, None).

        Qt reads .cur through its ICO handler. Animated .ani cursors are
        a RIFF list of .cur frames; Qt cannot read those, so the first
        frame is used as a still pointer. No fallback here - see
        pick_cursor.
        """
        filenames = self.CURSORS.get(name)
        if not filenames:
            return None, None
        cached = self._cursor_cache.get(name)
        if cached is not None:
            return cached if cached else (None, None)

        found = (None, None)
        for filename in filenames:
            data, shown = self._cursor_data(filename)
            if data:
                cursor = cursor_from_data(data)
                if cursor is not None:
                    found = (cursor, shown)
                    break
        self._cursor_cache[name] = found if found[0] is not None else False
        return found

    def cursor(self, name):
        """A QCursor from the skin's OWN cursor file for `name`, or None."""
        return self.own_cursor(name)[0]

    def best_cursor(self, name, idle="normal"):
        """The pointer for `name` on this skin - see pick_cursor."""
        return self.pick_cursor(name, idle)[0]

    def pick_cursor(self, name, idle="normal"):
        """(cursor, which file) for part `name` of a window (task 53).

        Only the skin's own files - nothing of Winamp's ships in the app.
        In this order:
        1. the skin's own file for that part (e.g. "EQSLID.CUR");
        2. the window's plain-part file - NORMAL.CUR / EQNORMAL.CUR /
           PNORMAL.CUR for the player / EQ / playlist ("…, plain part"),
           and for the EQ and playlist then NORMAL.CUR ("…, skin normal");
        3. the skin's own MAINMENU.CUR, its default pointer
           ("MAINMENU.CUR, skin default");
        4. the desktop's arrow (None, "desktop arrow").
        """
        idle = idle if idle in self.IDLE_CURSORS else "normal"
        if name in self.IDLE_CURSORS:
            idle = name
        chain = []
        for part in (name, idle, "normal", self.DEFAULT_CURSOR):
            if part and part not in chain:
                chain.append(part)
        for index, part in enumerate(chain):
            cursor, shown = self.own_cursor(part)
            if cursor is None:
                continue
            if index == 0:
                return cursor, shown
            if part == self.DEFAULT_CURSOR:
                return cursor, f"{shown}, skin default"
            if part == idle:
                return cursor, f"{shown}, plain part"
            return cursor, f"{shown}, skin normal"
        return None, "desktop arrow"

    def has(self, key):
        return key in self._images

    def color(self, key, fallback):
        value = self.playlist_colors.get(key)
        if value:
            color = QColor(value)
            if color.isValid():
                return color
        return QColor(fallback)


def first_ani_frame(data):
    """The first frame of an animated .ani cursor, as .cur bytes.

    An .ani file is RIFF('ACON') holding a LIST('fram') of 'icon'
    chunks, each one a complete .cur (or .ico). Anything that does not
    parse gives None, and the caller falls back as for a missing file.
    """
    import struct
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"ACON":
        return None
    end = min(len(data), 8 + struct.unpack("<I", data[4:8])[0])

    def walk(start, stop):
        pos = start
        while pos + 8 <= stop:
            tag = data[pos:pos + 4]
            size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
            body = pos + 8
            if tag == b"icon":
                return data[body:body + size]
            if tag == b"LIST":
                found = walk(body + 4, min(stop, body + size))
                if found:
                    return found
            pos = body + size + (size & 1)
        return None

    return walk(12, end)


def load_skin(path):
    """Load a skin, or raise SkinError with something readable."""
    try:
        return WinampSkin(path)
    except SkinError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SkinError(str(exc)) from exc


def cursor_from_data(data):
    """A QCursor from .cur bytes, with the file's own hotspot; None if unreadable."""
    from PyQt5.QtGui import QCursor, QPixmap
    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "CUR") and not pixmap.loadFromData(data):
        return None
    # The hotspot from the .cur header - ICONDIRENTRY bytes 10-13.
    #
    # QCursor(pixmap) alone puts the hotspot in the CENTRE, so a
    # 32x32 arrow appeared sixteen pixels up and left of where
    # clicks actually landed. These skins' cursors all use (0,0):
    # the arrow tip is the top-left corner, as a pointer should be.
    hotspot_x = hotspot_y = 0
    if len(data) >= 14 and data[2:4] == b"\x02\x00":
        import struct
        hotspot_x, hotspot_y = struct.unpack("<HH", data[10:14])
    return QCursor(pixmap, hotspot_x, hotspot_y)


# What the log was last told per widget, for _log_pointer (task 51).
_pointer_said = {}
_pointer_budget = [0.0, 0, 0]       # window start, lines in it, held back


OWN_SKIN = "frequencies-base.wsz"      # the Frequencies skin, shipped


def bundled_skin_folders():
    """Where the app's own skins are: a source checkout and an AppImage
    lay the tree out differently."""
    here = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(here, "skins"),
            os.path.join(here, "..", "skins"),
            os.path.join(here, "..", "share", "frequencies", "skins")]


def copy_own_skin(folder):
    """Put the Frequencies skin in the user's skins folder if it isn't
    there yet, so it is always one of their skins (build 109). Never
    overwrites a file of that name."""
    if not folder or not os.path.isdir(folder):
        return False
    target = os.path.join(folder, OWN_SKIN)
    if os.path.exists(target):
        return False
    for place in bundled_skin_folders():
        source = os.path.join(place, OWN_SKIN)
        if os.path.isfile(source):
            try:
                import shutil
                shutil.copyfile(source, target)
            except OSError as exc:
                _write_log(f"[skin] couldn't copy the Frequencies skin to "
                           f"{folder}: {exc}")
                return False
            _write_log(f"[skin] copied the Frequencies skin to {target}")
            return True
    _write_log("[skin] the Frequencies skin isn't in this build")
    return False


def _write_log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:                           # noqa: BLE001
        pass


def _log_pointer(widget, name, source, cursor):
    """`[skin] pointer → <name> (<source>) on <panel>` when it changes.

    Task 51: on the user's desktop the pointer looked wrong while every
    headless test passed. This says what the app itself set, so the next
    log tells "the app set the desktop arrow" apart from "the app set the
    skin's pointer and the desktop did not show it". At most 30 lines in
    any 10 s; the rest are counted and the count is logged.
    """
    import time
    try:
        key = id(widget)
        said = (name, source)
        if _pointer_said.get(key) == said:
            return
        _pointer_said[key] = said
        if len(_pointer_said) > 64:
            _pointer_said.clear()
            _pointer_said[key] = said
        now = time.monotonic()
        start, lines, held = _pointer_budget
        if now - start > 10.0:
            if held:
                _write(f"[skin] pointer: {held} more changes not logged")
            _pointer_budget[:] = [now, 0, 0]
        if _pointer_budget[1] >= 30:
            _pointer_budget[2] += 1
            return
        _pointer_budget[1] += 1
        size = ""
        if cursor is not None:
            pm = cursor.pixmap()
            if not pm.isNull():
                size = f", {pm.width()}x{pm.height()}"
        panel = type(widget).__name__
        _write(f"[skin] pointer → {name} ({source}{size}) on {panel}")
    except Exception:                       # noqa: BLE001
        pass


def _write(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:                       # noqa: BLE001
        pass


def set_skin_cursor(widget, skin, name, idle="normal"):
    """Put the skin's cursor for `name` on a skinned widget (task 32).

    Shared by the player, the other panels and the dock, so they all
    fall back the same way: the skin's pointer for that part, else the
    skin's own normal / MAINMENU.CUR, else the desktop's arrow
    (`WinampSkin.pick_cursor`, task 53). Returns True if a pointer was
    set (False: the desktop's arrow).
    """
    if skin is not None:
        cursor, source = skin.pick_cursor(name, idle)
    else:
        cursor, source = None, "no skin"
    if cursor is not None:
        widget.setCursor(cursor)
    else:
        widget.unsetCursor()
    _log_pointer(widget, name, source, cursor)
    sync_window_cursor(widget)
    return cursor is not None


def _same_cursor(a, b):
    from PyQt5.QtCore import Qt
    if a.shape() != b.shape():
        return False
    if a.shape() != Qt.BitmapCursor:
        return True
    return (a.pixmap().cacheKey() == b.pixmap().cacheKey()
            and a.hotSpot() == b.hotSpot())


def sync_window_cursor(widget):
    """Make sure the window really shows the pointer under the mouse (task 43).

    The panels are children of one dock window, and Qt hands a child's
    cursor to that window only when it has seen the mouse enter the
    child. A panel built under a pointer that is already there (opening
    the stack, a skin change) never gets that enter, so its setCursor
    changed nothing on screen: the window kept the desktop's arrow
    until the mouse went into another panel and came back. Reproduced
    under a Wayland compositor: the first panel hovered after opening
    showed the arrow over its title bar however the mouse moved.

    So when the pointer is over this widget, the window is told
    directly. Cheap enough to call on every mouse move.
    """
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QCursor
    try:
        if not widget.isVisible():
            return
        window = widget.window()
        handle = window.windowHandle()
        if handle is None:
            return
        target = window.childAt(window.mapFromGlobal(QCursor.pos()))
        if target is None:
            # The pointer is over the window itself, or not over it.
            if window is not widget or not window.rect().contains(
                    window.mapFromGlobal(QCursor.pos())):
                return
            target = window
        if target is not widget and not widget.isAncestorOf(target):
            return
        # The pointer Qt would pick: the nearest one that set its own.
        owner = target
        while (owner is not window
               and not owner.testAttribute(Qt.WA_SetCursor)):
            owner = owner.parentWidget()
        cursor = owner.cursor()
        if not _same_cursor(handle.cursor(), cursor):
            handle.setCursor(cursor)
    except RuntimeError:
        pass                    # the widget went while we looked
