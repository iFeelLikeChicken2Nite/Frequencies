"""probe_shazam.py — why isn't the song being identified?

Walks the identification chain in order and stops at the first step that
fails, so you get one answer rather than silence:

    1. is shazamio_core importable at all?
    2. can mpv capture a few seconds of the stream to a WAV?
    3. does the local fingerprinter produce a signature?
    4. does Shazam's endpoint answer, and does it match anything?

Run it from the folder holding src/:

    python3 src/probe_shazam.py

or against a specific stream:

    python3 src/probe_shazam.py "https://stream.example/live"

Nothing here touches the UI — it is the same code path feeds.py drives,
with the window taken out of the picture.
"""

import locale
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    locale.setlocale(locale.LC_NUMERIC, "C")

    print("1. fingerprinter")
    import shazam
    if not shazam.AVAILABLE:
        print("   NOT AVAILABLE:", shazam.IMPORT_ERROR or "shazamio_core missing")
        print()
        print("   This is the usual answer when running from source: the")
        print("   AppImage bundles shazamio_core, your system Python does not.")
        print("   Fix it with:  pip install --user shazamio-core")
        print("   (it needs Rust to build if there is no wheel for your Python)")
        return 1
    print("   ok — shazamio_core imported")

    import storage
    if len(sys.argv) > 1:
        url = sys.argv[1]
        name = url
    else:
        station = next((s for s in storage.load_stations()
                        if s.get("kind", "stream") == "stream" and s.get("url")),
                       None)
        if station is None:
            print("No plain stream stations found. Pass a URL instead.")
            return 1
        url, name = station["url"], storage.station_display_name(station)
    print(f"   station: {name}")

    print("2. capture")
    import player
    started = time.time()
    try:
        wav = player.capture_stream_to_wav(url, seconds=6)
    except Exception as exc:                        # noqa: BLE001
        print("   FAILED:", type(exc).__name__, exc)
        return 1
    if not wav or not os.path.exists(wav):
        print("   FAILED: no WAV was written")
        return 1
    size = os.path.getsize(wav)
    print(f"   ok — {size} bytes in {time.time() - started:.1f}s")
    if size < 20000:
        print("   ...but that is suspiciously small for six seconds of audio.")

    print("3. signature")
    try:
        signature = shazam._make_signature(wav)
        print(f"   ok — {signature.signature.samples} samples")
    except Exception as exc:                        # noqa: BLE001
        print("   FAILED:", type(exc).__name__, exc)
        return 1

    print("4. lookup")
    try:
        track = shazam.identify(wav)
    except shazam.ShazamError as exc:
        print("   FAILED:", exc)
        return 1
    except Exception as exc:                        # noqa: BLE001
        print("   FAILED:", type(exc).__name__, exc)
        return 1
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass

    if not track:
        print("   no match — the lookup worked, Shazam just didn't recognize")
        print("   this six seconds. Try again mid-song, or on a different")
        print("   station: talk radio and ads never match.")
        return 0

    print("   MATCH")
    for key in ("title", "artist", "cover_url", "apple_music_url",
                "match_offset", "release_date"):
        print(f"     {key:16} {track.get(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
