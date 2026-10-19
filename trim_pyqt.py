import subprocess, os, shutil, re, sys, glob

# Globbed: the venv's Python version follows whatever python3 is on the
# build host, so hardcoding it breaks the build on any other distro.
_CANDIDATES = sorted(glob.glob("venv/lib/python3.*/site-packages")) or \
    sorted(glob.glob("venv/lib64/python3.*/site-packages"))
if not _CANDIDATES:
    sys.exit("trim_pyqt: no venv site-packages found - run the venv step first")
SP = _CANDIDATES[0]
PYQT = f"{SP}/PyQt5"
QT5 = f"{PYQT}/Qt5"

def ldd_deps(path):
    """Dependencies of one file, resolved against the BUNDLED Qt first.

    The LD_LIBRARY_PATH matters enormously. Without it, ldd resolves
    libQt5Core.so.5 and friends against the host system - and on any
    machine that happens to have Qt5 installed (every KDE desktop), the
    resolved paths land in /usr/lib64 rather than inside PyQt5's own
    Qt5/lib. They then fail the "is this one of ours?" test below, get
    classified as unneeded, and are DELETED from the bundle. The build
    then quietly substitutes the host's Qt, whose plugins expect
    different symbols, and the finished AppImage dies at startup with:

        undefined symbol: _ZdlPvm, version Qt_5

    Ubuntu never showed this because that build box had no system Qt5.
    """
    env = dict(os.environ)
    qt_lib = os.path.realpath(f"{QT5}/lib")
    env["LD_LIBRARY_PATH"] = qt_lib + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    try:
        out = subprocess.run(["ldd", path], capture_output=True, text=True,
                             env=env).stdout
    except Exception:
        return set()
    deps = set()
    for line in out.splitlines():
        # Split on "=>" rather than regex-matching a run of non-space
        # characters. The old pattern was `=>\s*(\S+\.so\S*)`, which
        # silently captured nothing at all when the path contained a
        # space - and a build directory called "New Folder (20)" is
        # exactly that. Every bundled Qt library lives under the build
        # directory, so all of them were dropped while system libraries
        # in /lib64 resolved fine: the scan reported plenty of
        # dependencies and zero Qt ones, and the bundled Qt was then
        # deleted as "unneeded".
        if "=>" not in line:
            continue                      # vdso, or the loader itself
        target = line.split("=>", 1)[1].strip()
        # Drop the trailing load address, e.g. " (0x00007f...)".
        target = re.sub(r"\s*\(0x[0-9a-fA-F]+\)\s*$", "", target).strip()
        if not target or target == "not found":
            continue
        if os.path.exists(target):
            deps.add(os.path.realpath(target))
    return deps

# Seed files we actually need at the Python-extension level
seeds = [
    f"{PYQT}/QtCore.abi3.so",
    f"{PYQT}/QtGui.abi3.so",
    f"{PYQT}/QtWidgets.abi3.so",
    # QtDBus is what makes desktop media keys work (MPRIS).
    f"{PYQT}/QtDBus.abi3.so",
]
seeds += [os.path.join(PYQT, f) for f in os.listdir(PYQT) if f.startswith("sip")]

# Plugins we want to keep functionally (trim the rest).
# The wayland-* dirs are what make the app a native Wayland client:
#   wayland-shell-integration        - xdg-shell etc; without it the
#                                      wayland platform plugin loads but
#                                      can't create a toplevel window
#   wayland-decoration-client        - client-side title bar / borders
#   wayland-graphics-integration-client - EGL and shm buffer backends
keep_plugin_dirs = [
    "platforms",
    "imageformats",
    "iconengines",
    "platforminputcontexts",
    "styles",
    "wayland-shell-integration",
    "wayland-decoration-client",
    "wayland-graphics-integration-client",
    # xcbglintegrations is how Qt gets an OpenGL context under X11 -
    # libqxcb-glx-integration and libqxcb-egl-integration. Without them
    # QOpenGLWidget cannot create a context at all, and it fails
    # silently: the widget never paints, so the window shows whatever
    # was behind it and smears when dragged. That is the blank
    # ghost-trailing visualiser.
    "xcbglintegrations",
    # And the equivalent for EGL devices, used by some setups.
    "egldeviceintegrations",
]
plugin_seeds = []
for d in keep_plugin_dirs:
    dpath = f"{QT5}/plugins/{d}"
    if os.path.isdir(dpath):
        for f in os.listdir(dpath):
            if f.endswith(".so"):
                plugin_seeds.append(os.path.join(dpath, f))

all_seeds = seeds + plugin_seeds
needed = set(os.path.realpath(s) for s in all_seeds if os.path.exists(s))
frontier = set(needed)
while frontier:
    new = set()
    for f in frontier:
        for d in ldd_deps(f):
            if d not in needed:
                needed.add(d)
                new.add(d)
    frontier = new

# Only care about libs living inside our venv's PyQt5/Qt5/lib dir (bundled Qt libs)
needed_qt_libnames = set()
for n in needed:
    if n.startswith(os.path.realpath(f"{QT5}/lib")):
        needed_qt_libnames.add(os.path.basename(n))

print("Qt5 libs required:", len(needed_qt_libnames))
for n in sorted(needed_qt_libnames):
    print(" -", n)

# Refuse to proceed if the dependency scan came back implausible.
#
# Deleting is irreversible within a build, and "keep nothing" is never a
# correct answer for a working PyQt5 - it only happens when resolution
# went somewhere unexpected. Failing loudly here beats shipping an
# AppImage that is missing its own Qt and silently falls back to the
# host's.
_essential = {"libQt5Core.so.5", "libQt5Gui.so.5", "libQt5Widgets.so.5"}
_missing = _essential - needed_qt_libnames
if _missing:
    # Say WHY, not just that. A bare refusal sends you hunting; the
    # ldd output below names the actual resolution that went wrong.
    print()
    print("=" * 68)
    print("trim_pyqt: refusing to trim - the dependency scan did not find")
    print(f"  {sorted(_missing)}")
    print(f"inside {QT5}/lib")
    print()
    print(f"  seeds that exist : {sum(1 for s in all_seeds if os.path.exists(s))}"
          f" of {len(all_seeds)}")
    print(f"  libs resolved    : {len(needed)}")
    print(f"  Qt5/lib present  : {os.path.isdir(f'{QT5}/lib')}")
    if os.path.isdir(f"{QT5}/lib"):
        libs = sorted(os.listdir(f"{QT5}/lib"))
        print(f"  files in Qt5/lib : {len(libs)}"
              + (f"  (e.g. {libs[:3]})" if libs else "  <- EMPTY"))
    probe = f"{PYQT}/QtCore.abi3.so"
    print(f"  probe file       : {probe}")
    print(f"  exists           : {os.path.exists(probe)}")
    if os.path.exists(probe):
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = os.path.realpath(f"{QT5}/lib")
        out = subprocess.run(["ldd", probe], capture_output=True, text=True,
                             env=env)
        print("  ldd QtCore.abi3.so:")
        for line in (out.stdout + out.stderr).splitlines()[:12]:
            print("     ", line.strip())
    print("=" * 68)
    sys.exit(1)

# QtWebEngine.s helper and data must survive.
#
# The trimmer works by asking ldd what the Python extension modules
# need, and QtWebEngineProcess is a separate executable that nothing
# links against - so without this it looks unused and gets deleted,
# leaving an embedded browser that opens blank.
WEBENGINE_KEEP = (
    "libQt5WebEngineCore.so", "libQt5WebEngine.so",
    "libQt5WebEngineWidgets.so", "libQt5Quick.so", "libQt5Qml.so",
    "libQt5QmlModels.so", "libQt5WebChannel.so", "libQt5Positioning.so",
    "libQt5PrintSupport.so",
)

# Now delete unneeded libs from Qt5/lib
# When QtWebEngine is bundled, don't trim at all.
#
# Chromium pulls in a wide and non-obvious set of Qt libraries, several
# of them loaded at runtime rather than linked - so ldd cannot see them
# and the trimmer would remove exactly the ones the browser needs. The
# space saved is not worth an embedded browser that opens blank.
if os.path.exists(f"{QT5}/libexec/QtWebEngineProcess"):
    print("QtWebEngine is bundled - skipping the trim entirely.")
    raise SystemExit(0)

removed = 0
kept = 0
for f in os.listdir(f"{QT5}/lib"):
    fpath = f"{QT5}/lib/{f}"
    if os.path.islink(fpath) or os.path.isfile(fpath):
        if f in needed_qt_libnames:
            kept += 1
        else:
            if any(f.startswith(keep) for keep in WEBENGINE_KEEP):
                kept += 1
                continue
            os.remove(fpath)
            removed += 1
print(f"Removed {removed} unneeded Qt5 lib files, kept {kept}")

# Trim plugin dirs to only the ones we chose to keep
for d in os.listdir(f"{QT5}/plugins"):
    if d not in keep_plugin_dirs:
        shutil.rmtree(f"{QT5}/plugins/{d}", ignore_errors=True)

# Within platforms, keep xcb + wayland (native Wayland) + offscreen and
# minimal as small, useful fallbacks. The xcomposite wayland variants are
# legacy and not worth the space.
plat_dir = f"{QT5}/plugins/platforms"
if os.path.isdir(plat_dir):
    for f in os.listdir(plat_dir):
        if "xcomposite" in f:
            os.remove(os.path.join(plat_dir, f))
        elif not any(k in f for k in ("xcb", "wayland", "offscreen", "minimal")):
            os.remove(os.path.join(plat_dir, f))

# Remove unneeded top-level PyQt5 .abi3.so modules we don't import
needed_top = {"QtCore.abi3.so", "QtGui.abi3.so", "QtWidgets.abi3.so",
               "QtDBus.abi3.so", "Qt.abi3.so"}
for f in os.listdir(PYQT):
    if f.endswith(".abi3.so") and f not in needed_top:
        os.remove(os.path.join(PYQT, f))

# Remove Qt5 subdirs we don't need at all (qml, translations optional, etc.)
for extra in ["qml", "translations", "resources", "libexec", "bin"]:
    p = f"{QT5}/{extra}"
    if os.path.exists(p):
        shutil.rmtree(p, ignore_errors=True)

print("Done trimming.")
