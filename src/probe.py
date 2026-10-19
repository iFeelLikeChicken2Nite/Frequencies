"""probe.py — is it the player, or is it my wiring?

Plays one station using nothing but `player.Player` and a bare Qt event
loop: no Shell, no Library, no Playback. It makes the exact same call
`Playback.play()` makes, then prints what mpv is doing every half second.

    cd ~/Desktop/new\\ buildz/x86linux/309b
    python3 src/probe.py

or with a specific station:

    python3 src/probe.py "http://icecast.radiofrance.fr/fipelectro-hifi.aac"

Read the output like this:

  state=playing, pos climbing, vol climbing to N  -> the player is fine and
      the bug is in my wiring; say so and I'll look there
  state=playing, pos climbing, vol stuck at 0     -> the fade-in never ran,
      or _base_volume is 0
  pos stays None                                  -> no audio ever arrived;
      the static was covering a stream that never started
  state=no-audio                                  -> mpv couldn't open the
      sound device, most likely because the static already holds it
  state=error                                     -> the message says why
"""

import locale
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QTimer                       # noqa: E402
from PyQt5.QtWidgets import QApplication              # noqa: E402

DEFAULT_SECONDS = 20


def main():
    locale.setlocale(locale.LC_NUMERIC, "C")
    app = QApplication(sys.argv)
    locale.setlocale(locale.LC_NUMERIC, "C")

    import player
    import storage

    settings = storage.load_settings()
    stations = storage.load_stations()

    if len(sys.argv) > 1:
        url, name = sys.argv[1], sys.argv[1]
        station = {}
    else:
        station = next((s for s in stations
                        if s.get("kind", "stream") == "stream"
                        and s.get("url")), None)
        if station is None:
            print("No plain stream stations found.")
            return 1
        url = station["url"]
        name = storage.station_display_name(station)

    print(f"station : {name}")
    print(f"url     : {url}")
    print(f"volume  : settings say {settings.get('volume', 80)}")
    print(f"static  : {'on' if settings.get('noise_between_stations', True) else 'off'}")
    print()

    events = []
    engine = player.Player(
        on_state=lambda s, m: events.append(f"state={s} {m}".strip()),
        on_metadata=lambda t: events.append(f"icy={t}"),
        on_connected=lambda u, s: events.append(f"connected after {s:.1f}s"),
        on_stalled=lambda u: events.append("STALLED"),
    )
    engine.set_normalize(settings.get("normalize", True))
    engine.set_noise_enabled(settings.get("noise_between_stations", True))
    engine.set_noise_volume(settings.get("noise_volume", 100))
    engine.set_volume(int(settings.get("volume", 80)))

    engine.set_advanced_equalizer(False)
    engine.set_equalizer(**storage.station_eq(station))

    engine.play(
        url,
        gain_db=float(station.get("gain_db") or 0.0),
        insecure=bool(station.get("insecure")),
        crossfade_ms=settings.get("crossfade_ms", 700),
        radio_quality=bool(station.get("radio_quality")),
    )

    started = time.time()

    def sample():
        engine.tick()                      # the thing the UI has to drive
        elapsed = time.time() - started
        try:
            pos = engine._mpv.time_pos
        except Exception:
            pos = None
        try:
            vol = engine._mpv.volume
        except Exception:
            vol = "?"
        note = "  ".join(events)
        events.clear()
        print(f"{elapsed:5.1f}s  pos={str(pos)[:6]:>6}  vol={vol}"
              f"  audio_started={engine._audio_started}   {note}")
        if elapsed >= DEFAULT_SECONDS:
            print("\nbase volume mpv was fading toward:", engine._base_volume)
            engine.stop()
            engine.shutdown()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(sample)
    timer.start(500)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
