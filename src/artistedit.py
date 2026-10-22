"""Frequencies — correcting who an artist is.

The biography comes from MusicBrainz and Wikipedia, and for most acts
that is right. For the rest it is wrong in one of three ways: the name
matches somebody else entirely, the right article exists but nothing
links to it, or no article is a good fit and the only fix is to write
the thing yourself.

The override file has always handled all three — an mbid pins the
MusicBrainz artist, a Wikipedia title forces one article, and a written
biography replaces the text outright. What it has not had is a way to
fill in from inside the app, so a correction meant editing JSON by hand.

Overrides written here land in the user's own file. The ones that ship
with the app are separate and are not touched.
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QVBoxLayout)

import storage


class ArtistEditDialog(QDialog):
    """Edit one artist's correction."""

    def __init__(self, artist, parent=None, station=None):
        # Owned by the window, not by the widget that asked for it.
        #
        # It is opened from the artist photo and biography, which sit in
        # a column whose background is "transparent" so the photo can
        # fade into it. A dialog takes its parent's stylesheet with it,
        # so it opened with no background at all — and the lyrics and
        # station list showed through every field (task 16).
        super().__init__(parent.window() if parent is not None else None)
        self.artist = (artist or "").strip()
        # A talk station's own info (task 49, dev builds): same fields,
        # filed in the developer's talk list, which ships.
        self.station = station
        self.setWindowTitle(f"Artist — {self.artist}" if self.artist
                            else "Artist")
        self.setMinimumSize(560, 520)

        if station is not None:
            existing = dict(storage.talk_entry(station)[1] or {})
            photo = existing.get("photo", "")
            if photo and not photo.startswith(("http://", "https://")):
                existing["photo"] = (storage.talk_photo_path(photo)
                                     or photo)
        else:
            existing = storage.artist_override(self.artist) or {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        note = QLabel(
            "What you write here replaces what the app looks up, for "
            "everyone who gets this build. Leave a field blank to keep "
            "the automatic answer for it.")
        if station is not None:
            self.setWindowTitle(f"Station info — {self.artist}")
            note.setText(
                "Talk radio: this station's info stays in the artist "
                "panel the whole time it plays. What you write here ships "
                "to everyone in your next build. Leave the biography blank "
                "to use the Wikipedia article.")
        note.setWordWrap(True)
        note.setProperty("role", "secondary")
        outer.addWidget(note)

        form = QFormLayout()

        self.display = QLineEdit(existing.get("name", ""))
        self.display.setPlaceholderText(self.artist or "As identified")
        form.addRow("Show as:", self.display)

        # The two ways to point the lookup somewhere better, before the
        # last resort of writing it out.
        self.mbid = QLineEdit(existing.get("mbid", ""))
        self.mbid.setPlaceholderText(
            "MusicBrainz id, when the name matches several artists")
        form.addRow("MusicBrainz id:", self.mbid)
        if station is not None:
            # Stations are not on MusicBrainz.
            self.mbid.setVisible(False)
            form.labelForField(self.mbid).setVisible(False)

        # The line above the biography: what the panel calls genres.
        self.genres = QLineEdit(existing.get("genres", ""))
        self.genres.setPlaceholderText(
            "rock · electronic · shoegaze — shown above the biography")
        form.addRow("Genres:", self.genres)
        if station is not None:
            self.genres.setPlaceholderText(
                "news · talk — shown above the station's info")

        self.wikipedia = QLineEdit(existing.get("wikipedia", ""))
        self.wikipedia.setPlaceholderText(
            "Article title, when the right one is not being found")
        form.addRow("Wikipedia title:", self.wikipedia)

        outer.addLayout(form)

        outer.addWidget(QLabel("Biography:"))
        self.bio = QPlainTextEdit(existing.get("bio", ""))
        self.bio.setPlaceholderText(
            "Written out in full — used instead of any article.")
        outer.addWidget(self.bio, 1)

        # A picture, for artists whose photo is wrong or missing.
        photo_row = QHBoxLayout()
        self.photo = QLineEdit(existing.get("photo", ""))
        self.photo.setPlaceholderText("Path to a photograph, or blank")
        photo_row.addWidget(self.photo, 1)

        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_photo)
        photo_row.addWidget(browse)
        outer.addLayout(photo_row)

        # Where the picture came from, and where to go to see it.
        #
        # A photograph somebody else took should say so, and the credit
        # is worth nothing without a way to follow it back.
        credit = QFormLayout()
        self.photo_source = QLineEdit(existing.get("photo_source", ""))
        self.photo_source.setPlaceholderText(
            "Who took it, e.g. Press photo · Jane Doe")
        credit.addRow("Photo credit:", self.photo_source)

        self.photo_url = QLineEdit(existing.get("photo_url", ""))
        self.photo_url.setPlaceholderText("Link the credit points at")
        credit.addRow("Photo link:", self.photo_url)
        outer.addLayout(credit)

        # The band's logo, shown in place of the name (build 141, task
        # 203): chosen, then sized and placed before it is saved.
        import bandlogo
        self.logo_place = bandlogo.placement(existing)
        logo_row = QHBoxLayout()
        self.logo = QLineEdit(existing.get("logo", ""))
        self.logo.setPlaceholderText(
            "A PNG with a see-through background, or blank")
        logo_row.addWidget(self.logo, 1)
        pick = QPushButton("Choose…")
        pick.clicked.connect(self._choose_logo)
        logo_row.addWidget(pick)
        self.adjust = QPushButton("Adjust…")
        self.adjust.clicked.connect(self._adjust_logo)
        logo_row.addWidget(self.adjust)
        self.logo.textChanged.connect(
            lambda text: self.adjust.setEnabled(os.path.isfile(text.strip())))
        self.adjust.setEnabled(os.path.isfile(self.logo.text().strip()))
        logo_form = QFormLayout()
        logo_form.addRow("Logo:", logo_row)
        outer.addLayout(logo_form)
        if station is not None:
            # Stations keep their name as text.
            for widget in (self.logo, pick, self.adjust,
                           logo_form.labelForField(logo_row)):
                widget.setVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save correction")

        # Removing one is as important as adding one: a correction that
        # has been fixed upstream should be able to go away.
        clear = buttons.addButton("Remove", QDialogButtonBox.DestructiveRole)
        clear.clicked.connect(self._clear)

        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Photograph of {self.artist}", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.webp);;All files (*)")
        if path:
            self.photo.setText(path)

    def _choose_logo(self):
        """Pick a logo, then size and place it (the editor); Cancel there
        keeps what was before."""
        start = self.logo.text().strip()
        if not os.path.isfile(start):
            # The band-logos folder that comes with the source zip.
            here = os.path.dirname(os.path.dirname(os.path.abspath(
                __file__)))
            start = next((p for p in (os.path.join(here, "band-logos"),
                                      os.path.expanduser("~"))
                          if os.path.isdir(p)), "")
        path, _ = QFileDialog.getOpenFileName(
            self, f"Logo of {self.artist}", start,
            "Images (*.png *.webp);;All files (*)")
        if path and self._adjust_logo(path, (1.0, 0.0, 0.0)):
            self.logo.setText(path)

    def _adjust_logo(self, path=None, place=None):
        """The logo's size and place, as users will see it."""
        import bandlogo
        path = path or self.logo.text().strip()
        if not os.path.isfile(path):
            return False
        photo = self.photo.text().strip()
        editor = bandlogo.LogoEditor(self.artist, path,
                                     place or self.logo_place,
                                     photo if os.path.isfile(photo) else "",
                                     self)
        if not editor.exec_():
            return False
        self.logo_place = editor.place()
        return True

    def _clear(self):
        if self.station is not None:
            # The info goes; the talk switch stays as it is (Station
            # settings), so the Wikipedia article takes over.
            storage.save_talk_entry(self.station, fields={})
            self._log("info removed")
            self.accept()
            return
        overrides = storage.load_artist_overrides()
        overrides.pop(self.artist.lower(), None)
        storage.save_artist_overrides(overrides)
        self.accept()

    def _save(self):
        written = {
            "name": self.display.text().strip(),
            "genres": self.genres.text().strip(),
            "mbid": self.mbid.text().strip(),
            "wikipedia": self.wikipedia.text().strip(),
            "bio": self.bio.toPlainText().strip(),
            "photo": self.photo.text().strip(),
            "photo_source": self.photo_source.text().strip(),
            "photo_url": self.photo_url.text().strip(),
        }
        written = {k: v for k, v in written.items() if v}
        logo = self.logo.text().strip()
        if logo and self.station is None:
            import bandlogo
            written["logo"] = bandlogo.keep_logo(self.artist, logo)
            for field, value in zip(("logo_scale", "logo_x", "logo_y"),
                                    self.logo_place):
                written[field] = round(value, 3)

        if self.station is not None:
            # The photo is copied beside the talk list, so the build can
            # ship it (tools/ship_talk_stations.py).
            if written.get("photo"):
                written["photo"] = storage.keep_talk_photo(
                    self.station, written["photo"])
            storage.save_talk_entry(self.station, fields=written)
            self._log("info saved (" + (", ".join(sorted(written))
                                        or "nothing set") + ")")
            self.accept()
            return

        overrides = storage.load_artist_overrides()
        if written:
            overrides[self.artist.lower()] = written
        else:
            # Everything blank means "no correction", not "an empty one".
            overrides.pop(self.artist.lower(), None)
        storage.save_artist_overrides(overrides)
        self.accept()

    def _log(self, text):
        try:
            import crashlog
            crashlog.write(f"[talk] '{self.artist}': {text} - dev build, "
                           f"{storage.dev_talk_path()}")
        except Exception:                           # noqa: BLE001
            pass
