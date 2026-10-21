#!/usr/bin/env python3
"""Put the licenses of everything the AppImage carries inside it (build 136,
task 195).

Run by build-appimage-fedora.sh once the AppDir is complete, before
appimagetool:

    python3 tools/gather_licenses.py AppDir [--site-packages DIR]
        [--projectm-src DIR] [--projectm-version X.Y.Z]

The AppImage distribution guide asks for the license texts and a way to get
the source of what is bundled. This writes, under
AppDir/usr/share/doc/frequencies/:

    bundled-parts.txt      every bundled part: name, version, license, home
                           page, where its source is, and the files it came
                           from (system libraries: asked of rpm on the build
                           box; Python packages: their own metadata)
    licenses/system/<rpm>/ the license files of each system package that
                           supplied a library (rpm -qL)
    licenses/python/<pkg>/ the license files of each Python package
    licenses/projectm/     projectM's own license files

LICENSE, THIRD-PARTY.md and SOURCES.txt are copied in by the build script
(they are written by hand, in the repository).

Stand-alone and forgiving on purpose: a library rpm does not know is listed
as "not found in the package database", never stops the build.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

LICENSE_NAMES = re.compile(
    r"^(LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|AUTHORS|UNLICENSE)", re.I)
# Fedora's source packages live here; the version-release is in the list.
FEDORA_SRC = "https://src.fedoraproject.org/rpms/"
DOC_DIR = os.path.join("usr", "share", "doc", "frequencies")


def run(cmd):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return done.returncode, done.stdout


def is_elf(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._+-]", "_", text)


# --- where did this library come from? --------------------------------

def library_index():
    """basename -> real path on this machine, from the loader's cache and,
    for names it does not list (Samba's private libraries, PipeWire
    modules), a walk of the library folders."""
    index = {}
    _, out = run(["ldconfig", "-p"])
    for line in out.splitlines():
        match = re.match(r"\s*(\S+) \(([^)]*)\) => (\S+)", line)
        if not match:
            continue
        name, kind, path = match.groups()
        if "x86-64" in kind or "AArch64" in kind or "64" in kind \
                or name not in index:
            index[name] = path
    for base in ("/usr/lib64", "/usr/lib", "/lib64"):
        for root, _dirs, files in os.walk(base):
            if "site-packages" in root or "/python3" in root:
                continue
            for name in files:
                if ".so" in name and name not in index:
                    index[name] = os.path.join(root, name)
    return index


def owner(path):
    """(name, version-release, license, url, source rpm) or None."""
    real = os.path.realpath(path)
    code, out = run(["rpm", "-qf", "--qf",
                     "%{NAME}\t%{VERSION}-%{RELEASE}\t%{LICENSE}\t%{URL}\t"
                     "%{SOURCERPM}\n", real])
    if code != 0 or "\t" not in out:
        return None
    first = out.strip().splitlines()[0].split("\t")
    if len(first) < 5:
        return None
    return tuple(first[:5])


def license_files(package):
    code, out = run(["rpm", "-qL", package])
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines()
            if line.strip().startswith("/") and os.path.isfile(line.strip())]


def source_rpm_name(srpm):
    """'zlib-ng-2.2.4-3.fc44.src.rpm' -> 'zlib-ng'."""
    return re.sub(r"-[^-]+-[^-]+\.src\.rpm$", "", srpm or "")


def collect_system(appdir, index):
    """Walk the AppDir's binaries and libraries; group them by rpm."""
    found = {}      # package -> info dict
    unknown = []
    seen = set()
    roots = [os.path.join(appdir, "usr", "lib"),
             os.path.join(appdir, "usr", "bin")]
    for root in roots:
        for here, _dirs, files in os.walk(root):
            for name in sorted(files):
                full = os.path.join(here, name)
                if name in seen or not is_elf(full):
                    continue
                if os.path.islink(full) and not os.path.exists(full):
                    continue
                seen.add(name)
                if name.startswith("libprojectM"):
                    continue    # built from source, handled separately
                source = index.get(name)
                if source is None and root.endswith("bin"):
                    source = shutil.which(name)
                info = owner(source) if source else None
                if info is None:
                    unknown.append(name)
                    continue
                pkg = info[0]
                entry = found.setdefault(pkg, {
                    "version": info[1], "license": info[2], "url": info[3],
                    "srpm": source_rpm_name(info[4]), "files": []})
                entry["files"].append(name)
    # The Python standard library travels as plain files, not libraries.
    info = owner(os.__file__)
    if info and info[0] not in found:
        found[info[0]] = {"version": info[1], "license": info[2],
                          "url": info[3], "srpm": source_rpm_name(info[4]),
                          "files": ["(Python standard library)"]}
    return found, unknown


# --- Python packages ---------------------------------------------------

def read_metadata(dist_info):
    meta = {}
    path = os.path.join(dist_info, "METADATA")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    break
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key in ("Name", "Version", "License", "License-Expression",
                           "Home-page"):
                    meta.setdefault(key, value)
                elif key == "Project-URL":
                    label, _, link = value.partition(",")
                    if label.strip().lower() in (
                            "homepage", "home", "source", "repository",
                            "source code", "github", "code"):
                        meta.setdefault("Project-URL", link.strip())
                elif key == "Classifier" and value.startswith("License ::"):
                    meta.setdefault("Classifier", value.split("::")[-1].strip())
    except OSError:
        pass
    return meta


def collect_python(site_packages):
    packages = []
    for dist_info in sorted(glob.glob(os.path.join(site_packages,
                                                   "*.dist-info"))):
        meta = read_metadata(dist_info)
        if "Name" not in meta:
            continue
        licenses = (meta.get("License-Expression") or meta.get("Classifier")
                    or meta.get("License") or "see the license files")
        if len(licenses) > 80 or "\n" in licenses:
            licenses = meta.get("Classifier") or "see the license files"
        files = []
        for root, _dirs, names in os.walk(dist_info):
            for name in names:
                if LICENSE_NAMES.match(name):
                    files.append(os.path.join(root, name))
        packages.append({
            "name": meta["Name"], "version": meta.get("Version", "?"),
            "license": licenses,
            "url": meta.get("Home-page") or meta.get("Project-URL") or "",
            "files": files, "dist_info": dist_info})
    return packages


# --- writing -----------------------------------------------------------

def copy_all(files, destination, base=None):
    """Copy license files into destination, keeping their folder below
    `base` (pip, for one, carries the licenses of the code it vendors)."""
    for path in files:
        name = os.path.relpath(path, base) if base else os.path.basename(path)
        target = os.path.join(destination, name)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("appdir")
    parser.add_argument("--site-packages")
    parser.add_argument("--projectm-src")
    parser.add_argument("--projectm-version", default="")
    args = parser.parse_args()

    appdir = os.path.abspath(args.appdir)
    out_dir = os.path.join(appdir, DOC_DIR)
    os.makedirs(out_dir, exist_ok=True)
    lic_root = os.path.join(out_dir, "licenses")

    lines = [
        "Everything Frequencies carries inside this AppImage.",
        "",
        "The app's own code is GPL-3.0-or-later (see LICENSE). THIRD-PARTY.md",
        "explains the main parts in plain words; SOURCES.txt says where to get",
        "the matching source. This list is made by the build from the build",
        "machine's own package database and the Python packages' metadata.",
        "",
        "== System libraries and programs (Fedora packages) ==",
        "",
    ]

    have_rpm = shutil.which("rpm") is not None
    found, unknown = ({}, [])
    if have_rpm:
        found, unknown = collect_system(appdir, library_index())
    else:
        lines.append("(rpm was not available on the build machine, so the "
                     "system libraries are not listed.)\n")
    for pkg in sorted(found):
        info = found[pkg]
        lines.append(f"{pkg} {info['version']}")
        lines.append(f"    license: {info['license']}")
        if info["url"] and info["url"] != "(none)":
            lines.append(f"    home:    {info['url']}")
        if info["srpm"]:
            lines.append(f"    source:  {FEDORA_SRC}{info['srpm']}"
                         f"  (version {info['version']})")
        lines.append("    files:   " + ", ".join(sorted(set(info["files"]))))
        texts = license_files(pkg)
        if texts:
            copy_all(texts, os.path.join(lic_root, "system", safe_name(pkg)))
            lines.append("    license text: licenses/system/" + safe_name(pkg))
        else:
            lines.append("    license text: the package ships none; the "
                         "license above applies")
        lines.append("")
    if unknown:
        lines.append("Not found in the package database on the build "
                     "machine (see THIRD-PARTY.md):")
        lines.append("    " + ", ".join(sorted(unknown)))
        lines.append("")

    lines += ["== Built from source ==", ""]
    if args.projectm_src and os.path.isdir(args.projectm_src):
        files = []
        for pattern in ("*", "vendor/*/*"):
            for path in glob.glob(os.path.join(args.projectm_src, pattern)):
                if os.path.isfile(path) and LICENSE_NAMES.match(
                        os.path.basename(path)):
                    files.append(path)
        copy_all(files, os.path.join(lic_root, "projectm"),
                 base=args.projectm_src)
        lines += [f"projectM {args.projectm_version}".rstrip(),
                  "    license: LGPL-2.1-or-later",
                  "    home:    https://github.com/projectM-visualizer/projectm",
                  "    source:  https://github.com/projectM-visualizer/projectm"
                  + (f"  (tag v{args.projectm_version})"
                     if args.projectm_version else ""),
                  "    files:   libprojectM-4.so*",
                  "    license text: licenses/projectm", ""]
    else:
        lines += ["projectM was not built on this machine; nothing to list.",
                  ""]

    lines += ["== Python packages ==", ""]
    count_py = 0
    if args.site_packages and os.path.isdir(args.site_packages):
        for item in collect_python(args.site_packages):
            count_py += 1
            lines.append(f"{item['name']} {item['version']}")
            lines.append(f"    license: {item['license']}")
            if item["url"]:
                lines.append(f"    home:    {item['url']}")
            lines.append("    source:  https://pypi.org/project/"
                         f"{item['name']}/{item['version']}/")
            if item["files"]:
                copy_all(item["files"], os.path.join(
                    lic_root, "python", safe_name(item["name"])),
                    base=item["dist_info"])
                lines.append("    license text: licenses/python/"
                             + safe_name(item["name"]))
            lines.append("")
    else:
        lines += ["(the Python packages' folder was not given)", ""]

    with open(os.path.join(out_dir, "bundled-parts.txt"), "w",
              encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"    licenses: {len(found)} system packages "
          f"({len(unknown)} libraries unknown), {count_py} Python packages "
          f"-> {os.path.join(DOC_DIR, 'bundled-parts.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
