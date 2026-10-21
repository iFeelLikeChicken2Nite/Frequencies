# Frequencies: what is inside, and under which license

Frequencies itself is free software under the **GNU General Public License,
version 3 or (at your option) any later version**. The full text is in
`LICENSE`. It is not a legal opinion; it is what the parts below ask for.

The reason is PyQt5, which is free only under the GPL v3, so an app built on it
and shared with other people is GPL v3 too. In plain words: you may use, copy,
change and share Frequencies, and whoever shares it (or a changed version) has
to share the source under the same license. `SOURCES.txt` says where the source
is.

The AppImage carries about 350 libraries from the Fedora machine that built
it. They are listed one by one, with their licenses and source links, in
`bundled-parts.txt` (inside the AppImage under `usr/share/doc/frequencies/`
next to this file), and each package's own license files are in `licenses/`
there. This file explains the main parts.

## The main parts

| Part | What it does | License |
|---|---|---|
| PyQt5, PyQtWebEngine | Every window and button; the built-in browser | GPL v3 |
| Qt 5 (inside the PyQt5-Qt5 packages) | The toolkit under PyQt5 | LGPL v3 (QtWebEngine: LGPL v3 with Chromium's own licenses) |
| libmpv and FFmpeg libraries | Play and decode the streams | GPL v2 or later / LGPL, as Fedora builds them |
| python-mpv | Lets Python talk to libmpv | GPL v2+ or LGPL v2.1+ |
| projectM 4.x | The visualizer | LGPL v2.1 or later |
| projectM "cream of the crop" presets | The visualizer's pictures | Made by many people; shared with projectM |
| SDL3 | Needed by libmpv | zlib license |
| glibc | Travels with the app so it runs on older systems | LGPL v2.1 or later |
| Python 3 | Runs the app | PSF license |
| requests, urllib3, certifi, idna, charset-normalizer | Talk to websites | Apache 2.0, MIT, MPL 2.0, BSD |
| segno | Draws QR codes | BSD 3-clause |
| zxing-cpp | Reads QR codes | Apache 2.0 |
| yt-dlp | YouTube stations | Unlicense (public domain) |
| shazamio-core | Song fingerprints | MIT |
| Everything else in `bundled-parts.txt` | Libraries those need | As listed there |

The LGPL parts (Qt, glibc, projectM and others) can be replaced: run
`./Frequencies-x86_64.AppImage --appimage-extract`, swap the library inside
`squashfs-root/usr/lib` (or the PyQt5 folder), and start `squashfs-root/AppRun`.

## Parts with their own terms

These are not covered by Frequencies' license, whatever a LICENSE file next to
them says. They are here under the terms of whoever made them.

- **Clippy** (`companion/map.png`, `animations.json`): the character and its
  artwork belong to Microsoft Corporation. The code around it is Felix
  Rieseberg's (MIT, `companion/LICENSE-clippy.md`).
- **Radio static sounds** (`static/*.ogg`): recordings from freesound.org; each
  sound has its own license there.
- **Winamp-based skin** (`skins/frequencies-base.wsz`) and **the llama clip**
  (`sounds/winamp-llama.mp3`): from Winamp, which belongs to its owners. The
  other skin, `skins/base.wsz`, is drawn by this project.
- **Artist photos**: shown from a separate photo pack the app downloads. They
  belong to their photographers and are not part of the program.
- **Wikipedia text** (artist bios, CC BY-SA 4.0), **Wikimedia Commons photos**
  (each with its own license), **MusicBrainz** facts (CC0): fetched while the
  app runs, with the credit the app shows beside them.
- **Names and logos** such as Shazam, Twitch, YouTube, Winamp and iBroadcast are
  trademarks of their owners. Frequencies is not made or endorsed by them.
