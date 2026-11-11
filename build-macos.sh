#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ $# -gt 1 ]] || { [[ $# -eq 1 ]] && [[ "$1" != "--setup-only" ]]; }; then
    printf '%s\n' 'Usage: bash build-macos.sh [--setup-only]' >&2
    exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
    printf '%s\n' 'The macOS build must be run on macOS.' >&2
    exit 1
fi

work="$PWD/build-macos"
mkdir -p "$work"

fetch_verified() {
    local url="$1" destination="$2" expected="$3" actual
    if [[ ! -f "$destination" ]]; then
        curl --fail --location --retry 3 --output "$destination.part" "$url"
        actual="$(shasum -a 256 "$destination.part")"
        if [[ "${actual%% *}" != "$expected" ]]; then
            printf 'Checksum mismatch for %s\n' "$destination" >&2
            return 1
        fi
        mv "$destination.part" "$destination"
    fi
    actual="$(shasum -a 256 "$destination")"
    if [[ "${actual%% *}" != "$expected" ]]; then
        printf 'Checksum mismatch for %s\n' "$destination" >&2
        return 1
    fi
}

python="${PYTHON:-}"
if [[ -z "$python" ]]; then
    host_arch="$(uname -m)"
    case "$host_arch" in
        arm64) python_arch="aarch64"; checksum='fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e' ;;
        x86_64) python_arch="x86_64"; checksum='a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3' ;;
        *) printf 'Unsupported Mac architecture: %s\n' "$host_arch" >&2; exit 1 ;;
    esac
    python_dir="$work/python-3.12.16-$host_arch"
    archive="$work/cpython-3.12.16-$host_arch.tar.gz"
    printf '%s\n' '==> Fetching standalone Python'
    fetch_verified "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-$python_arch-apple-darwin-install_only.tar.gz" \
        "$archive" "$checksum"
    if [[ ! -x "$python_dir/python/bin/python3" ]]; then
        mkdir -p "$python_dir"
        tar -xzf "$archive" -C "$python_dir"
    fi
    python="$python_dir/python/bin/python3"
fi
if ! command -v "$python" >/dev/null 2>&1; then
    printf 'Python not found: %s\n' "$python" >&2
    exit 1
fi
if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    printf '%s\n' 'The macOS build requires Python 3.12 or newer.' >&2
    exit 1
fi

python_tag="$("$python" -c 'import platform, sys; print(platform.machine() + "-" + ".".join(map(str, sys.version_info[:3])))')"
if [[ -n "${PYTHON:-}" ]]; then
    python_path="$("$python" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
    python_hash="$(printf '%s' "$python_path" | shasum -a 256)"
    python_tag="$python_tag-${python_hash:0:8}"
fi
venv="$work/venv-$python_tag"
if [[ ! -x "$venv/bin/python" ]]; then
    "$python" -m venv "$venv"
fi
python="$venv/bin/python"
printf '%s\n' "$python" > "$work/build-python"

printf '%s\n' '==> Installing bundled Qt dependencies'
"$python" -m pip install --only-binary=:all: \
    'PyQt5==5.15.11' 'PyQt5-Qt5==5.15.19' 'PyQt5-sip==12.20.0' \
    'PyQtWebEngine==5.15.7' 'PyQtWebEngine-Qt5==5.15.19'
"$python" -c 'from PyQt5 import QtCore, QtWidgets, QtWebEngineWidgets; print("Qt", QtCore.QT_VERSION_STR)'

printf '%s\n' '==> Installing application and packaging dependencies'
"$python" -m pip install --only-binary=:all: \
    'pyinstaller==6.16.0' 'python-mpv==1.0.8' 'requests==2.34.2' \
    'segno==1.6.6' 'zxing-cpp==3.1.1' 'yt-dlp==2026.10.26' 'zstandard==0.25.0'
if ! "$python" -m pip install --only-binary=:all: 'shazamio-core==2.0.0'; then
    printf '%s\n' 'Shazam is unavailable: no compatible shazamio-core wheel was found.'
fi

if [[ "${1:-}" == "--setup-only" ]]; then
    exit 0
fi

arch="$("$python" -c 'import platform; print(platform.machine())')"
case "$arch" in
    arm64|x86_64) ;;
    *) printf 'Unsupported Python architecture: %s\n' "$arch" >&2; exit 1 ;;
esac
libmpv="${MACOS_LIBMPV:-}"
unset MACOS_LIBMPV_DIR
if [[ -z "$libmpv" ]]; then
    version="26.10.1"
    case "$arch" in
        arm64) package_arch="arm64"; checksum='37e93dea19e511178b7b6241f275ddefb3f3aea789351c14c4a58ac060e120a3' ;;
        x86_64) package_arch="x64"; checksum='2732efe189419103d60c36e2e2e2252d4eed0a2124e1c6bc49faa285afa1e23c' ;;
    esac
    archive="$work/mpv-$version-$package_arch.zip"
    libdir="$work/mpv-$version-$package_arch/lib"

    printf '%s\n' '==> Fetching bundled libmpv'
    fetch_verified "https://github.com/vegidio/binaries-mpv/releases/download/$version/dynamic_osx_$package_arch.zip" \
        "$archive" "$checksum"
    unzip -o -q "$archive" 'lib/*' -d "$work/mpv-$version-$package_arch"
    libmpv="$libdir/libmpv.dylib"
    export MACOS_LIBMPV_DIR="$libdir"
    export DYLD_LIBRARY_PATH="$libdir${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
fi
if [[ ! -f "$libmpv" ]]; then
    printf 'libmpv.dylib not found: %s\n' "$libmpv" >&2
    exit 1
fi
case "$(basename "$libmpv")" in
    libmpv*.dylib) ;;
    *) printf '%s\n' 'The libmpv filename must start with libmpv and end in .dylib.' >&2; exit 1 ;;
esac
export MACOS_LIBMPV="$libmpv"

stage="$(mktemp -d "$work/staging.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
ditto "$PWD/src" "$stage/src"
ditto "$PWD/tools" "$stage/tools"
export FREQUENCIES_SOURCE_DIR="$stage/src"
if [[ -z "${FREQUENCIES_DEV_CONFIG:-}" ]]; then
    FREQUENCIES_DEV_CONFIG="$("$python" -c 'import os, sys; sys.path.insert(0, sys.argv[1]); import appdirs; print(os.path.join(appdirs.config_base(), "frequencies"))' "$PWD/src")"
    export FREQUENCIES_DEV_CONFIG
fi

printf '%s\n' '==> Staging developer settings'
"$python" "$stage/tools/ship_talk_stations.py"
"$python" "$stage/tools/ship_twitch_app.py"
"$python" "$stage/tools/ship_visualizer_disabled.py"
"$python" "$stage/tools/make_photo_pack.py"

printf '%s\n' '==> Building experimental macOS app'
touch "$work/dev-build"
"$python" -m PyInstaller --noconfirm --clean \
    --workpath "$work/pyinstaller" --distpath "$work/dist" frequencies-macos.spec
printf '%s\n' '==> Checking macOS 11 binary compatibility'
"$python" tools/check_macos_bundle.py "$work/dist/Frequencies.app"
"$python" tools/scan_tokens.py "$work/dist/Frequencies.app"

printf '%s\n' '==> Creating development DMG'
mkdir -p "$stage/dmg"
ditto "$work/dist/Frequencies.app" "$stage/dmg/Frequencies.app"
ln -s /Applications "$stage/dmg/Applications"
dmg="$work/dist/Frequencies-macOS-$arch-dev.dmg"
hdiutil create -quiet -ov -volname Frequencies -srcfolder "$stage/dmg" -format UDZO "$dmg"
"$python" tools/build_info.py "$dmg" --dev
printf 'Built: %s\n' "$work/dist/Frequencies.app"
printf 'Built: %s\n' "$dmg"
printf '%s\n' 'This is an experimental unsigned build; macOS 11 compatibility has not yet been verified.'