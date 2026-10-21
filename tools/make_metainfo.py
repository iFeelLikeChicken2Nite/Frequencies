#!/usr/bin/env python3
"""Write the desktop file and the AppStream file into the AppDir (build 136,
task 195).

Run by build-appimage-fedora.sh:

    python3 tools/make_metainfo.py AppDir

appimagetool looks for the AppStream file under the desktop file's name and
appstreamcli wants both named after the component id, so inside the AppDir
all three carry the id (a reverse-DNS name, read from the template):

    AppDir/<id>.desktop and AppDir/usr/share/applications/<id>.desktop
        (copies of frequencies.desktop)
    AppDir/usr/share/metainfo/<id>.appdata.xml
        (frequencies.metainfo.xml, with <!--RELEASES--> replaced by this
        build's release: its number from src/version.py, today's date and
        its lines from src/whats_new.json)
"""

import shutil

import datetime
import json
import os
import re
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    appdir = os.path.abspath(sys.argv[1])
    with open(os.path.join(HERE, "src", "version.py"), encoding="utf-8") as f:
        build = re.search(r"^BUILD\s*=\s*(\d+)", f.read(), re.M).group(1)
    try:
        with open(os.path.join(HERE, "src", "whats_new.json"),
                  encoding="utf-8") as f:
            notes = json.load(f).get(build, [])
    except (OSError, ValueError):
        notes = []
    release = [f'  <releases>',
               f'    <release version="{build}" '
               f'date="{datetime.date.today().isoformat()}">']
    if notes:
        release.append("      <description>")
        release.append("        <ul>")
        for line in notes:
            release.append(f"          <li>{escape(line)}</li>")
        release.append("        </ul>")
        release.append("      </description>")
    release += ["    </release>", "  </releases>"]
    with open(os.path.join(HERE, "frequencies.metainfo.xml"),
              encoding="utf-8") as f:
        text = f.read().replace("  <!--RELEASES-->", "\n".join(release))
    app_id = re.search(r"<id>([^<]+)</id>", text).group(1)
    target = os.path.join(appdir, "usr", "share", "metainfo")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, app_id + ".appdata.xml"), "w",
              encoding="utf-8") as f:
        f.write(text)
    desktop = os.path.join(HERE, "frequencies.desktop")
    for folder in (appdir, os.path.join(appdir, "usr", "share",
                                        "applications")):
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(desktop, os.path.join(folder, app_id + ".desktop"))
    print(f"    AppStream: {app_id} (build {build})")


if __name__ == "__main__":
    main()
