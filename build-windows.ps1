# Frequencies: the Windows build (WP1, WP2, WP3).
#
# Double-click build-windows.bat (it runs this). Needs Python 3.10 or
# newer from python.org and an internet connection. Makes
# dist\Frequencies\Frequencies.exe and the installer
# dist\Frequencies-Setup.exe. Add -Dev for a dev build
# (dist\Frequencies-Setup-dev.exe: never share that one).
#
# Steps: a venv in build-windows\venv, the app's Python packages,
# libmpv-2.dll (the sound engine, from shinchiro's mpv builds), projectM
# and its presets (tools\fetch_projectm_windows.py), your dev build's
# picks from its Settings (as on Linux: talk stations, Twitch Client ID,
# report token, disabled presets, photo pack address, bio fixes), then
# PyInstaller with frequencies-windows.spec, then Inno Setup with
# frequencies.iss. Windows 10 or 11 (tar.exe, winget).

param([switch]$Dev)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$work = Join-Path $PSScriptRoot "build-windows"
New-Item -ItemType Directory -Force $work | Out-Null

Write-Host "==> Python"
$python = $null
foreach ($try in @("py -3", "python")) {
    try {
        $version = & cmd /c "$try -c ""import sys; print(sys.version_info >= (3, 10))"" 2>nul"
        if ($version -eq "True") { $python = $try; break }
    } catch {}
}
if (-not $python) {
    Write-Host "Python 3.10 or newer isn't installed. Install it with:"
    Write-Host "    winget install -e --id Python.Python.3.12"
    Write-Host "then run build-windows.bat again."
    exit 1
}

$venv = Join-Path $work "venv"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    & cmd /c "$python -m venv ""$venv"""
}
& $py -m pip install --upgrade pip --quiet
Write-Host "==> Python packages"
& $py -m pip install --quiet "PyQt5==5.15.11" "PyQtWebEngine==5.15.7" `
    python-mpv requests segno zxing-cpp yt-dlp shazamio-core pyaudiowpatch `
    zstandard pyinstaller winrt-runtime winrt-Windows.Foundation `
    winrt-Windows.Media winrt-Windows.Media.Playback `
    winrt-Windows.Storage.Streams
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> libmpv-2.dll"
$dll = Join-Path $work "libmpv-2.dll"
if (-not ((Test-Path $dll) -and (Get-Item $dll).Length -gt 0)) {
    $release = Invoke-RestMethod `
        "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
    $asset = $release.assets |
        Where-Object { $_.name -match '^mpv-dev-x86_64-\d{8}-git-.*\.7z$' } |
        Select-Object -First 1
    if (-not $asset) { throw "no mpv-dev-x86_64 download in the latest release" }
    $archive = Join-Path $work $asset.name
    Write-Host "    downloading $($asset.name)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $archive
    # Windows' own tar reads 7z with the BCJ2 filter; py7zr can't.
    & tar.exe -xf $archive -C $work libmpv-2.dll
    Remove-Item $archive
    if (-not ((Test-Path $dll) -and (Get-Item $dll).Length -gt 0)) {
        Remove-Item $dll -ErrorAction SilentlyContinue
        throw "libmpv-2.dll was not in $($asset.name)"
    }
}

Write-Host "==> projectM and presets"
& $py tools\fetch_projectm_windows.py

Write-Host "==> Settings from your dev build"
# The same picks the Linux build ships (WP3): talk stations, Twitch
# Client ID, report token (never printed), disabled visualizer presets,
# and the photo pack and report addresses with your bio fixes.
# Read from a copy: Python from the Microsoft Store sees its own private
# AppData, where a stale frequencies folder can hide the real one.
$devConfig = Join-Path $work "dev-config"
$realConfig = Join-Path $env:APPDATA "frequencies"
if (Test-Path $devConfig) { Remove-Item $devConfig -Recurse -Force }
if (Test-Path $realConfig) {
    Copy-Item $realConfig $devConfig -Recurse
    # The two publishing tokens are never read by a build tool and must
    # never travel with a build (build 125): the photo-publish token and
    # the release token.
    foreach ($secret in @("github_token.json", "release_token.json")) {
        Remove-Item (Join-Path $devConfig $secret) -ErrorAction SilentlyContinue
    }
} else {
    New-Item -ItemType Directory $devConfig | Out-Null
}
$env:FREQUENCIES_DEV_CONFIG = $devConfig
& $py tools\ship_talk_stations.py
& $py tools\ship_twitch_app.py
& $py tools\ship_report_token.py
& $py tools\ship_visualizer_disabled.py
& $py tools\make_photo_pack.py

$marker = Join-Path $work "dev-build"
if ($Dev) { Set-Content $marker "dev build" } elseif (Test-Path $marker) { Remove-Item $marker }

Write-Host "==> PyInstaller"
& $py -m PyInstaller --noconfirm --clean frequencies-windows.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> Installer"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "    installing Inno Setup (makes the installer)"
    & winget install -e --id JRSoftware.InnoSetup --silent `
        --accept-source-agreements --accept-package-agreements | Out-Null
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
$setup = if ($Dev) { "Frequencies-Setup-dev" } else { "Frequencies-Setup" }
# The build number (src\version.py) goes in the installer's version.
$build = [regex]::Match((Get-Content src\version.py -Raw), '(?m)^BUILD\s*=\s*(\d+)').Groups[1].Value
if ($iscc) {
    & $iscc /Q "/DSetupName=$setup" "/DAppBuild=$build" frequencies.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    # A note beside it for "Publish this build as an update" (build 125).
    if ($Dev) {
        & $py tools\build_info.py "dist\$setup.exe" --dev
    } else {
        & $py tools\build_info.py "dist\$setup.exe"
    }
} else {
    Write-Host "    Inno Setup isn't installed, so no installer this time."
    Write-Host "    Install it with: winget install -e --id JRSoftware.InnoSetup"
}

Write-Host ""
Write-Host "Done: dist\Frequencies\Frequencies.exe"
if ($iscc) { Write-Host "      dist\$setup.exe (the installer to share)" }
