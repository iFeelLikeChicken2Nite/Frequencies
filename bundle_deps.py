#!/usr/bin/env python3
"""
Walk the shared-library dependency closure of every ELF binary/.so we've
placed in AppDir, and copy in everything that isn't on the official
AppImage "assume present on host" excludelist.

IMPORTANT: the destination filename must be the SONAME (the exact
string another library's DT_NEEDED entry asks for, e.g.
"libavcodec.so.60") - not the fully-versioned real filename it's
symlinked to on the build system (e.g. "libavcodec.so.60.31.102").
The dynamic linker looks up dependencies by exact filename; if we
bundle a file under the wrong name, dlopen() fails with "No such
file or directory" even though the bytes are technically present
under a different name.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.join(ROOT, "AppDir")
EXCLUDELIST_PATH = os.path.join(ROOT, "excludelist")
LIB_OUT = os.path.join(APPDIR, "usr", "lib")
# Copies of excludelist libraries, used only on a system that lacks them.
FALLBACK_OUT = os.path.join(LIB_OUT, "fallback")

# Never shipped, not even as a fallback: glibc has to be the system's own.
GLIBC = ("ld-linux", "libc.so", "libm.so", "libmvec.so", "libdl.so",
         "libpthread.so", "librt.so", "libresolv.so", "libutil.so",
         "libanl.so", "libnss_", "libthread_db.so", "libBrokenLocale.so",
         "libcidn.so")

# The libraries whose age matters, not just whether they are there: the
# bundle may need a newer one than the system has (built on a newer
# distro). AppRun compares the newest version tag the bundle asks for.
VERSIONED = {"libc.so.6": "GLIBC_", "libstdc++.so.6": "GLIBCXX_",
             "libgcc_s.so.1": "GCC_"}


def pyqt_lib_dirs():
    """PyQt5 ships a complete, self-contained Qt5 under its own tree,
    and its binaries find it through RUNPATH ($ORIGIN/../../lib)."""
    return [
        os.path.realpath(d)
        for d in glob.glob(os.path.join(
            APPDIR, "usr", "lib*", "python3.*", "site-packages",
            "PyQt5", "Qt5", "lib"))
        if os.path.isdir(d)
    ]


def pyqt_provided_sonames():
    """Every library name PyQt5 brings its own copy of.

    These must NEVER be copied into usr/lib, because usr/lib comes first
    on LD_LIBRARY_PATH and would shadow them.

    This matters more than it looks. PyQt5's Qt is built so that
    libQt5Core re-exports operator new/delete under the Qt_5 symbol
    version - `_ZdlPvm@@Qt_5` - and its plugins are linked expecting
    exactly that. No distribution's Qt5 exports those symbols. So if the
    build host happens to have system Qt5 installed (any KDE desktop
    does), ldd resolves some dependency to the host's libQt5Core, it
    gets bundled into usr/lib, and at runtime it shadows PyQt5's own.
    The app then dies with:

        undefined symbol: _ZdlPvm, version Qt_5

    The Ubuntu build only ever worked because that build box had no
    system Qt5 to resolve against.
    """
    provided = set()
    for d in pyqt_lib_dirs():
        for fn in os.listdir(d):
            if ".so" in fn:
                provided.add(fn)
    return provided


def load_excludelist():
    names = set()
    with open(EXCLUDELIST_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            names.add(line.split()[0])
    return names


def is_excluded(basename, excludelist):
    if basename in excludelist:
        return True
    for pattern in excludelist:
        if basename.startswith(pattern.split(".so")[0] + ".so"):
            return True
    return False


def find_elf_files(root):
    elf_files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            if os.path.islink(path):
                continue
            if fn.endswith(".so") or ".so." in fn or is_elf(path):
                elf_files.append(path)
    return elf_files


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def ldd_deps(path):
    """Returns a dict of {soname_as_requested: resolved_real_path}."""
    try:
        out = subprocess.run(
            ["ldd", path], capture_output=True, text=True, timeout=20
        ).stdout
    except Exception:
        return {}
    deps = {}
    for line in out.splitlines():
        # typical line: "        libfoo.so.1 => /usr/lib/libfoo.so.1 (0x00007f...)"
        #
        # Split on "=>" rather than matching (/\S+) for the path: a run
        # of non-space characters stops dead at the first space, so any
        # build directory with a space in its name - "New Folder (20)",
        # say - made every library under it invisible to this scan while
        # system libraries resolved normally.
        if "=>" in line:
            requested = line.split("=>", 1)[0].strip()
            resolved = line.split("=>", 1)[1].strip()
            resolved = re.sub(r"\s*\(0x[0-9a-fA-F]+\)\s*$", "", resolved).strip()
            if requested and resolved and resolved != "not found" \
                    and os.path.exists(resolved):
                deps[requested] = os.path.realpath(resolved)
                continue
        if "not found" in line:
            print(f"  !! unresolved dependency in {path}: {line.strip()}", file=sys.stderr)
    return deps


def main():
    excludelist = load_excludelist()
    provided_by_pyqt = pyqt_provided_sonames()
    if provided_by_pyqt:
        print(f"PyQt5 provides {len(provided_by_pyqt)} libraries of its own "
              f"(they will not be duplicated into usr/lib).")
    seeds = find_elf_files(APPDIR)
    # Globbed rather than named: the interpreter version depends on the
    # build host (3.12 on Ubuntu 24.04, 3.13 on Fedora 42), and naming
    # it here meant the interpreter itself was silently skipped when it
    # didn't match - so its own dependencies never got bundled.
    for py in glob.glob(os.path.join(APPDIR, "usr", "bin", "python3.*")):
        seeds.append(os.path.realpath(py))
    print(f"Scanning {len(seeds)} ELF files for dependencies...")

    # soname -> real content path
    all_deps = {}
    for s in seeds:
        for soname, real_path in ldd_deps(s).items():
            all_deps.setdefault(soname, real_path)

    os.makedirs(LIB_OUT, exist_ok=True)
    bundled = 0
    skipped_excluded = 0
    skipped_pyqt = []
    fallbacks = []
    for soname, real_path in sorted(all_deps.items()):
        if soname in provided_by_pyqt:
            # Leave it to PyQt5's own copy, found via RUNPATH.
            in_appdir = os.path.realpath(real_path).startswith(
                os.path.realpath(APPDIR))
            skipped_pyqt.append((soname, "" if in_appdir else real_path))
            continue
        if is_excluded(soname, excludelist):
            skipped_excluded += 1
            # Assumed present on every desktop, and usually it is, but
            # not always: stock Ubuntu has no libOpenGL.so.0, and
            # libmpv will not load without it (task 160). So a copy
            # goes in fallback/<soname>/, and AppRun adds that folder
            # only on a system that lacks the library (or, for
            # libstdc++, has an older one).
            if not soname.startswith(GLIBC):
                keep = os.path.join(FALLBACK_OUT, soname)
                os.makedirs(keep, exist_ok=True)
                if not os.path.exists(os.path.join(keep, soname)):
                    shutil.copy2(real_path, os.path.join(keep, soname))
                    fallbacks.append(soname)
            continue
        dest = os.path.join(LIB_OUT, soname)
        if os.path.exists(dest):
            continue
        shutil.copy2(real_path, dest)
        bundled += 1

    # Anything here that resolved outside AppDir is the host's copy of a
    # library PyQt5 also ships - the exact shadowing that breaks Qt
    # plugins. Worth saying out loud, because it is invisible otherwise
    # and only shows up as a symbol lookup error at launch.
    from_host = [(n, p) for n, p in skipped_pyqt if p]
    if from_host:
        print(f"\nNote: {len(from_host)} dependency/ies resolved to the build "
              f"host's copy of a library PyQt5 ships itself.")
        print("      Skipped, so PyQt5's own copies are used at runtime:")
        for soname, path in sorted(from_host)[:10]:
            print(f"        {soname}  (host had {path})")
        if len(from_host) > 10:
            print(f"        ... and {len(from_host) - 10} more")
        print()

    write_requirements(seeds)
    if fallbacks:
        print(f"Fallback copies (used only where the system lacks them): "
              f"{', '.join(sorted(fallbacks))}")
    print(f"Bundled {bundled} shared libraries under their SONAME "
          f"(skipped {skipped_excluded} as host-provided, "
          f"{len(skipped_pyqt)} as PyQt5-provided).")
    # Counted in Python rather than shelled out.
    #
    # These were f-strings interpolated into a shell command, so a build
    # directory with a space in it - "new buildz" - was split into two
    # arguments and both ls and du failed. Nothing here needs a shell.
    names = os.listdir(LIB_OUT)
    total = sum(
        os.path.getsize(os.path.join(LIB_OUT, n))
        for n in names
        if os.path.isfile(os.path.join(LIB_OUT, n))
    )
    print(f"{len(names)} files, {total / (1024 * 1024):.1f} MiB in {LIB_OUT}")


def _version_key(tag):
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def write_requirements(seeds):
    """The newest glibc, libstdc++ and libgcc version each bundle needs.

    Read from every ELF file's version references (objdump -p), so it is
    what the binaries really ask for. Written to fallback/requires for
    AppRun: an older system libstdc++ is swapped for the bundled one,
    and an older glibc gets a plain message instead of a cryptic
    "version `GLIBC_2.38' not found".
    """
    files = set(seeds) | set(find_elf_files(LIB_OUT))
    newest = {}
    for path in files:
        try:
            out = subprocess.run(["objdump", "-p", path], capture_output=True,
                                 text=True, timeout=20).stdout
        except Exception:
            continue
        lib = None
        for line in out.splitlines():
            m = re.match(r"\s+required from (\S+):", line)
            if m:
                lib = m.group(1)
                continue
            m = re.match(r"\s+0x[0-9a-f]+ 0x[0-9a-f]+ \d+ (\S+)", line)
            if m and lib in VERSIONED and \
                    m.group(1).startswith(VERSIONED[lib]):
                tag = m.group(1)
                if lib not in newest or \
                        _version_key(tag) > _version_key(newest[lib]):
                    newest[lib] = tag
    os.makedirs(FALLBACK_OUT, exist_ok=True)
    with open(os.path.join(FALLBACK_OUT, "requires"), "w") as f:
        for lib, tag in sorted(newest.items()):
            f.write(f"{lib} {tag}\n")
    if "libc.so.6" in newest:
        print(f"This AppImage needs glibc {newest['libc.so.6'][6:]} or newer "
              f"on the system that runs it.")


def verify_closure():
    """Clean-room check: for every .so we bundled, confirm every one of
    its own NEEDED sonames is either present in our bundle dir or on
    the excludelist. This does NOT rely on the host's ldconfig cache,
    so it can't be masked by libraries that happen to already be
    installed on the build machine."""
    excludelist = load_excludelist()
    present = set(os.listdir(LIB_OUT))
    # PyQt5's own libraries satisfy dependencies too, via RUNPATH.
    present_via_pyqt = pyqt_provided_sonames()
    problems = []
    for fn in sorted(present):
        path = os.path.join(LIB_OUT, fn)
        if not is_elf(path):
            continue
        try:
            out = subprocess.run(
                ["objdump", "-p", path], capture_output=True, text=True, timeout=20
            ).stdout
        except Exception:
            continue
        needed = re.findall(r"NEEDED\s+(\S+)", out)
        for n in needed:
            if n in present or n in present_via_pyqt:
                continue
            if is_excluded(n, excludelist):
                continue
            problems.append((fn, n))

    if problems:
        print(f"\n!! VERIFICATION FAILED: {len(problems)} missing dependencies:", file=sys.stderr)
        for fn, n in problems:
            print(f"   {fn} needs {n} - not bundled and not on excludelist", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nVerification OK: every bundled library's dependencies are satisfied within the bundle (or excludelist).")


if __name__ == "__main__":
    main()
    verify_closure()
