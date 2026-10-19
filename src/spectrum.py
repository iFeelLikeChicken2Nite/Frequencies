"""Turning PCM into something to draw in the visualizer panel.

A small FFT in plain Python rather than numpy: numpy is not a dependency
of this app and pulling one in to transform 512 samples thirty times a
second would be a poor trade. A 512-point transform is a few thousand
butterflies - nothing next to the work of drawing the frame.
"""

import cmath

# 512 samples at 44100Hz is about 12ms - short enough to track a beat.
WINDOW = 512


def _fft(values):
    """Iterative radix-2 FFT. len(values) must be a power of two."""
    n = len(values)
    if n <= 1:
        return values

    # Bit-reversal permutation, done in place.
    data = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]

    length = 2
    while length <= n:
        step = -2j * cmath.pi / length
        wl = cmath.exp(step)
        for i in range(0, n, length):
            w = 1 + 0j
            for k in range(i, i + length // 2):
                u = data[k]
                v = data[k + length // 2] * w
                data[k] = u + v
                data[k + length // 2] = u - v
                w *= wl
        length <<= 1
    return data


def to_mono(samples):
    """Interleaved stereo floats -> a mono window of WINDOW samples.

    Short input is padded at the FRONT, not the back. A pipe read often
    returns fewer frames than asked for, and padding the end put silence
    where the newest audio should be - drawn as a flat line running off
    to the right of the waveform, as though the signal stopped partway
    across. Older-than-we-have reads as silence on the left instead,
    which is true.
    """
    if not samples:
        return []
    mono = [(samples[i] + samples[i + 1]) * 0.5
            for i in range(0, len(samples) - 1, 2)]
    if len(mono) >= WINDOW:
        return mono[-WINDOW:]
    return [0.0] * (WINDOW - len(mono)) + mono


def apply_gain(samples, gain):
    """Scale samples, clipping at full scale.

    Clipped rather than left to overflow: a value beyond 1.0 would push
    a bar off the top of the panel and a waveform outside its box, and
    a meter that leaves its own frame is worse than one that saturates.
    """
    if gain == 1.0:
        return samples
    return [max(-1.0, min(1.0, value * gain)) for value in samples]


def bands(samples, count, gain=1.0):
    """`count` bar heights in 0..1, low frequencies first.

    Bands are spaced logarithmically, because pitch is: linear bins put
    almost everything in the first bar and leave the rest flat.
    """
    mono = apply_gain(to_mono(samples), gain)
    if not mono or not any(mono):
        return [0.0] * count

    # A Hann window, so a tone between bins does not smear across them.
    windowed = [
        value * (0.5 - 0.5 * cmath.cos(2 * cmath.pi * i / (WINDOW - 1)).real)
        for i, value in enumerate(mono)
    ]
    spectrum = _fft([complex(v, 0.0) for v in windowed])

    usable = WINDOW // 2
    out = []
    for index in range(count):
        low = int(usable ** (index / count))
        high = max(low + 1, int(usable ** ((index + 1) / count)))
        chunk = spectrum[low:min(high, usable)]
        if not chunk:
            out.append(0.0)
            continue
        peak = max(abs(c) for c in chunk) / (WINDOW * 0.25)
        # Compressed: a linear scale leaves everything but the loudest
        # peak invisible.
        out.append(min(1.0, peak ** 0.5))
    return out


def waveform(samples, count, gain=1.0):
    """`count` points in -1..1 across the window."""
    mono = apply_gain(to_mono(samples), gain)
    if not mono:
        return [0.0] * count
    step = max(1, len(mono) // count)
    points = [mono[i * step] for i in range(count) if i * step < len(mono)]
    return points + [0.0] * (count - len(points))
