# Frequencies for Pebble

The Frequencies watchapp (build 190, task 234): shows what the Frequencies Android app
plays and runs it from the watch. Made for the Pebble Time 2 (emery: 200×228, touch,
microphone); it also builds for basalt, chalk, diorite, flint and gabbro. The original
Pebble (aplite) is too small for it.

It talks only to the Frequencies Android app, through PebbleKit Android 2 (AppMessage).
There is no PebbleKit JS: a JS part (Clay included) would take the messages, so the watch's
settings live in the phone app (Settings › Appearance › Pebble) and on the watch's own Settings screen.

- `src/c/freq.h` – the shared declarations and the protocol numbers.
  Keep them in step with `core/.../Pebble.kt` and `notes/pebble-protocol.md`.
- `src/c/comm.c` – messages, the row/tag/picture decoding.
- `src/c/buttons.c` – one press/hold engine (0.7 s hold ring, Up+Down together; a hold let go after 0.2 s does nothing) and the swipe from the left edge that goes back.
- `src/c/draw.c` – the theme, the vector icons, headers, edge icons, pills, pictures.
- `src/c/win_*.c` – one file per screen.
- `src/c/pull.c` – pulling the bio (down) or the lyrics (up) back to Now Playing with the thumb.
- `tools/fake_phone.py` – a fake phone for the emulator (canned data, scenes over 127.0.0.1:7777).

## Build and try it in the emulator

    pebble build
    pebble install --emulator emery --vnc build/frequencies-pebble.pbw
    ~/.local/share/uv/tools/pebble-tool/bin/python tools/fake_phone.py   # then start the app again
    pebble emu-button --emulator emery --vnc click select
    pebble screenshot --emulator emery --vnc --no-open shot.png

Pass `--vnc` to every command, or pebble-tool restarts the emulator each time.
