package com.frequencies.app.playback

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import android.os.Handler
import android.os.Looper
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import kotlin.random.Random

/**
 * Real radio static (the desktop's own recordings, loudness-matched when
 * they were bundled) while a station tunes in, mixed the desktop's way
 * (player.NoisePlayer): one bed clip, usually a second bed under it and
 * sometimes an event on top, each joined at a random point, and the whole
 * mix re-rolled every 1.6-3.2 seconds (RESHUFFLE_MS) so it sounds like a
 * dial moving rather than a loop. A re-roll is at full level, with no
 * fade between mixes; during a fade-out it matches wherever the fade has
 * got to. The next mix is readied on a second set of players and swapped
 * in once it sounds, so the change never leaves a gap. It never takes
 * audio focus: the station's player owns that. A build without the
 * recordings (the public source may leave them out) tunes in silently.
 */
@OptIn(UnstableApi::class)
class StaticPlayer(private val context: Context) {
    /** Only the clips this build carries. */
    private val present: Set<String> = runCatching { context.assets.list("static")?.toSet() }.getOrNull().orEmpty()
    private val beds = listOf("hiss", "dial", "tuning", "search").filter { "$it.ogg" in present }
    private val events = listOf("flip", "sweep1", "sweep2").filter { "$it.ogg" in present }
    private val lengths = mapOf(
        "hiss" to 7, "flip" to 14, "sweep2" to 28, "sweep1" to 43,
        "search" to 72, "tuning" to 165, "dial" to 207,
    )
    private val handler = Handler(Looper.getMainLooper())

    /** Two sets of up to three layers: the one heard, and the next mix getting ready. */
    private class Bank {
        val layers = ArrayList<ExoPlayer>()
        /** Each layer's own gain stage, for the car's extra loudness (a player's volume stops at 1). */
        val gains = ArrayList<EqProcessor>()
        /** Each layer's share of the mix, as the desktop's amix weights and make-up gain set it. */
        val weights = FloatArray(3)
        var used = 0
    }
    private val banks = arrayOf(Bank(), Bank())
    private var heard = 0
    private var level = 0f
    /** Between start and stop: re-rolls keep coming. */
    private var active = false
    private var fade: Runnable? = null
    private var swap: Runnable? = null

    /**
     * Extra loudness in dB on top of the static's level: Android Auto
     * gets a little more (user, batch 24: "slightly too quiet in android
     * auto specifically"); the phone's own speaker and headphones stay at 0.
     */
    @Volatile var boostDb: Double = 0.0
        set(value) {
            field = value
            banks.forEach { b -> b.gains.forEach { it.set(emptyList(), value) } }
        }

    val playing: Boolean get() = active || banks.any { b -> (0 until b.used).any { b.layers[it].isPlaying || b.layers[it].playWhenReady } }

    /** Start the static at [volume] (0-1), with a new mix. */
    fun start(volume: Float) {
        cancelFade()
        cancelSwap()
        handler.removeCallbacks(reroll)
        stopBank(banks[1 - heard])
        if (beds.isEmpty()) { stopBank(banks[heard]); active = false; return }
        level = volume
        active = true
        stopBank(banks[heard])
        mix(banks[heard], audible = true)
        scheduleReroll()
    }

    /** Fade out over [ms], then stop. */
    fun fadeOut(ms: Int) {
        cancelFade()
        if (ms <= 0 || banks[heard].used == 0) { stop(); return }
        val from = level
        val started = System.currentTimeMillis()
        val step = object : Runnable {
            override fun run() {
                val t = ((System.currentTimeMillis() - started).toFloat() / ms).coerceIn(0f, 1f)
                level = from * (1f - t)
                setVolumes(banks[heard])
                if (t >= 1f) stop() else handler.postDelayed(this, 30)
            }
        }
        fade = step
        handler.post(step)
    }

    fun stop() {
        active = false
        handler.removeCallbacks(reroll)
        cancelFade()
        cancelSwap()
        banks.forEach(::stopBank)
    }

    fun release() {
        stop()
        banks.forEach { b -> b.layers.forEach { it.release() }; b.layers.clear(); b.gains.clear() }
    }

    // -- the re-roll (player.NoisePlayer._reshuffle) ------------------------------

    private val reroll = Runnable {
        if (!active) return@Runnable
        val next = banks[1 - heard]
        cancelSwap()
        stopBank(next)
        mix(next, audible = false)
        // Swapped in once every layer sounds (a local file: a few tens of ms), or after 400 ms whatever.
        val started = System.currentTimeMillis()
        val check = object : Runnable {
            override fun run() {
                if (!active) return
                val ready = (0 until next.used).all { next.layers[it].isPlaying }
                if (!ready && System.currentTimeMillis() - started < 400) { handler.postDelayed(this, 15); return }
                swap = null
                val old = banks[heard]
                heard = 1 - heard
                setVolumes(next)
                stopBank(old)
                scheduleReroll()
            }
        }
        swap = check
        handler.post(check)
    }

    private fun scheduleReroll() {
        handler.removeCallbacks(reroll)
        if (active) handler.postDelayed(reroll, Random.nextLong(RESHUFFLE_MS.first, RESHUFFLE_MS.last + 1))
    }

    /** A new mix on [bank], at the current level or (getting ready) silent. */
    private fun mix(bank: Bank, audible: Boolean) {
        val chosen = arrayListOf(beds.random())
        if (beds.size > 1 && Random.nextFloat() < 0.75f) chosen.add((beds - chosen.first()).random())
        if (events.isNotEmpty() && Random.nextFloat() < 0.45f) chosen.add(events.random())
        // amix divides by the number of inputs; the desktop brings it back up by 0.28 per extra clip.
        val makeUp = (1f + 0.28f * (chosen.size - 1)) / chosen.size
        bank.used = chosen.size
        chosen.forEachIndexed { i, clip ->
            bank.weights[i] = makeUp * (if (i == 0) 1f else Random.nextDouble(0.35, 0.85).toFloat())
            val p = layer(bank, i)
            p.repeatMode = Player.REPEAT_MODE_ONE
            val length = (lengths[clip] ?: 7) * 1000L
            p.setMediaItem(MediaItem.fromUri("asset:///static/$clip.ogg"), Random.nextLong(0, maxOf(1L, length - 2000)))
            p.prepare()
            p.volume = if (audible) (bank.weights[i] * level).coerceIn(0f, 1f) else 0f
            p.playWhenReady = true
        }
    }

    private fun setVolumes(bank: Bank) {
        for (i in 0 until bank.used) bank.layers[i].volume = (bank.weights[i] * level).coerceIn(0f, 1f)
    }

    private fun stopBank(bank: Bank) {
        for (i in 0 until bank.used) bank.layers[i].run { stop(); clearMediaItems() }
        bank.used = 0
    }

    private fun layer(bank: Bank, i: Int): ExoPlayer {
        while (bank.layers.size <= i) {
            val gain = EqProcessor().apply { set(emptyList(), boostDb) }
            val renderers = object : DefaultRenderersFactory(context) {
                override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioTrackPlaybackParams: Boolean): AudioSink =
                    DefaultAudioSink.Builder(context)
                        .setAudioProcessors(arrayOf(gain))
                        .setEnableFloatOutput(false)
                        .setEnableAudioTrackPlaybackParams(enableAudioTrackPlaybackParams)
                        .build()
            }
            bank.gains += gain
            bank.layers += ExoPlayer.Builder(context, renderers)
                .setAudioAttributes(
                    AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                    false,
                )
                .build()
        }
        return bank.layers[i]
    }

    private fun cancelFade() {
        fade?.let { handler.removeCallbacks(it) }
        fade = null
    }

    private fun cancelSwap() {
        swap?.let { handler.removeCallbacks(it) }
        swap = null
    }

    companion object {
        /** player.NoisePlayer.RESHUFFLE_MS */
        val RESHUFFLE_MS = 1600L..3200L
    }
}
