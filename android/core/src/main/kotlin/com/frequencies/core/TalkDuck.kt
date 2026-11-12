package com.frequencies.core

/**
 * Talk stations and spoken prompts (Android only): while navigation
 * guidance or the voice assistant speaks, a talk station's stream is
 * MUTED, not paused, so a live broadcast keeps going and comes back where
 * the broadcast is, not where it was stopped; the volume comes back when
 * the prompt ends. Music stations keep Android's normal behavior (the
 * player pauses or ducks by itself). A phone call still pauses, like any
 * other app's audio, because a call can last a long time.
 *
 * Pure logic: the playback service feeds it the audio focus changes
 * (AUDIOFOCUS_LOSS_TRANSIENT and _CAN_DUCK are what navigation and the
 * assistant ask for) and does what it answers.
 */
object TalkDuck {
    enum class Change { GAIN, LOSS, LOSS_TRANSIENT, LOSS_TRANSIENT_CAN_DUCK }

    enum class Action {
        NONE,
        /** Silence the stream; it keeps playing. */
        MUTE,
        /** The prompt ended: the volume comes back. */
        UNMUTE,
        /** A call (or another app took the audio for a moment while a call is on): pause, resume on GAIN. */
        PAUSE_FOR_NOW,
        /** Back after [PAUSE_FOR_NOW]. */
        RESUME,
        /** Another app took the audio for good (music elsewhere): stop, as any player does. */
        STOP,
    }

    data class State(val muted: Boolean = false, val paused: Boolean = false)

    /** Android's AudioManager.AUDIOFOCUS_* values, so the app hands them over as they come. */
    fun change(focus: Int): Change? = when (focus) {
        1, 2, 3, 4 -> Change.GAIN          // GAIN, GAIN_TRANSIENT, GAIN_TRANSIENT_MAY_DUCK, GAIN_TRANSIENT_EXCLUSIVE
        -1 -> Change.LOSS
        -2 -> Change.LOSS_TRANSIENT
        -3 -> Change.LOSS_TRANSIENT_CAN_DUCK
        else -> null
    }

    /** What to do with [change], given whether a phone call is on ([inCall]). */
    fun on(change: Change, inCall: Boolean, state: State): Pair<State, Action> = when (change) {
        Change.LOSS_TRANSIENT, Change.LOSS_TRANSIENT_CAN_DUCK ->
            if (inCall) State(muted = false, paused = true) to (if (state.paused) Action.NONE else Action.PAUSE_FOR_NOW)
            else if (state.muted) state to Action.NONE
            else state.copy(muted = true) to Action.MUTE
        Change.GAIN -> when {
            state.paused -> State() to Action.RESUME
            state.muted -> State() to Action.UNMUTE
            else -> State() to Action.NONE
        }
        Change.LOSS -> State() to Action.STOP
    }

    /** Log words for an action. */
    fun describe(action: Action, station: String): String = when (action) {
        Action.MUTE -> "[talk] '$station': muted while another app speaks (navigation or the assistant); the stream keeps going"
        Action.UNMUTE -> "[talk] '$station': the prompt ended, volume back"
        Action.PAUSE_FOR_NOW -> "[talk] '$station': paused for a call"
        Action.RESUME -> "[talk] '$station': the call ended, playing again"
        Action.STOP -> "[talk] '$station': another app took the sound, paused"
        Action.NONE -> ""
    }
}
