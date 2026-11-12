#!/usr/bin/env python3
"""Golden numbers for BandLogos.onPhoto / inColumn (comfort spec 6), from the
desktop's own bandlogo.header_box + place() and LogoName._rect.

    QT_QPA_PLATFORM=offscreen python3 tools/make_logo_fixtures.py <desktop src> > core/src/test/resources/logo/placement.json
"""
import json
import sys

sys.path.insert(0, sys.argv[1])
from PyQt5.QtCore import QRectF, QSizeF  # noqa: E402
import bandlogo  # noqa: E402

photos = [(290, 290), (360, 225), (118, 118), (52, 52), (600, 400)]
images = [(400, 100), (120, 120), (90, 300), (1000, 80)]
placements = [(1.0, 0.0, 0.0), (1.6, 0.1, -0.2), (0.5, -0.3, 0.4), (2.0, 0.9, -1.0)]
out = {"photo": [], "column": []}
for w, h in photos:
    for iw, ih in images:
        for scale, x, y in placements:
            bounds = QRectF(0, 0, w, h).adjusted(4, 4, -4, -4)
            r = bandlogo.place(bandlogo.header_box(w, h), QSizeF(iw, ih), scale, x, y, bounds)
            out["photo"].append({"w": w, "h": h, "iw": iw, "ih": ih, "scale": scale, "x": x, "y": y,
                                 "box": [r.left(), r.top(), r.width(), r.height()]})
for width in (150, 220, 240, 320):
    for iw, ih in images:
        for scale, x, _ in placements:
            c = bandlogo.COMFORT_BOX
            r = bandlogo.place(QRectF(0, 0, width, c), QSizeF(iw, ih), scale, x, 0, QRectF(0, -c, width, 2 * c))
            out["column"].append({"width": width, "iw": iw, "ih": ih, "scale": scale, "x": x,
                                  "box": [r.left(), 0.0, r.width(), r.height()]})
json.dump(out, sys.stdout, indent=1)
print()
