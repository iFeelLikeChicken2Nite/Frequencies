package com.frequencies.core

import com.frequencies.core.TalkDuck.Action
import com.frequencies.core.TalkDuck.Change
import com.frequencies.core.TalkDuck.State
import org.junit.Assert.assertEquals
import org.junit.Test

class TalkDuckTest {
    private fun run(vararg steps: Pair<Change, Boolean>): List<Action> {
        var state = State()
        return steps.map { (c, call) -> TalkDuck.on(c, call, state).also { state = it.first }.second }
    }

    @Test
    fun navigationPromptMutesThenRestores() {
        // Maps asks for a ducking focus, then gives it back.
        assertEquals(listOf(Action.MUTE, Action.UNMUTE), run(Change.LOSS_TRANSIENT_CAN_DUCK to false, Change.GAIN to false))
        // The assistant asks for a transient focus: muted too, never paused.
        assertEquals(listOf(Action.MUTE, Action.NONE, Action.UNMUTE),
            run(Change.LOSS_TRANSIENT to false, Change.LOSS_TRANSIENT_CAN_DUCK to false, Change.GAIN to false))
    }

    @Test
    fun callsPauseAndOtherPlayersStop() {
        assertEquals(listOf(Action.PAUSE_FOR_NOW, Action.RESUME), run(Change.LOSS_TRANSIENT to true, Change.GAIN to false))
        // A prompt that turns into a call: from muted to paused, then back playing.
        assertEquals(listOf(Action.MUTE, Action.PAUSE_FOR_NOW, Action.RESUME),
            run(Change.LOSS_TRANSIENT_CAN_DUCK to false, Change.LOSS_TRANSIENT to true, Change.GAIN to false))
        assertEquals(listOf(Action.MUTE, Action.STOP, Action.NONE), run(Change.LOSS_TRANSIENT to false, Change.LOSS to false, Change.GAIN to false))
    }

    @Test
    fun androidFocusCodes() {
        assertEquals(Change.GAIN, TalkDuck.change(1))
        assertEquals(Change.GAIN, TalkDuck.change(3))
        assertEquals(Change.LOSS, TalkDuck.change(-1))
        assertEquals(Change.LOSS_TRANSIENT, TalkDuck.change(-2))
        assertEquals(Change.LOSS_TRANSIENT_CAN_DUCK, TalkDuck.change(-3))
        assertEquals(null, TalkDuck.change(0))
    }
}
