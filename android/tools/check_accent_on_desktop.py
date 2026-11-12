"""Phone -> desktop -> phone for the accent choice.

    python3 tools/check_accent_on_desktop.py <desktop source folder>/src

Reads core/build/accent-<mode>.json (written by AccentSettingsTest) with the
desktop's own storage.read_backup, checks the desktop's Accent color list
can show the accent_source the phone wrote, and writes the desktop's own
backups of them to core/build/desktop-accent-<mode>.json, plus one where the
desktop changed it to "A color I choose" (#c0ffee). Then run
`./gradlew :core:test` again: AccentSettingsTest.desktopBackupsComeBack
reads them.
"""
import os
import sys
import tempfile

src = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, src)
import storage  # noqa: E402

desktop_choices = ("artwork", "desktop", "fixed")   # optionspage.ACCENT_CHOICES
build = os.path.join(here, "..", "core", "build")
problems = 0
for mode in ("artwork", "material_you", "theme", "fixed"):
    stations, settings, health, history, artists, talk = storage.read_backup(os.path.join(build, f"accent-{mode}.json"))
    source = settings.get("accent_source")
    if source not in desktop_choices:
        problems += 1
        print(f"{mode}: the desktop can't show accent_source {source!r}")
    print(f"phone {mode}: desktop shows {source}, accent_fixed {settings.get('accent_fixed')}, "
          f"phone keys kept: {settings.get('android_accent')}/{settings.get('android_accent_shared')}")
    storage.export_backup(os.path.join(build, f"desktop-accent-{mode}.json"), stations, settings, health,
                          history, artists, talk)
    if mode == "theme":
        settings["accent_source"] = "fixed"      # it was already: only the color changes
        settings["accent_fixed"] = "#c0ffee"
        storage.export_backup(os.path.join(build, "desktop-accent-changed.json"), stations, settings, health,
                              history, artists, talk)
print(f"{problems} problems")
sys.exit(1 if problems else 0)
