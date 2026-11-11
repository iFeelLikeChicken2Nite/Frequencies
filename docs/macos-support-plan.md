# macOS 11 and Later Support

## Scope

- Support Intel and Apple silicon Macs with separate native builds for macOS 11 and later.
- The first release must launch, play HTTPS radio streams, manage stations, persist settings, and quit cleanly.
- Defer native media controls, audio-reactive visualization, and automatic update installation. Unavailable features must have clear macOS-specific behavior.
- Distribute unsigned DMGs for both architectures. Developer ID signing and notarization are out of scope; development builds must be clearly identified.
- Test macOS 11 and physical Intel Macs later. Until then, macOS 11 and physical Intel compatibility remain unverified.

## Completed

- [x] Add an experimental macOS build script and PyInstaller spec with a development-build marker and token scan.
- [x] Install pinned PyQt5, PyQtWebEngine, and PyInstaller in an isolated build environment and bundle Qt with the app.
- [x] Download checksum-verified, architecture-specific libmpv archives and bundle libmpv and their dylibs; load the bundled library before importing `mpv`.
- [x] Restrict MPRIS and NetworkManager integration to Linux, and provide macOS fallbacks for unavailable visualizer components.
- [x] Document the experimental build, its prerequisites, and the libmpv source and licensing caveat.
- [x] Add focused tests for platform fallbacks and bundled libmpv lookup; verify generated audio decoding through the bundled library on Apple silicon.
- [x] Build an experimental Apple silicon app and verify its bundled Qt helper starts in a clean environment.
- [x] Use native macOS Library paths, honor XDG overrides, and migrate legacy XDG files without overwriting existing data or deleting originals. Test collisions, repeated migration, legacy names, symlinks, and private file permissions.
- [x] Identify macOS updates by architecture, preserve other platforms in release manifests, and offer HTTPS links for manual installation. Add update and build-metadata tests.
- [x] Stage developer settings without modifying the checkout, set minimum OS and build metadata, and produce an architecture-specific development DMG with a token scan.
- [x] Default to a pinned standalone Python and tested package versions; audit bundled Mach-O deployment targets and reject non-system absolute dylib paths before creating the DMG.
- [x] On the current Apple silicon Mac, start the packaged app, verify the development DMG checksum and contents, and test HTTPS playback and rejection of an expired certificate.
- [x] Build and audit the Intel development app under Rosetta, verify its DMG checksum, run the focused tests, and smoke-test startup.
- [x] Run all 26 focused tests against both the Apple silicon build and the Intel build under Rosetta, including Linux and Windows update-dispatch regression checks.

## Remaining

### Compatibility and Runtime

- [ ] Complete the dependency and asset inventory for the pinned Python, Qt, libmpv, and native extensions. Current Homebrew binaries may require a newer OS.
- [ ] Finish auditing non-Windows branches, Linux desktop helpers, and direct resource paths; verify all application resources.

### Packaging and Release

- [ ] Audit individual third-party licenses, notices, and corresponding source before distribution.
- [ ] Prepare unsigned release rather than development packages for both architectures.
- [ ] Document unsigned installation and the expected Gatekeeper warning, architecture selection, updates, and license requirements.

## Remaining Verification

- [ ] Test the current Apple silicon build using a clean account without Homebrew or developer tools. Check Finder launch, the unsigned Gatekeeper flow, QtWebEngine, station editing, settings persistence, restart, and clean quit.
- [ ] Complete Linux and Windows build and update regression tests beyond the focused dispatch tests already run.
- [ ] Confirm deferred integrations fail gracefully without Linux-specific instructions, and exercise offline and missing-library behavior.

## Later Testing

- [ ] Test both complete apps on macOS 11 with clean accounts and without Homebrew or developer tools. Verify Finder launch, the unsigned Gatekeeper flow, QtWebEngine, TLS and HTTPS playback, resources, station editing, persistence, restart, and clean quit.
- [ ] Test the Intel build on physical Intel hardware, including the same core workflows and unsigned installation flow. The current Intel testing was performed under Rosetta.