package com.frequencies.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Comfort Now Playing's state machine (comfort spec 5b) and its settings (spec 9). */
class ComfortTest {
    private val out = false
    private val comfort = true

    /** A fake clock run over the rules the screen follows: touches, the idle timer, taps, new songs. */
    /** The older tests run with the setting at its largest, 20 s (the delay before build 175). */
    private class Screen(var auto: Boolean = true, var reader: Boolean = false, var idleMs: Long = 20_000L) {
        var now = 0L
        var lastTouch = 0L
        var state = false
        fun touch() { lastTouch = now }
        fun tap() { touch(); state = Comfort.next(state, Comfort.Event.TAP, auto, reader) }
        fun song() { lastTouch = now; state = Comfort.next(state, Comfort.Event.NEW_SONG, auto, reader) }
        fun advance(ms: Long) {
            val end = now + ms
            while (now < end) {
                now = minOf(end, now + 250)
                if (Comfort.timing(state, auto, reader, inFront = true) && Comfort.idleLeft(now, lastTouch, idleMs) == 0L) {
                    state = Comfort.next(state, Comfort.Event.IDLE, auto, reader)
                }
            }
        }
    }

    @Test
    fun idleGoesToComfortAtTwentySeconds() {
        val s = Screen()
        s.advance(19_750)
        assertEquals(out, s.state)
        s.advance(250)
        assertEquals(comfort, s.state)
    }

    @Test
    fun theDefaultIsSevenSeconds() {
        val s = Screen(idleMs = Comfort.idleMs(Settings.from(null)))
        assertEquals(7_000L, s.idleMs)
        s.advance(6_750)
        assertEquals(out, s.state)
        s.advance(250)
        assertEquals(comfort, s.state)
        s.tap()
        s.advance(5_000)
        s.touch()
        s.advance(6_000)
        assertEquals(out, s.state)
        s.advance(1_000)
        assertEquals(comfort, s.state)
    }

    @Test
    fun theDelaySettingIsWholeSecondsFromFiveToTwenty() {
        fun of(v: kotlinx.serialization.json.JsonElement) = Comfort.retractSeconds(Settings.from(JsonObject(mapOf(Comfort.RETRACT_SECONDS to v))))
        assertEquals(7, Comfort.retractSeconds(Settings.from(null)))
        assertEquals(7, Comfort.retractSeconds(null))
        assertEquals(5, of(JsonPrimitive(5)))
        assertEquals(12, of(JsonPrimitive(12)))
        assertEquals(20, of(JsonPrimitive(20)))
        assertEquals(5, of(JsonPrimitive(1)))
        assertEquals(5, of(JsonPrimitive(-3)))
        assertEquals(20, of(JsonPrimitive(90)))
        assertEquals(20, of(JsonPrimitive(1e300)))
        assertEquals(9, of(JsonPrimitive(8.6)))
        assertEquals(7, of(JsonPrimitive("soon")))
        assertEquals(12_000L, Comfort.idleMs(Settings.from(JsonObject(mapOf(Comfort.RETRACT_SECONDS to JsonPrimitive(12))))))
        // Next to the other Comfort settings, and Android only: sync never sends it.
        assertEquals(SettingsTabs.APPEARANCE, SettingsTabs.tabOf(Comfort.RETRACT_SECONDS))
        assertFalse(Comfort.RETRACT_SECONDS in SyncData.SHARED_SETTINGS)
        val s = Screen(idleMs = Comfort.idleMs(Settings.from(JsonObject(mapOf(Comfort.RETRACT_SECONDS to JsonPrimitive(5))))))
        s.advance(4_750)
        assertEquals(out, s.state)
        s.advance(250)
        assertEquals(comfort, s.state)
    }

    @Test
    fun aTouchStartsTheClockAgain() {
        val s = Screen()
        s.advance(15_000)
        s.touch()                              // scrolling the lyrics counts
        s.advance(15_000)
        assertEquals(out, s.state)
        s.advance(5_000)
        assertEquals(comfort, s.state)
    }

    @Test
    fun aTapWorksBothWaysAndWins() {
        val s = Screen()
        s.tap()
        assertEquals(comfort, s.state)
        s.advance(120_000)                     // tapped into Comfort: stays
        assertEquals(comfort, s.state)
        s.tap()
        assertEquals(out, s.state)
        s.advance(19_000)                      // tapped out: the 20 s start again
        assertEquals(out, s.state)
        s.advance(1_000)
        assertEquals(comfort, s.state)
    }

    @Test
    fun aNewSongBringsTheArtworkBack() {
        val s = Screen()
        s.advance(20_000)
        assertEquals(comfort, s.state)
        s.song()
        assertEquals(out, s.state)
        s.advance(20_000)
        assertEquals(comfort, s.state)
    }

    @Test
    fun automaticOffStaysWhereItIs() {
        val s = Screen(auto = false)
        s.advance(60_000)
        assertEquals(out, s.state)
        s.tap()
        s.song()
        assertEquals(comfort, s.state)
        s.tap()
        assertEquals(out, s.state)
    }

    @Test
    fun aScreenReaderStopsTheAutomaticChanges() {
        val s = Screen(reader = true)
        s.advance(60_000)
        assertEquals(out, s.state)
        s.tap()
        assertEquals(comfort, s.state)
        s.song()
        assertEquals(comfort, s.state)
        assertEquals("Show artwork", Comfort.toggleLabel(true))
        assertEquals("Show lyrics fullscreen", Comfort.toggleLabel(false))
    }

    @Test
    fun noClockInTheBackground() {
        assertFalse(Comfort.timing(out, auto = true, screenReader = false, inFront = false))
        assertFalse(Comfort.timing(comfort, auto = true, screenReader = false, inFront = true))
        assertTrue(Comfort.timing(out, auto = true, screenReader = false, inFront = true))
    }

    @Test
    fun settingsAndKeepingTheScreenOn() {
        val fresh = Settings.from(null)
        assertTrue(Comfort.auto(fresh))
        assertFalse(Comfort.keepAwake(fresh))   // off by default (user, 2026-11-04)
        val on = Settings.from(JsonObject(mapOf(Comfort.KEEP_AWAKE to JsonPrimitive(true), Comfort.AUTO to JsonPrimitive(false))))
        assertTrue(Comfort.keepAwake(on))
        assertFalse(Comfort.auto(on))
        assertTrue(Comfort.keepScreenOn(true, comfort = true, playing = true))
        assertFalse(Comfort.keepScreenOn(true, comfort = false, playing = true))
        assertFalse(Comfort.keepScreenOn(true, comfort = true, playing = false))
        assertFalse(Comfort.keepScreenOn(false, comfort = true, playing = true))
        // Both are Appearance settings.
        assertEquals(SettingsTabs.APPEARANCE, SettingsTabs.tabOf(Comfort.AUTO))
        assertEquals(SettingsTabs.APPEARANCE, SettingsTabs.tabOf(Comfort.KEEP_AWAKE))
        // "Custom station frequency" is gone (user, 2026-11-04): no tab shows it.
        assertEquals(null, SettingsTabs.tabOf("local_station_ratio"))
    }

    @Test
    fun theUprightNumbers() {
        // The mock-up's phone: 360 wide, 780 - 24 status - 44 bar - 36 tabs - 76 controls = 600 of body area.
        assertEquals(290f, Comfort.Upright.artOut(360f, 600f))
        // A narrow or short window gives the cover less, never less than the Comfort thumbnail.
        assertEquals(250f, Comfort.Upright.artOut(320f, 600f))
        assertEquals(52f, Comfort.Upright.artOut(780f, 150f))
        assertEquals(0.5f, Comfort.Upright.lerp(0f, 1f, 0.5f))
    }

    @Test
    fun theWindowsSizeChoosesTheLayout() {
        // The spec's check sizes (dp).
        assertEquals(Comfort.Shape.UPRIGHT, Comfort.shape(360f, 780f))
        assertEquals(Comfort.Shape.SIDEWAYS, Comfort.shape(780f, 360f))
        assertEquals(Comfort.Shape.WIDE, Comfort.shape(1024f, 660f))
        assertEquals(Comfort.Shape.UPRIGHT, Comfort.shape(600f, 960f))     // a tablet in portrait, Medium width
        assertEquals(420f, Comfort.uprightCap(600f))
        assertEquals(290f, Comfort.uprightCap(360f))
        assertEquals(Comfort.Shape.WIDE, Comfort.shape(840f, 900f))        // an unfolded foldable
        assertEquals(Comfort.Shape.WIDE, Comfort.shape(1280f, 800f))
        assertEquals(Comfort.Shape.SIDEWAYS, Comfort.shape(915f, 412f))    // a big phone on its side
        assertEquals(Comfort.Shape.UPRIGHT, Comfort.shape(400f, 400f))     // a small square window
        // The sideways cover leaves the right column its room; the wide cover leaves room for the title.
        assertEquals(284f, Comfort.Sideways.artOut(780f, 360f))
        assertEquals(118f, Comfort.Sideways.artComfort(360f))
        assertTrue(Comfort.Sideways.artComfort(300f) < 118f)
        assertEquals(340f, Comfort.Wide.artOut(560f))
        assertEquals(420f, Comfort.Upright.artOut(600f, 900f, Comfort.uprightCap(600f)))
    }
}
