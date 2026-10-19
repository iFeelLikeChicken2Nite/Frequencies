# PyInstaller spec for the Windows build (WP1, WP2).
#
# Run by build-windows.ps1 (double-click build-windows.bat), not by hand.
# Output: dist\Frequencies\Frequencies.exe plus its _internal folder.
#
# The data files go where the code looks for them: the code sits flat in
# _internal (like usr/share/frequencies in the AppImage), so icons/,
# skins/, sounds/, static/ and companion/ sit beside it there too.

import glob
import os

ROOT = os.path.abspath(SPECPATH)
SRC = os.path.join(ROOT, "src")


def folder(source, dest):
    return [(path, dest) for path in glob.glob(os.path.join(source, "*"))
            if os.path.isfile(path)]


datas = (
    [(path, ".") for path in glob.glob(os.path.join(SRC, "*.json"))]
    + [(os.path.join(SRC, "freqamp.png"), "."),
       (os.path.join(ROOT, "frequencies.png"), ".")]
    + folder(os.path.join(SRC, "icons"), "icons")
    + folder(os.path.join(ROOT, "icons"), "icons")
    + folder(os.path.join(SRC, "skins"), "skins")
    + folder(os.path.join(SRC, "sounds"), "sounds")
    + folder(os.path.join(SRC, "companion"), "companion")
    + [(path, "static") for path in glob.glob(os.path.join(ROOT, "static", "*.ogg"))]
    # projectM's presets (WP2), fetched by tools/fetch_projectm_windows.py.
    + folder(os.path.join(ROOT, "build-windows", "presets"), "presets")
)
# The dev marker: devmode.py reads it (task 49). Written by
# build-windows.ps1 -Dev, never in a user build.
if os.path.exists(os.path.join(ROOT, "build-windows", "dev-build")):
    datas.append((os.path.join(ROOT, "build-windows", "dev-build"), "."))

binaries = [(path, ".") for path in glob.glob(
    os.path.join(ROOT, "build-windows", "libmpv-2.dll"))]
# projectM and the DLLs it needs (WP2), beside the code: projectm.py
# loads it by full path.
binaries += [(path, ".") for path in glob.glob(
    os.path.join(ROOT, "build-windows", "projectm", "*.dll"))]

# Every app module, so ones imported inside functions are never missed.
hidden = [os.path.splitext(os.path.basename(path))[0]
          for path in glob.glob(os.path.join(SRC, "*.py"))
          if not path.endswith(("main.py", "main_legacy.py"))]

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["shazamio_core", "zxingcpp", "segno",
                            "yt_dlp", "PyQt5.QtWebEngineWidgets",
                            "pyaudiowpatch",
                            # Windows media controls (WP3, smtc.py).
                            "winrt.windows.foundation",
                            "winrt.windows.media",
                            "winrt.windows.media.playback",
                            "winrt.windows.storage.streams"],
    excludes=["main_legacy", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    # Files are read as UTF-8 everywhere, as on Linux.
    [("X utf8_mode=1", None, "OPTION")],
    exclude_binaries=True,
    name="Frequencies",
    icon=os.path.join(ROOT, "frequencies.ico"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Frequencies")
