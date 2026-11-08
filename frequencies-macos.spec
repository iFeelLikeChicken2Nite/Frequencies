import glob
import os
import runpy

ROOT = os.path.abspath(SPECPATH)
SRC = os.environ.get("FREQUENCIES_SOURCE_DIR") or os.path.join(ROOT, "src")
BUILD = runpy.run_path(os.path.join(SRC, "version.py"))["BUILD"]
SECRET_FILES = {"github_token.json", "release_token.json",
                "report_token.json", "twitch_chat.json"}


def folder(source, dest):
    return [(path, dest) for path in glob.glob(os.path.join(source, "*"))
            if os.path.isfile(path)]


datas = (
    [(path, ".") for path in glob.glob(os.path.join(SRC, "*.json"))
     if os.path.basename(path) not in SECRET_FILES]
    + [(os.path.join(SRC, "freqamp.png"), "."),
       (os.path.join(ROOT, "frequencies.png"), ".")]
    + folder(os.path.join(SRC, "icons"), "icons")
    + folder(os.path.join(ROOT, "icons"), "icons")
    + folder(os.path.join(SRC, "skins"), "skins")
    + folder(os.path.join(SRC, "sounds"), "sounds")
    + folder(os.path.join(SRC, "companion"), "companion")
    + folder(os.path.join(SRC, "talk_photos"), "talk_photos")
    + folder(os.path.join(ROOT, "static"), "static")
    + [(os.path.join(ROOT, "build-macos", "dev-build"), ".")]
    + [(os.path.join(ROOT, "LICENSE"), "."),
       (os.path.join(ROOT, "THIRD-PARTY.md"), ".")]
)
hidden = [os.path.splitext(os.path.basename(path))[0]
          for path in glob.glob(os.path.join(SRC, "*.py"))
          if not path.endswith(("main.py", "main_legacy.py"))]

libdir = os.environ.get("MACOS_LIBMPV_DIR")
binaries = ([(path, ".") for path in glob.glob(os.path.join(libdir, "*.dylib"))]
            if libdir else [(os.environ["MACOS_LIBMPV"], ".")])

analysis = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["shazamio_core", "zxingcpp", "segno", "yt_dlp",
                            "PyQt5.QtWebEngineWidgets"],
    excludes=["main_legacy", "tkinter"],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [("X utf8_mode=1", None, "OPTION")],
          exclude_binaries=True, name="Frequencies", console=False)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="Frequencies")
app = BUNDLE(coll, name="Frequencies.app", bundle_identifier="org.frequencies.app",
             info_plist={"NSHighResolutionCapable": True,
                         "LSMinimumSystemVersion": "11.0",
                         "CFBundleVersion": str(BUILD)})