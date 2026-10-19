@echo off
rem Frequencies: build the Windows app. Double-click, or run
rem "build-windows.bat dev" for a dev build. See build-windows.ps1.
if /i "%1"=="dev" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-windows.ps1" -Dev
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-windows.ps1"
)
pause
