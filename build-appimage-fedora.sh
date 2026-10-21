#!/bin/bash
# build-appimage-fedora.sh
#
# Rebuilds the Frequencies AppImage on Fedora.
# Tested against Fedora 42 x86_64; the same script builds an arm64
# AppImage when run on arm64 hardware. Needs sudo (for dnf) and network
# access to dnf, PyPI and GitHub.
#
# This is the Fedora sibling of build-appimage.sh. Four things differ
# from the Debian/Ubuntu build, and all four are why that script can't
# just have its apt-get line swapped:
#
#   1. dnf, and different package names (mpv-libs, not libmpv2).
#   2. A different Python version - 3.13 here, 3.12 on Ubuntu 24.04 -
#      so nothing may hardcode "python3.12".
#   3. Libraries live in /usr/lib64, not /usr/lib/x86_64-linux-gnu.
#   4. Fedora builds its Python with --with-platlibdir=lib64, so the
#      standard library sits in /usr/lib64/python3.13 and the
#      interpreter looks for it under lib64 inside the AppImage too.
#
# Usage:
#   ./build-appimage-fedora.sh          a user build - the one you share
#   ./build-appimage-fedora.sh --dev    a dev build - for you: shows the
#                                       dev-only settings (task 49)
#
# Output:
#   ./Frequencies-x86_64.AppImage      (or -aarch64 on an arm64 machine)
#   ./Frequencies-dev-x86_64.AppImage  (with --dev)
#
# Both copy the talk stations you set in a dev build into the app first
# (tools/ship_talk_stations.py), so a user build ships them. Same for the
# visualizer presets you disabled (tools/ship_visualizer_disabled.py).
# Photos never ship: tools/make_photo_pack.py puts them in
#   ./photo-pack/frequencies-photo-pack.zip and photo-pack.json
# for you to upload to the photo pack's release (build 91).

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

DEV_BUILD=0
for arg in "$@"; do
    case "${arg}" in
        --dev) DEV_BUILD=1 ;;
        *) echo "!! Unknown option: ${arg} (only --dev)" >&2; exit 1 ;;
    esac
done

# --------------------------------------------------------------------
# Build for whatever machine this is running on.
#
# An AppImage bundles the host's own shared libraries, so it can only
# ever be built for the architecture it is built on - there is no
# cross-compiling here without a full sysroot. Running this script on an
# arm64 machine produces an arm64 AppImage; on x86_64, an x86_64 one.
HOST_ARCH="$(uname -m)"
case "${HOST_ARCH}" in
    x86_64)          APP_ARCH=x86_64 ;;
    aarch64|arm64)   APP_ARCH=aarch64 ;;
    *)
        echo "!! Unsupported architecture: ${HOST_ARCH}" >&2
        echo "   AppImage supports x86_64 and aarch64." >&2
        exit 1
        ;;
esac
OUTPUT="Frequencies-${APP_ARCH}.AppImage"
if [ "${DEV_BUILD}" = 1 ]; then
    OUTPUT="Frequencies-dev-${APP_ARCH}.AppImage"
    echo "==> Building for ${APP_ARCH} (dev build)"
else
    echo "==> Building for ${APP_ARCH} (user build)"
fi

echo "==> Installing system dependencies"
# ImageMagick and python3-pillow are only needed if you regenerate the
# icon; they're cheap, so they're here rather than being a surprise
# later. mpv-libs provides libmpv.so.2, which is what python-mpv loads.
# An immutable host cannot install build dependencies.
#
# Bazzite, Silverblue, Kinoite and the rest of the Fedora Atomic family
# have a read-only /usr, so dnf is either missing or refuses. Building in
# a container is the supported answer there, and distrobox ships with
# Bazzite — so this says exactly what to run rather than failing on the
# first dnf line with something cryptic.
if [ -f /run/ostree-booted ] || [ -d /ostree ]; then
    cat >&2 <<'IMMUTABLE'

This looks like an immutable system (Bazzite, Silverblue, Kinoite...).
The base image is read-only, so build dependencies cannot be installed
on the host. Build inside a Fedora container instead:

    distrobox create --name freqbuild --image fedora:latest
    distrobox enter freqbuild
    cd <this folder>
    ./build-appimage-fedora.sh

The AppImage it produces runs on the host as normal — nothing about the
result is container-specific.

If distrobox is not present, `toolbox create` works the same way.

IMMUTABLE
    exit 1
fi

sudo dnf install -y \
    mpv-libs python3 python3-pip \
    file desktop-file-utils appstream ImageMagick python3-pillow curl binutils
# Libraries the app loads that the scan can only bundle if they are here
# (task 185): SDL3, which Fedora's libSDL2 (sdl2-compat, linked by
# libmpv) loads by name and quits without; and the X11 helpers PyQt5's
# xcb plugin needs. libxkbcommon-x11 must come from the same release as
# the bundled libxkbcommon: Ubuntu 22.04's older one crashed Qt at start.
sudo dnf install -y \
    SDL3 libxkbcommon-x11 xcb-util-wm xcb-util-image xcb-util-keysyms \
    xcb-util-renderutil

# Find libmpv, whatever it's called and wherever it lives.
#
# Note the complete absence of pipes here, which is deliberate.
#
# The obvious way to write this is `ldconfig -p | grep -q libmpv.so.2`.
# Under `set -o pipefail` that is a trap: grep -q exits the instant it
# matches, ldconfig is still writing, so ldconfig dies of SIGPIPE and
# the pipeline reports 141. pipefail propagates that failure, and the
# script concludes libmpv is missing on a machine where it is plainly
# installed. It only bites when ldconfig's output is bigger than the
# 64 KB pipe buffer - which is to say, on a real desktop with a few
# thousand libraries, and not on a small build container. That is
# exactly the bug that sent this script into its "install RPM Fusion"
# branch when RPM Fusion had nothing to do with it.
#
# Reading into a variable and matching with shell builtins has no
# producer to kill, so it cannot fail that way.
#
# The search is also version-agnostic: mpv 0.35 shipped libmpv.so.1,
# 0.36 onwards ship libmpv.so.2, and it will move again.
find_libmpv() {
    local cache line candidate dir

    cache="$(ldconfig -p 2>/dev/null || true)"
    while IFS= read -r line; do
        case "${line}" in
            *libmpv.so.*)
                candidate="${line##* }"
                if [ -e "${candidate}" ]; then
                    echo "${candidate}"
                    return 0
                fi
                ;;
        esac
    done <<< "${cache}"

    # Not in the cache (a fresh install that ldconfig hasn't seen yet):
    # look where the file would actually be.
    for dir in /usr/lib64 /usr/lib /lib64 /lib /usr/local/lib64 /usr/local/lib; do
        for candidate in "${dir}"/libmpv.so.*; do
            [ -e "${candidate}" ] || continue
            echo "${candidate}"
            return 0
        done
    done

    # Last resort: ask rpm what the package actually put on disk.
    while IFS= read -r line; do
        case "${line}" in
            *libmpv.so.*)
                if [ -e "${line}" ]; then
                    echo "${line}"
                    return 0
                fi
                ;;
        esac
    done <<< "$(rpm -ql mpv-libs 2>/dev/null || true)"

    return 1
}

LIBMPV_PATH="$(find_libmpv || true)"
if [ -z "${LIBMPV_PATH}" ]; then
    cat >&2 <<'MSG'

!! Could not find libmpv anywhere.

   mpv-libs provides it. If that is already installed, please run:

     rpm -ql mpv-libs | grep libmpv
     ls -l /usr/lib64/libmpv*

   and send the output - the library is evidently somewhere this
   script does not know to look.

   If mpv-libs is genuinely missing, it lives in RPM Fusion on some
   Fedora releases:

     # An immutable host cannot install build dependencies.
#
# Bazzite, Silverblue, Kinoite and the rest of the Fedora Atomic family
# have a read-only /usr, so dnf is either missing or refuses. Building in
# a container is the supported answer there, and distrobox ships with
# Bazzite — so this says exactly what to run rather than failing on the
# first dnf line with something cryptic.
if [ -f /run/ostree-booted ] || [ -d /ostree ]; then
    cat >&2 <<'IMMUTABLE'

This looks like an immutable system (Bazzite, Silverblue, Kinoite...).
The base image is read-only, so build dependencies cannot be installed
on the host. Build inside a Fedora container instead:

    distrobox create --name freqbuild --image fedora:latest
    distrobox enter freqbuild
    cd <this folder>
    ./build-appimage-fedora.sh

The AppImage it produces runs on the host as normal — nothing about the
result is container-specific.

If distrobox is not present, `toolbox create` works the same way.

IMMUTABLE
    exit 1
fi

sudo dnf install -y \
       https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
     sudo dnf install -y mpv-libs

MSG
    exit 1
fi

# The filename the dynamic linker will ask for, e.g. "libmpv.so.2".
# /usr/lib64/libmpv.so.2 -> libmpv.so.2.5.0 is the normal arrangement:
# the symlink is named after the soname, the target is the real file.
# Bundle the bytes under the soname, because that is the exact string
# other libraries' DT_NEEDED entries ask for.
LIBMPV_SONAME="$(basename "${LIBMPV_PATH}")"
case "${LIBMPV_SONAME}" in
    libmpv.so.*.*)
        # We landed on the fully-versioned real file rather than the
        # symlink, so read the soname out of the ELF header instead.
        _objout="$(objdump -p "${LIBMPV_PATH}" 2>/dev/null || true)"
        _soname="$(awk '/SONAME/ {print $2; exit}' <<< "${_objout}")"
        [ -n "${_soname}" ] && LIBMPV_SONAME="${_soname}"
        ;;
esac
echo "    libmpv: ${LIBMPV_PATH} -> ${LIBMPV_SONAME}"

# --------------------------------------------------------------------
echo "==> Detecting the host Python layout"
PYVER="$(python3 -c 'import sys; print("python%d.%d" % sys.version_info[:2])')"
PLATLIBDIR="$(python3 -c 'import sysconfig; print(sysconfig.get_config_var("platlibdir") or "lib")')"
PY_STDLIB="$(python3 -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"
PY_BIN="$(readlink -f "$(command -v python3)")"

echo "    interpreter : ${PY_BIN} (${PYVER})"
echo "    stdlib      : ${PY_STDLIB}"
echo "    platlibdir  : ${PLATLIBDIR}"

if [ ! -d "${PY_STDLIB}" ]; then
    echo "!! Could not locate the Python standard library at ${PY_STDLIB}" >&2
    exit 1
fi

# --------------------------------------------------------------------
echo "==> Creating Python virtualenv with app dependencies"
rm -rf venv
python3 -m venv venv
./venv/bin/pip install --upgrade pip
# PyQt5 comes from pip on x86_64 and from the distro on arm64.
#
# There is no Linux aarch64 wheel for PyQt5 on PyPI - only x86_64,
# Windows and macOS - so pip would fall back to building Qt from
# source, which takes hours and usually fails. Fedora packages it, so
# on arm64 the venv is given access to the system copy instead.
if [ "${APP_ARCH}" = "aarch64" ]; then
    echo "    (arm64: using the distro's PyQt5, no wheel exists on PyPI)"
    sudo dnf install -y python3-qt5 python3-qt5-base >/dev/null
    rm -rf venv
    python3 -m venv --system-site-packages venv
    ./venv/bin/pip install --upgrade pip
    # segno draws the QR code in the share window. Pure Python, no
    # compiled parts, a few tens of kilobytes.
    # segno writes QR codes; zxing-cpp reads them back. Both ship as
    # wheels, so neither needs anything installed on the machine.
    ./venv/bin/pip install python-mpv requests segno zxing-cpp
else
    ./venv/bin/pip install "PyQt5==5.15.11" python-mpv requests segno \
        zxing-cpp
    # The embedded browser: lyrics from sites that block scrapers, and
    # picking station art from an image search. Optional - the app falls
    # back to the system browser if this isn't present - but it is a
    # much better experience bundled.
    ./venv/bin/pip install "PyQtWebEngine==5.15.7" || \
        echo "!! QtWebEngine unavailable; the embedded browser will fall back"
fi
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
# shazamio-core has no aarch64 wheel either. pip would try to build it,
# which needs a Rust toolchain; if that isn't present the build carries
# on without it and song identification reports itself unavailable,
# rather than the whole build failing over an optional feature.
if ! ./venv/bin/pip install shazamio-core; then
    echo "!! shazamio-core could not be installed."
    if [ "${APP_ARCH}" = "aarch64" ]; then
        echo "   No aarch64 wheel exists; building it needs Rust:"
        echo "     sudo dnf install -y cargo rust"
        echo "   Continuing without song identification."
    fi
fi

# Trimming is purely a size optimisation - it deletes the parts of
# PyQt5's bundled Qt the app never loads. Set SKIP_TRIM=1 to leave the
# bundle whole: the AppImage comes out a few hundred MB larger but is
# otherwise identical, which makes it a useful escape hatch when the
# trimmer is unhappy.
if [ "${APP_ARCH}" = "aarch64" ]; then
    # Nothing to trim: on arm64 Qt is the system's, not a bundled copy
    # inside site-packages.
    echo "==> Skipping the PyQt5 trim (system Qt on arm64)"
elif [ "${SKIP_TRIM:-0}" = "1" ]; then
    echo "==> Skipping the PyQt5 trim (SKIP_TRIM=1) - larger AppImage"
else
    echo "==> Trimming PyQt5's bundled Qt5 to only what we use"
    ./venv/bin/python3 trim_pyqt.py
fi

# The bundle must still own its Qt after trimming. Only meaningful when
# PyQt5 brought its own - on arm64 it is the distro's, bundled by
# bundle_deps.py like any other system library.
if [ "${APP_ARCH}" != "aarch64" ]; then
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
fi

# The venv may use lib or lib64 depending on how python3 was built.
VENV_SP=""
for _candidate in "venv/lib/${PYVER}/site-packages" "venv/lib64/${PYVER}/site-packages"; do
    if [ -d "${_candidate}" ]; then VENV_SP="${_candidate}"; break; fi
done
if [ -z "${VENV_SP}" ]; then
    echo "!! Could not find the venv's site-packages" >&2
    exit 1
fi
echo "    venv packages: ${VENV_SP}"

# --------------------------------------------------------------------
# ---------------------------------------------------------------------
# projectM, built from source and bundled.
#
# The distro packages carry projectM 2.x, which is C++-only and cannot
# be driven from Python; the app needs the version 4 C API. Building it
# here means the AppImage carries its own copy and nothing has to be
# preinstalled on the machine that runs it.
#
# A small preset pack rides along - a visualiser with nothing to show
# is just a black square. Cream of the Crop is thousands of files, so a
# curated slice is fetched rather than the lot.
# ---------------------------------------------------------------------
PROJECTM_VERSION="v4.1.4"
# Note the `|| true`. The script runs under `set -euo pipefail`, and
# find exits non-zero when projectm-build does not exist yet - which is
# every first run. Without it the assignment's failure status ends the
# whole build here, silently, before the AppImage is ever made.
PROJECTM_LIB=""
if [ -d projectm-build ]; then
    PROJECTM_LIB="$(find projectm-build -name 'libprojectM-4.so.4*' 2>/dev/null | head -1 || true)"
fi

if [ -z "${PROJECTM_LIB}" ]; then
    echo "==> Building projectM ${PROJECTM_VERSION} (bundled visualiser)"

    # projectM needs OpenGL headers and glm to build. They are build-time
    # only - the AppImage bundles the resulting .so, and libGL itself is
    # deliberately NOT bundled because it has to come from the host's
    # driver stack.
    sudo dnf install -y --setopt=install_weak_deps=False \
        cmake git gcc-c++ mesa-libGL-devel mesa-libEGL-devel glm-devel \
        >/dev/null 2>&1 || \
        echo "    (could not install projectM build dependencies)"

    # pw-record is how the visualiser hears the audio. It is a runtime
    # tool run as a subprocess, not a library, so it is not bundled -
    # but it is worth making sure the build machine has it.
    sudo dnf install -y --setopt=install_weak_deps=False \
        pipewire-utils >/dev/null 2>&1 || true

    if command -v cmake >/dev/null 2>&1; then
        rm -rf projectm-src projectm-build
        # --recurse-submodules matters: projectM-Eval is a submodule, not
        # a system package, and without it cmake stops looking for a
        # projectM-EvalConfig.cmake that is never going to exist.
        if git clone --depth 1 --recurse-submodules --shallow-submodules \
               --branch "${PROJECTM_VERSION}" \
               https://github.com/projectM-visualizer/projectm.git \
               projectm-src >/dev/null 2>&1
        then
            cmake -S projectm-src -B projectm-src/build \
                  -DCMAKE_BUILD_TYPE=Release \
                  -DCMAKE_INSTALL_PREFIX="${PWD}/projectm-build" \
                  -DENABLE_SDL_UI=OFF -DBUILD_TESTING=OFF \
                  -DBUILD_SHARED_LIBS=ON \
            && cmake --build projectm-src/build -j"$(nproc)" \
            && cmake --install projectm-src/build \
            || echo "    (projectM build failed; Options will say so)"
        else
            echo "    (could not fetch projectM; Options will say so)"
        fi
    else
        echo "    (cmake not found; skipping the bundled visualiser)"
    fi
fi

if [ ! -d "projectm-presets" ]; then
    echo "==> Fetching a preset pack"
    if git clone --depth 1 \
           https://github.com/projectM-visualizer/presets-cream-of-the-crop.git \
           projectm-presets-full >/dev/null 2>&1
    then
        mkdir -p projectm-presets
        # -print0 / xargs -0: preset filenames are full of apostrophes
        # and spaces ("Geiss - Journey's End.milk"), which plain xargs
        # treats as quoting and chokes on.
        # `|| true` again: head closes the pipe once it has its 120
        # names, which hands find a SIGPIPE, which pipefail turns into a
        # failed pipeline and set -e turns into a dead build.
        # All of them by default now.
        #
        # This kept the first 120 to hold the AppImage size down, which
        # was a guess made once and never revisited — and it is the wrong
        # default for anyone choosing their own set, since you cannot
        # pick from presets that were never bundled. Set
        # FREQ_PRESET_LIMIT to a number to go back to a subset.
        if [ -n "${FREQ_PRESET_LIMIT:-}" ]; then
            find projectm-presets-full -name '*.milk' -print0 2>/dev/null \
                | sort -z | head -z -n "$FREQ_PRESET_LIMIT" \
                | xargs -0 -I{} cp {} projectm-presets/ 2>/dev/null || true
        else
            find projectm-presets-full -name '*.milk' -print0 2>/dev/null \
                | xargs -0 -I{} cp {} projectm-presets/ 2>/dev/null || true
        fi
        rm -rf projectm-presets-full
        echo "    bundled $(find projectm-presets -name '*.milk' 2>/dev/null | wc -l || echo 0) presets"
    else
        echo "    (preset fetch failed; visualiser renders its default)"
    fi
fi

echo "==> Assembling AppDir"
rm -rf AppDir
mkdir -p AppDir/usr/bin "AppDir/usr/${PLATLIBDIR}" AppDir/usr/lib \
         AppDir/usr/share/frequencies \
         AppDir/usr/share/applications \
         AppDir/usr/share/icons/hicolor/256x256/apps \
         AppDir/usr/share/projectM/presets

# The interpreter and its standard library. The stdlib goes under
# whatever platlibdir this Python was built to look in, or it won't be
# found at runtime no matter what PYTHONPATH says.
cp "${PY_BIN}" "AppDir/usr/bin/${PYVER}"
cp -a "${PY_STDLIB}" "AppDir/usr/${PLATLIBDIR}/${PYVER}"
DEST_STDLIB="AppDir/usr/${PLATLIBDIR}/${PYVER}"

rm -rf "${DEST_STDLIB}/site-packages" "${DEST_STDLIB}/test"
cp -a "${VENV_SP}" "${DEST_STDLIB}/site-packages"

find "${DEST_STDLIB}" -name "__pycache__" -type d -prune -exec rm -rf {} + || true
find "${DEST_STDLIB}" -name "*.pyc" -delete
find "${DEST_STDLIB}/site-packages" -name "*.dist-info" -type d -prune -exec rm -rf {} + || true
rm -f "${DEST_STDLIB}/site-packages/PyQt5/Qt5/plugins/imageformats/libqpdf.so" || true

# libmpv, resolved near the top of this script under its real soname.
cp -L "$(readlink -f "${LIBMPV_PATH}")" "AppDir/usr/lib/${LIBMPV_SONAME}"
# SDL3, beside it (task 185): Fedora's libSDL2 is sdl2-compat, which loads
# libSDL3.so.0 when libmpv loads and quits the app with "Failed loading
# SDL3 library." on a system without it. bundle_deps.py brings its
# libraries.
for candidate in /usr/lib64/libSDL3.so.0 /usr/lib/libSDL3.so.0; do
    if [ -e "${candidate}" ]; then
        cp -L "${candidate}" AppDir/usr/lib/libSDL3.so.0
        break
    fi
done
[ -e AppDir/usr/lib/libSDL3.so.0 ] || echo "!! SDL3 not found; the AppImage needs it on the system"

# Your talk stations (set in a dev build) into src/, then into the app.
echo "==> Talk stations from your dev build"
python3 tools/ship_talk_stations.py
# Your Twitch app's Client ID (set in a dev build's Settings), so the
# builds you share can sign in to Twitch chat (build 82, task 92e).
echo "==> Twitch app Client ID from your dev build"
python3 tools/ship_twitch_app.py
# The token that files thumbs-down reports (build 97, task 133): Issues
# on your reports repository only. Shipped; never printed.
echo "==> Report token from your dev build"
python3 tools/ship_report_token.py
# Your disabled visualizer presets (task 119), so new users start with
# the same list.
echo "==> Disabled visualizer presets from your dev build"
python3 tools/ship_visualizer_disabled.py
# Your artist photos (and talk station photos) are not free to share, so
# no build carries them (build 91, task 117): they go into photo-pack/,
# which you upload to your pack's release. Your bio fixes ship as text.
echo "==> Artist photo pack and text fixes from your dev build"
python3 tools/make_photo_pack.py
cp src/*.py AppDir/usr/share/frequencies/
# The dev marker: devmode.py reads it (task 49). Never in a user build.
if [ "${DEV_BUILD}" = 1 ]; then
    echo "dev build" > AppDir/usr/share/frequencies/dev-build
fi
# The preset pack, which was fetched and then left behind.
#
# projectm-presets/ was being built next to the script and never copied
# in, so every AppImage shipped a visualiser with nothing to show and
# said so.
if [ -d projectm-presets ]; then
    mkdir -p AppDir/usr/share/frequencies/presets
    cp projectm-presets/*.milk AppDir/usr/share/frequencies/presets/ \
        2>/dev/null || true
    echo "    copied $(ls AppDir/usr/share/frequencies/presets/*.milk \
        2>/dev/null | wc -l) presets into the AppDir"
fi
# Glyph overrides live beside the code that looks for them.
#
# src/icons/ holds images that replace drawn glyphs — icon() prefers
# them. Copying only *.py left them behind, so the app fell back to the
# drawn version and every fix to the file went nowhere.
# The bundled skin, so the Winamp button has something to open.
if [ -d src/skins ]; then
    mkdir -p AppDir/usr/share/frequencies/skins
    cp src/skins/*.wsz AppDir/usr/share/frequencies/skins/ 2>/dev/null || true
fi
if [ -d src/icons ]; then
    mkdir -p AppDir/usr/share/frequencies/icons
    cp src/icons/* AppDir/usr/share/frequencies/icons/ 2>/dev/null || true
fi
# The shipped artist corrections travel with the code that reads them.
# Only .py was copied, so the overrides file was simply absent from
# every build and the corrections never reached anyone.
cp src/*.json AppDir/usr/share/frequencies/ 2>/dev/null || true
# Clippy's sprite sheet and animations (build 107, task 116), with their
# license and credits.
mkdir -p AppDir/usr/share/frequencies/companion
cp src/companion/* AppDir/usr/share/frequencies/companion/
# Winamp's llama, played once the first time the Winamp view opens
# (build 114, task 154).
mkdir -p AppDir/usr/share/frequencies/sounds
cp src/sounds/* AppDir/usr/share/frequencies/sounds/ 2>/dev/null || true
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
# The build machine's glibc travels with the app (task 185, bundle_deps.py
# puts it in usr/lib/glibc), and AppRun starts Python through its loader.
# Programs the app starts again by their path go through it as well:
# glibc-run, installed under their names, hands them to that loader.
cp glibc-run AppDir/usr/bin/python3
chmod +x AppDir/usr/bin/python3
if [ -n "$(find "${DEST_STDLIB}/site-packages/PyQt5/Qt5/libexec" -name QtWebEngineProcess 2>/dev/null || true)" ]; then
    cp glibc-run AppDir/usr/bin/QtWebEngineProcess
    chmod +x AppDir/usr/bin/QtWebEngineProcess
fi

# The bundled GnuTLS takes its TLS settings from Fedora's crypto-policies
# file, which other distros don't have; without it every https stream
# failed with "No or insufficient priorities were set" (Ubuntu, task 164).
# A copy of this machine's file goes in for AppRun to use there.
mkdir -p AppDir/usr/share/frequencies/tls
if [ -f /etc/crypto-policies/back-ends/gnutls.config ]; then
    cp /etc/crypto-policies/back-ends/gnutls.config AppDir/usr/share/frequencies/tls/gnutls.config
else
    printf '[priorities]\nSYSTEM=NORMAL\n' > AppDir/usr/share/frequencies/tls/gnutls.config
fi
cp frequencies.png AppDir/
cp frequencies.png AppDir/usr/share/icons/hicolor/256x256/apps/

# The AppImage distribution guide (task 195): the desktop file must be
# valid, AppStream metadata goes in usr/share/metainfo (appimagetool checks
# it below), and the licenses travel with the app in usr/share/doc.
echo "==> Desktop file and AppStream metadata"
desktop-file-validate frequencies.desktop
# Also copies the desktop file in, under the AppStream id.
python3 tools/make_metainfo.py AppDir
mkdir -p AppDir/usr/share/doc/frequencies
cp LICENSE THIRD-PARTY.md SOURCES.txt AppDir/usr/share/doc/frequencies/

# --------------------------------------------------------------------
# projectM goes in BEFORE the dependency scan.
#
# It was being copied in afterwards, which meant the scanner never saw
# it: anything libprojectM needs that nothing else already pulled in
# would have been missing at runtime, and the "Verification OK" line
# was not actually covering it. Only the main library - the playlist
# one is a separate API this app does not use.
if [ -d projectm-build ]; then
    find projectm-build -name 'libprojectM-4.so*' \
         ! -name '*playlist*' \
         -exec cp -a {} "AppDir/usr/lib/" \; 2>/dev/null || true
fi
if [ -d projectm-presets ]; then
    find projectm-presets -name '*.milk' \
         -exec cp {} "AppDir/usr/share/projectM/presets/" \; 2>/dev/null || true
fi

echo "==> Bundling shared library dependency closure"
if [ ! -f excludelist ]; then
    curl -fsSL -o excludelist "https://raw.githubusercontent.com/AppImage/pkg2appimage/master/excludelist"
fi
python3 bundle_deps.py

# --------------------------------------------------------------------
# The license of every bundled library and Python package, made last so it
# sees the finished AppDir (it asks rpm on this machine who owns each one).
echo "==> Licenses of the bundled parts"
python3 tools/gather_licenses.py AppDir --site-packages "${VENV_SP}" \
    --projectm-src projectm-src --projectm-version "${PROJECTM_VERSION#v}" \
    || echo "    (could not list the licenses; the AppImage still builds)"

echo "==> Downloading appimagetool"
TOOL="appimagetool-${APP_ARCH}"
if [ ! -f "${TOOL}" ]; then
    curl -fsSL -o "${TOOL}" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${APP_ARCH}.AppImage"
    chmod +x "${TOOL}"
fi

# appimagetool is itself an AppImage, so it needs FUSE. Where that is
# missing - containers, minimal arm64 images - extract and run it
# directly rather than failing with a confusing mount error.
if ! ./"${TOOL}" --version >/dev/null 2>&1; then
    echo "    (FUSE unavailable; extracting appimagetool)"
    ./"${TOOL}" --appimage-extract >/dev/null 2>&1 || true
    if [ -x squashfs-root/AppRun ]; then
        TOOL_CMD="squashfs-root/AppRun"
    else
        echo "!! Could not run appimagetool. Install fuse2, or run" >&2
        echo "   with --appimage-extract-and-run." >&2
        exit 1
    fi
else
    TOOL_CMD="./${TOOL}"
fi

echo "==> Building AppImage"
ARCH="${APP_ARCH}" "${TOOL_CMD}" AppDir "${OUTPUT}"

# A note beside it for "Publish this build as an update" (build 125):
# build number, checksum, and whether your dev picks shipped.
if [ "${DEV_BUILD}" = 1 ]; then
    python3 tools/build_info.py "${OUTPUT}" --dev
else
    python3 tools/build_info.py "${OUTPUT}"
fi

echo "==> Done: ./${OUTPUT}"
if [ -f photo-pack/photo-pack.json ]; then
    echo "    Photo pack: ./photo-pack/ (upload both files when the line"
    echo "    above under 'Artist photo pack' says NEW)"
fi
