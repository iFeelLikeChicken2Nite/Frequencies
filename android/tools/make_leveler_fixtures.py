#!/usr/bin/python3
"""Make LevelerTest's fixture with ffmpeg's own dynaudnorm, the leveler the
desktop runs through mpv (player.build_filter_chain).

    python3 tools/make_leveler_fixtures.py

Needs numpy and ffmpeg. The signal is the one LevelerTest.signal() makes:
8 kHz stereo (right = half the left), 3 s of a quiet 440 Hz tone (0.1),
3 s of a loud 300 Hz one (0.8), 1 s of silence, 3 s of a very quiet
1 kHz tone (0.05). It goes through dynaudnorm=f=200:g=15:p=0.95 and the
loudest sample of every 50 ms of what comes out is saved.
"""

import json
import pathlib
import subprocess
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "core/src/test/resources/leveler/ffmpeg-peaks.json"
RATE = 8000


def signal():
    t = np.arange(10 * RATE) / RATE
    left = np.where(t < 3, 0.1 * np.sin(2 * np.pi * 440 * t),
           np.where(t < 6, 0.8 * np.sin(2 * np.pi * 300 * t),
           np.where(t < 7, 0.0, 0.05 * np.sin(2 * np.pi * 1000 * t))))
    return np.stack([left, 0.5 * left], axis=1).astype(np.float32)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = pathlib.Path(tmp, "in.f32"), pathlib.Path(tmp, "out.f32")
        signal().tofile(src)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "2",
                        "-i", str(src), "-af", "dynaudnorm=f=200:g=15:p=0.95",
                        "-f", "f32le", "-ar", str(RATE), "-ac", "2", str(dst)], check=True)
        out = np.fromfile(dst, dtype=np.float32)
    step = RATE // 20 * 2
    peaks = [round(float(np.abs(out[i:i + step]).max()), 6) for i in range(0, out.size, step)]
    version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"made_with": version, "filter": "dynaudnorm=f=200:g=15:p=0.95",
                               "rate": RATE, "window_ms": 50, "peaks": peaks}, indent=0) + "\n")
    print(f"{len(peaks)} peaks -> {OUT}")


if __name__ == "__main__":
    main()
