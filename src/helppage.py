"""Frequencies — the Help page.

A scrolling column of cards, a question and an answer each, in the
order and the words the developer wrote (build 101, task 115). The user
writes this page: do not edit HELP without being asked.
"""

import html

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QLabel, QScrollArea, QVBoxLayout, QWidget)

from theme import T

HELP = [
    ('How do I find new frequencies?',
     'That\'s easy! If you know the name of the radio station, or if you\'ve ever seen or heard it on radio.garden, you can simply click on "Search" under Add Frequencies, and type its name into the bar at the top. You can also use this search feature to check out streams you\'ve never heard before! Try typing in "metal" or "jazz."\n\nThere is also a featured selection of curated radio stations on the Add Frequencies screen, which should update regularly.\n\nIf you really like a radio station, but their website doesn\'t have a link you can copy to paste into the "URL" button on the Add Frequencies screen, you can try to use the Stream Sniffer button to go to their website and steal the audio URL.'),
    ('How can I share frequencies?',
     "Sharing is caring! To share a frequency, right click and choose "
     "share. You can scan the QR code with the mobile app, or save it as a "
     "photo to share it on desktop. To send several at once, ctrl + click a "
     "few stations, then right-click and choose Share these frequencies. "
     "Tip: using the QR code to share them will carry over the station's "
     "enhanced rich data, as well as its tags!"),
    ('How does YouTube work?',
     'By clicking on the YouTube button on the Add Frequencies screen, you can use a YouTube playlist to simulate a radio station. Make sure the playlist is public!'),
    ('What is iBroadcast?',
     "It's like Spotify, but instead of listening to files that they have stored on their servers, you listen to files you've uploaded yourself. It's just like Google Play Music, if you ever used that back in the day. Accounts are free, and they provide an excellent service!"),
    ('I want a normal EQ, not a three band EQ',
     'You can toggle a 10 band EQ by going to Settings > Sound > Ten-band equalizer'),
    ("A station says it's down?",
     "Occasionally, some radio streams may go down. Sometimes it is for good, but other times it's just maintenance. Regardless, the app will mark a station as 'down' if it isn't successfully reached on the first attempt. The app will periodically check the stream in the background to see if it is back up."),
    ('How do I submit a bug report?',
     'BUGREPORT'),
    ('Tags',
     "Unlike most radio apps, Frequencies utilizes a tag based system for organizing your favorite radio stations. Tags can be very powerful: you can sort talk radio, high energy, chill sleepy music, etc so shuffling won't lead to you listening to the wrong music at the wrong time. Left click a tag to include it in the filter, and right click it to remove it from the filter. Easy!"),
    ('Winamp',
     "It's cool and all but you've probably noticed if you're on Linux that dragging a window out of the dock is a little wonky, sorry about that! this is mostly a wayland thing; right click on the titlebar of a window you have undocked to reattach it"),
    ('Ad covering',
     "Some radio streams will forcibly inject ads into the very beginning of your audio. This can be irritating, and unfortunately, there's nothing you can do about it...unless you want to cover the ad with your own music, and make it seem like you're catching the best parts of your favorite songs in between radio towers. On stations that do this, you can override the first few seconds of forced ads with whatever you'd like. This is preferable to listening to the same Geico ad for the 80th time in the past two hours."),
    ("Why doesn't the Shazam button stay on?",
     "The Shazam feature is toggled on a per frequency basis, so talk radio stations or stations that play things that aren't gonna be identified don't needlessly shazam when you don't want them to."),
]


class HelpPage(QScrollArea):
    """Cards down a column, scrolling with everything else."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 24, 28, 40)
        lay.setSpacing(14)

        head = QLabel("Help")
        head.setProperty("role", "display")
        lay.addWidget(head)

        for title, text in HELP:
            lay.addWidget(self._card(title, text))
        lay.addStretch(1)
        self.setWidget(body)

    @staticmethod
    def _card(title, text):
        card = QWidget()
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(
            f"background: {T['bg1']}; border-radius: 10px;")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(6)

        heading = QLabel(title)
        heading.setProperty("role", "title")
        lay.addWidget(heading)

        if text == "BUGREPORT":
            body = HelpPage._bug_report()
        else:
            body = QLabel(text)
        body.setProperty("role", "body")
        body.setWordWrap(True)
        lay.addWidget(body)
        return card

    @staticmethod
    def _bug_report():
        """The reports repository's issues page, when the build knows it."""
        try:
            import photopack
            repo = photopack.report_address()
        except Exception:
            repo = ""
        if not repo:
            return QLabel("Open an issue on the Frequencies GitHub issues "
                          "page.")
        url = html.escape(repo + "/issues", quote=True)
        label = QLabel(f'Open an issue on the GitHub issues tracker: '
                       f'<a href="{url}">{url}</a>')
        label.setTextFormat(Qt.RichText)
        label.setOpenExternalLinks(True)
        return label
