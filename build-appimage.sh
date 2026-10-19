#!/bin/bash
# build-appimage.sh
#
# Rebuilds the Frequencies AppImage from scratch.
# Tested on Ubuntu 24.04 x86_64. Run as root or with sudo access
# (apt-get install needs it). Needs network access to apt, PyPI, and
# GitHub.
#
# On Fedora use build-appimage-fedora.sh instead: the package manager,
# the package names, the Python version and the library directory all
# differ, so this script cannot simply have its apt-get line swapped.
#
# Usage:
#   ./build-appimage.sh
#
# Output:
#   ./Frequencies-x86_64.AppImage

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

echo "==> Installing system dependencies (mpv/libmpv, python venv, tools)"
apt-get update
apt-get install -y \
    libmpv2 mpv python3-venv python3-pip \
    file desktop-file-utils imagemagick python3-pil curl binutils

PYVER="$(python3 -c 'import sys; print("python%d.%d" % sys.version_info[:2])')"
echo "==> Building against ${PYVER}"

echo "==> Creating Python virtualenv with app dependencies"
rm -rf venv
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install "PyQt5==5.15.11" python-mpv requests
# yt-dlp powers YouTube custom stations. Optional at runtime - without
# it those stations say so rather than failing oddly - but bundling it
# means they work without a separate install. It is also the one
# dependency here that genuinely rots: YouTube changes its internals
# often, so a station that stops working usually wants a newer yt-dlp.
./venv/bin/pip install yt-dlp
# shazamio-core is the Rust fingerprinting engine used for song
# identification. Only the core is installed, not the full shazamio
# package - src/shazam.py makes the lookup request with `requests`,
# which keeps aiohttp, pydantic, pydub and numpy (~50 MB) out of the
# AppImage.
./venv/bin/pip install shazamio-core

# Trimming is purely a size optimisation - it deletes the parts of
# PyQt5's bundled Qt the app never loads. Set SKIP_TRIM=1 to leave the
# bundle whole: the AppImage comes out a few hundred MB larger but is
# otherwise identical, which makes it a useful escape hatch when the
# trimmer is unhappy.
if [ "${SKIP_TRIM:-0}" = "1" ]; then
    echo "==> Skipping the PyQt5 trim (SKIP_TRIM=1) - larger AppImage"
else
    echo "==> Trimming PyQt5's bundled Qt5 to only what we use"
    ./venv/bin/python3 trim_pyqt.py
fi

# The bundle must still own its Qt after trimming.
#
# PyQt5's Qt is built so libQt5Core re-exports operator new/delete under
# the Qt_5 symbol version, and its plugins are linked expecting exactly
# that; no distribution's Qt5 exports them. So an AppImage that ends up
# carrying the host's Qt instead of PyQt5's builds fine and then dies on
# launch with "undefined symbol: _ZdlPvm, version Qt_5". Catching it
# here costs a second and saves rebuilding blind.
if ! find venv -path "*/PyQt5/Qt5/lib/libQt5Core.so.5" | grep -q .; then
    echo "!! PyQt5's bundled Qt is missing after trimming." >&2
    echo "   Expected venv/.../PyQt5/Qt5/lib/libQt5Core.so.5" >&2
    echo "   Without it the AppImage will pick up the host's Qt and" >&2
    echo "   fail at startup. Not continuing." >&2
    exit 1
fi

echo "==> Assembling AppDir"
rm -rf AppDir
mkdir -p AppDir/usr/bin AppDir/usr/lib AppDir/usr/share/frequencies \
         AppDir/usr/share/applications AppDir/usr/share/icons/hicolor/256x256/apps

cp -a "venv/lib/${PYVER}/site-packages" "AppDir/usr/lib/${PYVER}-site-packages"
find "AppDir/usr/lib/${PYVER}-site-packages" -name "__pycache__" -type d -prune -exec rm -rf {} + || true
find "AppDir/usr/lib/${PYVER}-site-packages" -name "*.dist-info" -type d -prune -exec rm -rf {} + || true
rm -f "AppDir/usr/lib/${PYVER}-site-packages/PyQt5/Qt5/plugins/imageformats/libqpdf.so" || true

cp "/usr/bin/${PYVER}" "AppDir/usr/bin/${PYVER}"
cp -a "/usr/lib/${PYVER}" "AppDir/usr/lib/${PYVER}"
rm -rf "AppDir/usr/lib/${PYVER}/site-packages"
mv "AppDir/usr/lib/${PYVER}-site-packages" "AppDir/usr/lib/${PYVER}/site-packages"
rm -rf "AppDir/usr/lib/${PYVER}/test"
find "AppDir/usr/lib/${PYVER}" -name "__pycache__" -type d -prune -exec rm -rf {} + || true
find "AppDir/usr/lib/${PYVER}" -name "*.pyc" -delete

# Read ldconfig's output into a variable and match it with shell
# builtins rather than piping into grep/awk. An early-exiting consumer
# closes the pipe, ldconfig dies of SIGPIPE, and `set -o pipefail` then
# reports the whole pipeline as failed even though the match succeeded -
# which looks exactly like "libmpv is not installed" on any machine with
# more than ~64 KB of ldconfig output. See build-appimage-fedora.sh.
LIBMPV_REAL=""
_cache="$(ldconfig -p 2>/dev/null || true)"
while IFS= read -r _line; do
    case "${_line}" in
        *libmpv.so.*)
            _candidate="${_line##* }"
            if [ -e "${_candidate}" ]; then
                LIBMPV_REAL="$(readlink -f "${_candidate}")"
                break
            fi
            ;;
    esac
done <<< "${_cache}"

if [ -z "${LIBMPV_REAL}" ]; then
    for _candidate in /usr/lib/x86_64-linux-gnu/libmpv.so.* /usr/lib/libmpv.so.* /lib/x86_64-linux-gnu/libmpv.so.*; do
        if [ -e "${_candidate}" ]; then
            LIBMPV_REAL="$(readlink -f "${_candidate}")"
            break
        fi
    done
fi

if [ -z "${LIBMPV_REAL}" ]; then
    echo "!! Could not find libmpv - is libmpv2 installed?" >&2
    exit 1
fi
echo "    libmpv: ${LIBMPV_REAL}"
cp -L "$LIBMPV_REAL" AppDir/usr/lib/libmpv.so.2

cp src/*.py AppDir/usr/share/frequencies/
cp src/*.json AppDir/usr/share/frequencies/ 2>/dev/null || true
# Clippy's sprite sheet and animations (build 107, task 116), with their
# license and credits.
mkdir -p AppDir/usr/share/frequencies/companion
cp src/companion/* AppDir/usr/share/frequencies/companion/
# Winamp's llama, played once the first time the Winamp view opens
# (build 114, task 154).
mkdir -p AppDir/usr/share/frequencies/sounds
cp src/sounds/* AppDir/usr/share/frequencies/sounds/ 2>/dev/null || true
# The bundled skins, the Frequencies skin among them (build 109).
mkdir -p AppDir/usr/share/frequencies/skins
cp src/skins/*.wsz AppDir/usr/share/frequencies/skins/ 2>/dev/null || true
# The between-station static: real radio recordings, loudness-matched
# ahead of time so no clip jumps out when the mixer picks it.
mkdir -p AppDir/usr/share/frequencies/static
cp static/*.ogg AppDir/usr/share/frequencies/static/

# The app's own icons, next to the code that loads them.
#
# The 256px one at the AppDir root is what the desktop file and the
# launcher use; these are what the running app needs for its window and
# its tray entry. Without them the tray icon is empty, which on most
# desktops means an invisible entry rather than a missing one.
mkdir -p AppDir/usr/share/frequencies/icons
cp icons/*.png AppDir/usr/share/frequencies/icons/
cp frequencies.png AppDir/usr/share/frequencies/
cp AppRun AppDir/AppRun
chmod +x AppDir/AppRun
cp frequencies.desktop AppDir/
cp frequencies.desktop AppDir/usr/share/applications/
cp frequencies.png AppDir/
cp frequencies.png AppDir/usr/share/icons/hicolor/256x256/apps/

echo "==> Bundling shared library dependency closure"
if [ ! -f excludelist ]; then
    curl -fsSL -o excludelist "https://raw.githubusercontent.com/AppImage/pkg2appimage/master/excludelist"
fi
python3 bundle_deps.py

echo "==> Downloading appimagetool"
if [ ! -f appimagetool ]; then
    curl -fsSL -o appimagetool "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool
fi

echo "==> Building AppImage"
ARCH=x86_64 ./appimagetool --no-appstream AppDir Frequencies-x86_64.AppImage

echo "==> Done: ./Frequencies-x86_64.AppImage"
