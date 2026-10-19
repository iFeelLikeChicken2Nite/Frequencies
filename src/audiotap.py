"""Real audio for the visualizer, captured from the output device.

projectM needs the samples that are actually being heard. libmpv offers
no way to hand them back to Python - there is no audio callback in
python-mpv, and an mpv filter graph has nowhere to write to - so this
records the default output's MONITOR instead: the loopback of whatever
the speakers are playing.

That choice has a useful property. The monitor carries silence when
nothing is playing, so the visualizer goes still on its own when the
radio stops, without anything having to tell it. It also means the
visualizer reacts to what you hear, including the volume slider and the
equalizer, rather than to the raw stream before any of that.

Capture runs in a subprocess reading raw PCM on stdout. pw-record ships
with PipeWire, parec with PulseAudio, and Fedora's pipewire-pulse
provides parec too, so one of them is almost always there.
"""

import shutil
import struct
import subprocess
import threading

RATE = 44100
CHANNELS = 2
# 1024 frames is ~23ms: small enough that the visuals track the beat,
# large enough that we are not waking up thousands of times a second.
CHUNK_FRAMES = 1024
BYTES_PER_SAMPLE = 2                       # signed 16-bit
CHUNK_BYTES = CHUNK_FRAMES * CHANNELS * BYTES_PER_SAMPLE

# Scale for converting signed 16-bit to the -1..1 floats projectM wants.
_INT16_SCALE = 1.0 / 32768.0


def _pipewire_command(source=None):
    return [
        "pw-record",
        # "0" is the default sink's monitor.
        "--target", str(source) if source else "0",
        "--rate", str(RATE),
        "--channels", str(CHANNELS),
        "--format", "s16",
        "-",
    ]


def _pulse_command(source=None):
    command = [
        "parec",
        "--format=s16le",
        f"--rate={RATE}",
        f"--channels={CHANNELS}",
        "--latency-msec=30",
    ]
    if source:
        command.append(f"--device={source}")
    return command


def capture_command(source=None):
    """The command to run, or None if neither tool is installed."""
    if shutil.which("parec"):
        # Preferred when a source is named: parec takes device names,
        # which are stable and readable, where pw-record wants numeric
        # node ids that change between reboots.
        return _pulse_command(source)
    if shutil.which("pw-record"):
        return _pipewire_command(source)
    return None


def list_sources():
    """[(display name, device name)] of what can be recorded.

    Monitors first: those are the loopbacks of output devices, which is
    what a visualizer wants. A real microphone is offered too, since
    somebody may actually want that, but it should not be the default.
    """
    if not shutil.which("pactl"):
        return []
    try:
        output = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:                      # noqa: BLE001
        return []

    monitors, inputs = [], []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        pretty = name.replace("alsa_output.", "").replace("alsa_input.", "")
        pretty = pretty.replace(".monitor", " (output)").replace("_", " ")
        (monitors if name.endswith(".monitor") else inputs).append(
            (pretty, name)
        )
    return monitors + inputs


def availability():
    """(usable, message) for the visualizer to show."""
    if capture_command() is None:
        return False, (
            "No audio capture tool found, so the visualizer can't hear "
            "anything. Install pipewire-utils (for pw-record) or "
            "pulseaudio-utils (for parec)."
        )
    return True, "Listening to the audio output."


def to_floats(raw):
    """Signed 16-bit little-endian bytes -> a list of floats in -1..1.

    Whole frames only: a short read mid-frame would otherwise swap the
    channels for everything after it.
    """
    usable = len(raw) - (len(raw) % (2 * CHANNELS))
    if usable <= 0:
        return []
    values = struct.unpack(f"<{usable // 2}h", raw[:usable])
    return [v * _INT16_SCALE for v in values]


# Every tap started and not yet stopped, so the log can say how many
# capture processes are running (build 68, tasks 55/56: closed
# visualisers and extra sessions left theirs running for good).
_running = set()
_running_lock = threading.Lock()


def running_count():
    """How many capture processes this app has running right now."""
    with _running_lock:
        return sum(1 for tap in _running
                   if tap._process is not None
                   and tap._process.poll() is None)


class AudioTap:
    """Reads the output monitor and hands chunks to a callback.

    The callback runs on the reader thread, so whatever it does has to
    be safe there - the visualizer only stores the samples and lets the
    GL thread pick them up.
    """

    def __init__(self, on_samples, source=None):
        self._on_samples = on_samples
        self.source = source
        self._process = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        command = capture_command(self.source)
        if command is None:
            return False
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except Exception:                  # noqa: BLE001
            self._process = None
            return False

        self._stop.clear()
        with _running_lock:
            _running.add(self)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def _read_loop(self):
        stream = self._process.stdout
        while not self._stop.is_set():
            try:
                raw = stream.read(CHUNK_BYTES)
            except Exception:              # noqa: BLE001
                break
            if not raw:
                break
            samples = to_floats(raw)
            if samples:
                try:
                    self._on_samples(samples)
                except Exception:          # noqa: BLE001
                    pass

    def stop(self):
        self._stop.set()
        with _running_lock:
            _running.discard(self)
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=1)
            except Exception:              # noqa: BLE001
                try:
                    self._process.kill()
                except Exception:          # noqa: BLE001
                    pass
            self._process = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
