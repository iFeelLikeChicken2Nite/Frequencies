package com.frequencies.app.playback

import android.content.Context
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import com.frequencies.core.TalkDuck

/**
 * Audio focus for talk stations (core TalkDuck): while one plays, the
 * service holds the focus itself, so a navigation prompt or the assistant
 * mutes the stream instead of pausing it. Any other station hands the
 * focus back to ExoPlayer, which pauses and ducks the usual way. Works the
 * same with the phone's screen, the notification and Android Auto, since
 * the car's prompts come through the phone's audio focus too.
 */
@OptIn(UnstableApi::class)
class TalkFocus(
    context: Context,
    private val exo: ExoPlayer,
    private val handler: Handler,
    /** Silence (true) or bring back (false) the station's sound; the stream keeps going. */
    private val mute: (Boolean) -> Unit,
) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private var talk = false
    private var name = ""
    private var holding = false
    private var state = TalkDuck.State()

    private val listener = AudioManager.OnAudioFocusChangeListener { focus ->
        val change = TalkDuck.change(focus) ?: return@OnAudioFocusChangeListener
        val inCall = audio.mode == AudioManager.MODE_IN_CALL || audio.mode == AudioManager.MODE_IN_COMMUNICATION || audio.mode == AudioManager.MODE_RINGTONE
        val (next, action) = TalkDuck.on(change, inCall, state)
        state = next
        if (action != TalkDuck.Action.NONE) Log.i(TAG, TalkDuck.describe(action, name))
        when (action) {
            TalkDuck.Action.MUTE -> mute(true)
            TalkDuck.Action.UNMUTE -> mute(false)
            TalkDuck.Action.PAUSE_FOR_NOW -> { mute(false); exo.pause() }
            TalkDuck.Action.RESUME -> { mute(false); exo.play() }
            TalkDuck.Action.STOP -> { mute(false); holding = false; exo.pause() }
            TalkDuck.Action.NONE -> Unit
        }
    }

    private val request: AudioFocusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
        .setAudioAttributes(
            android.media.AudioAttributes.Builder()
                .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                .setContentType(android.media.AudioAttributes.CONTENT_TYPE_MUSIC)
                .build(),
        )
        // A ducking prompt comes to the listener (to mute), instead of Android lowering the volume by itself.
        .setWillPauseWhenDucked(true)
        .setOnAudioFocusChangeListener(listener, handler)
        .build()

    private val attributes = AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build()

    /** The station changed: a talk station takes the focus over from ExoPlayer, any other gives it back. */
    fun station(isTalk: Boolean, displayName: String) {
        name = displayName
        if (isTalk == talk) return
        talk = isTalk
        if (isTalk) {
            exo.setAudioAttributes(attributes, false)
            if (exo.playWhenReady) take()
            Log.i(TAG, "[talk] '$displayName': spoken prompts mute it instead of pausing it")
        } else {
            release()
            exo.setAudioAttributes(attributes, true)
        }
    }

    /** Play or pause pressed (or the stream stopped). */
    fun playing(playWhenReady: Boolean) {
        if (!talk) return
        if (playWhenReady) take() else if (!state.paused) release()
    }

    private fun take() {
        if (holding) return
        val got = audio.requestAudioFocus(request)
        holding = got == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        if (got == AudioManager.AUDIOFOCUS_REQUEST_FAILED) {
            Log.i(TAG, "[talk] '$name': no sound for now (another app holds it, e.g. a call)")
            exo.pause()
        }
    }

    private fun release() {
        if (state.muted) mute(false)
        state = TalkDuck.State()
        if (!holding) return
        audio.abandonAudioFocusRequest(request)
        holding = false
    }

    fun shutdown() = release()

    companion object { private const val TAG = "Frequencies" }
}
