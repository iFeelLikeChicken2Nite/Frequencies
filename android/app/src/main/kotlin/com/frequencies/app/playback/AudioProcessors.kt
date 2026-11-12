package com.frequencies.app.playback

import android.os.SystemClock
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor.AudioFormat
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import com.frequencies.core.Equalizer
import com.frequencies.core.Leveler
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Listens to what the station plays, for Song ID: the last few seconds,
 * mixed to mono at the decoder's own rate (an HE-AAC stream decodes at
 * twice its header's rate; whatever comes out is what is kept). The sound
 * passes through untouched, before the EQ, so a station's EQ never
 * changes what Shazam hears. No second connection to the station.
 */
@OptIn(UnstableApi::class)
class AudioTap(private val seconds: Int = 12) : BaseAudioProcessor() {
    /** Mono audio and when its last sample was handed on (elapsedRealtime). */
    class Clip(val samples: FloatArray, val rate: Int, val endedAt: Long) {
        val durationMs: Long get() = samples.size * 1000L / rate
    }

    private val lock = Any()
    private var ring = FloatArray(0)
    private var rate = 0
    private var channels = 0
    private var written = 0L
    private var lastAt = 0L

    override fun onConfigure(inputAudioFormat: AudioFormat): AudioFormat {
        if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) return AudioFormat.NOT_SET
        synchronized(lock) {
            rate = inputAudioFormat.sampleRate
            channels = inputAudioFormat.channelCount
            ring = FloatArray(rate * seconds)
            written = 0
        }
        return inputAudioFormat
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val size = inputBuffer.remaining()
        if (size == 0) return
        val shorts = inputBuffer.duplicate().order(ByteOrder.nativeOrder()).asShortBuffer()
        synchronized(lock) {
            val ch = channels
            if (ch > 0 && ring.isNotEmpty()) {
                val frames = shorts.remaining() / ch
                var pos = (written % ring.size).toInt()
                for (f in 0 until frames) {
                    var sum = 0
                    for (c in 0 until ch) sum += shorts.get(f * ch + c)
                    ring[pos] = sum / (32768f * ch)
                    pos++
                    if (pos == ring.size) pos = 0
                }
                written += frames
                lastAt = SystemClock.elapsedRealtime()
            }
        }
        replaceOutputBuffer(size).put(inputBuffer).flip()
    }

    /** A new station: what was heard before is no use. */
    fun forget() = synchronized(lock) { written = 0 }

    // A seek or the next item: the sound before it belongs to something else.
    override fun onFlush() = forget()

    /** How much is buffered, in milliseconds. */
    fun heardMs(): Long = synchronized(lock) { if (rate == 0) 0 else minOf(written, ring.size.toLong()) * 1000 / rate }

    /**
     * The level meter (player.level_db): the station's loudness over the
     * last [ms], in dB (RMS, 0 dB = full scale), before the volume and the
     * EQ, so it reads the stream even while the static covers it. Null
     * when the tap can't hear this stream at all (not 16-bit PCM): then
     * the position alone decides, as on a desktop without a meter.
     * Silence (or nothing new for half a second) reads -120.
     */
    fun levelDb(ms: Long = 200): Double? = synchronized(lock) {
        if (rate == 0 || ring.isEmpty()) return null
        if (written == 0L || SystemClock.elapsedRealtime() - lastAt > 500) return -120.0
        val n = minOf((rate * ms / 1000).toInt(), ring.size, written.toInt().coerceAtLeast(0).let { if (written > Int.MAX_VALUE) ring.size else it })
        if (n <= 0) return -120.0
        val end = (written % ring.size).toInt()
        var sum = 0.0
        for (k in 1..n) {
            val v = ring[(end - k + ring.size) % ring.size]
            sum += v * v
        }
        val rms = kotlin.math.sqrt(sum / n)
        return if (rms <= 1e-6) -120.0 else 20 * kotlin.math.log10(rms)
    }

    /** The last [ms] of sound, or null when there isn't that much yet. */
    fun last(ms: Long): Clip? = synchronized(lock) {
        if (rate == 0) return null
        val n = (rate * ms / 1000).toInt()
        if (n > ring.size || written < n) return null
        val out = FloatArray(n)
        val end = (written % ring.size).toInt()
        val start = (end - n + ring.size) % ring.size
        if (start + n <= ring.size) System.arraycopy(ring, start, out, 0, n)
        else {
            val first = ring.size - start
            System.arraycopy(ring, start, out, 0, first)
            System.arraycopy(ring, 0, out, first, n - first)
        }
        Clip(out, rate, lastAt)
    }
}

/**
 * The station's EQ and level trim (core Equalizer: the desktop's filters),
 * live: a slider moved in Station settings is heard at once. Gain is
 * applied last, as on the desktop.
 */
@OptIn(UnstableApi::class)
class EqProcessor : BaseAudioProcessor() {
    private class Wanted(val bands: List<Equalizer.Band>, val gainDb: Double)

    @Volatile private var wanted = Wanted(emptyList(), 0.0)
    private var applied: Wanted? = null
    private var chain: Equalizer.Chain? = null
    private var floats = FloatArray(0)

    fun set(bands: List<Equalizer.Band>, gainDb: Double) {
        wanted = Wanted(bands, gainDb)
    }

    override fun onConfigure(inputAudioFormat: AudioFormat): AudioFormat {
        if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) return AudioFormat.NOT_SET
        chain = null
        applied = null
        return inputAudioFormat
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val size = inputBuffer.remaining()
        if (size == 0) return
        val want = wanted
        val format = inputAudioFormat
        var c = chain
        if (c == null || applied !== want) {
            if (c == null) c = Equalizer.Chain(want.bands, want.gainDb, format.sampleRate, format.channelCount).also { chain = it }
            else c.update(want.bands, want.gainDb)
            applied = want
        }
        val out = replaceOutputBuffer(size)
        if (c.isFlat) {
            out.put(inputBuffer).flip()
            return
        }
        val input = inputBuffer.order(ByteOrder.nativeOrder())
        val count = size / 2
        if (floats.size < count) floats = FloatArray(count)
        for (i in 0 until count) floats[i] = input.getShort(input.position() + 2 * i) / 32768f
        inputBuffer.position(inputBuffer.limit())
        c.process(floats, count / format.channelCount)
        val shorts = out.order(ByteOrder.nativeOrder())
        for (i in 0 until count) {
            val v = (floats[i] * 32768f).toInt().coerceIn(-32768, 32767)
            shorts.putShort(v.toShort())
        }
        out.flip()
    }

    override fun onFlush() {
        // Filter memory from the last stream would click into the next.
        chain = null
        applied = null
    }
}

/**
 * Settings' "Even out loudness between stations" (task 250): the desktop's
 * dynaudnorm=f=200:g=15:p=0.95 (core Leveler), ahead of the tap, the EQ
 * and the station's trim, as on the desktop. A station's sound comes out
 * 2.8 s after it comes in (the leveler looks ahead, like mpv's), so the
 * static covers a little longer when tuning in. [on] follows the switch
 * and glides, with no gap; [songs] true (a music folder, iBroadcast or
 * YouTube) lets the sound straight through, so each song still ends on
 * time and goes on to the next.
 */
@OptIn(UnstableApi::class)
class LevelProcessor : BaseAudioProcessor() {
    @Volatile var on = true
    @Volatile var songs = false

    private var leveler: Leveler? = null
    private var floats = FloatArray(0)

    /** The gain going out now, in dB (0: none, or not running). */
    fun gainDb(): Double = leveler?.gainDb ?: 0.0

    override fun onConfigure(inputAudioFormat: AudioFormat): AudioFormat {
        if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) return AudioFormat.NOT_SET
        return inputAudioFormat
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val format = inputAudioFormat
        val lv = leveler ?: Leveler(format.sampleRate, format.channelCount).also { leveler = it }
        lv.on = on
        lv.lookAhead = !songs
        val size = inputBuffer.remaining()
        if (!lv.lookAhead && lv.available() == 0) {
            if (size > 0) replaceOutputBuffer(size).put(inputBuffer).flip()
            return
        }
        if (size > 0) {
            val input = inputBuffer.order(ByteOrder.nativeOrder())
            val count = size / 2
            if (floats.size < count) floats = FloatArray(count)
            for (i in 0 until count) floats[i] = input.getShort(input.position() + 2 * i) / 32768f
            inputBuffer.position(inputBuffer.limit())
            lv.push(floats, count)
        }
        emit(lv)
    }

    private fun emit(lv: Leveler) {
        val n = lv.available()
        if (n == 0) return
        if (floats.size < n) floats = FloatArray(n)
        lv.read(floats, n)
        val out = replaceOutputBuffer(n * 2).order(ByteOrder.nativeOrder())
        for (i in 0 until n) out.putShort((floats[i] * 32768f).toInt().coerceIn(-32768, 32767).toShort())
        out.flip()
    }

    // The end of a song or stream: what the look-ahead holds goes out too, on the calls that follow
    // (the last output may not have been taken yet, so it isn't replaced here).
    override fun onQueueEndOfStream() {
        leveler?.drain()
    }

    override fun isEnded(): Boolean = super.isEnded() && (leveler?.available() ?: 0) == 0

    // A new station or a seek: the held sound belongs to what played before. A new format starts afresh.
    override fun onFlush() {
        val format = inputAudioFormat
        val lv = leveler
        if (lv != null && lv.rate == format.sampleRate && lv.channels == format.channelCount) lv.flush() else leveler = null
    }

    override fun onReset() {
        leveler = null
        floats = FloatArray(0)
    }
}
