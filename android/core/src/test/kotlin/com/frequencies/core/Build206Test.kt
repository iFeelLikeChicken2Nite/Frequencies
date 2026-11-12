package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Android build 206, batch 30: tasks 248 (the artist photo doesn't retract
 * by itself over a page with lots of empty space) and 249 (a station that
 * kept "Tuning in…" for ever: every kind of drop counts against the
 * station's reconnects, and only steady play since the LAST drop gives
 * them back). Nothing station-specific.
 */
class Build206Test {
    // -- task 248 --------------------------------------------------------------

    /** A phone upright: about 600 dp between the top bar and the tabs. */
    private val hero = 600f
    private val room = hero - Comfort.Upright.BODY_TOP_COMFORT

    /** A two-line bio (genres, two lines, credits, the thumbs: about 160 dp) keeps the photo big: no retract. */
    @Test fun aShortBioNeverRetractsThePhoto() {
        val shortBio = 160f
        assertFalse(Comfort.bioFills(shortBio, hero))
        assertFalse(Comfort.viewedReady("artist", lyricsFound = true, bioShown = Comfort.bioFills(shortBio, hero)))
    }

    /** Build 198 retracted a page 60% full, leaving 40% of the screen empty under it; now it stays out. */
    @Test fun aPageWithLotsOfBlankSpaceStaysOut() {
        assertFalse(Comfort.bioFills(room * 0.6f, hero))
        assertFalse(Comfort.bioFills(room * 0.75f, hero))
    }

    /** A page that reaches the bottom fade (or scrolls) still retracts, as before. */
    @Test fun aLongBioStillRetracts() {
        assertTrue(Comfort.bioFills(room * (1f - Comfort.Upright.FADE), hero))
        assertTrue(Comfort.bioFills(room * 1.5f, hero))
        assertTrue(Comfort.viewedReady("artist", lyricsFound = false, bioShown = Comfort.bioFills(room * 2f, hero)))
    }

    // -- task 249 --------------------------------------------------------------

    /**
     * The loop: a stream plays a few seconds, its connection breaks, Media3 reconnects by itself, and
     * again and again. Build 198 gave a station its tries back a minute after its sound first started,
     * even with drops since, so it never ran out. Now the minute counts from the last drop: the fourth
     * drop finds no tries left and the next station takes over.
     */
    @Test fun aStreamThatKeepsBreakingRunsOutOfTries() {
        val t = Recovery.Tries()
        val start = 0L
        var now = start
        var drops = 0
        while (t.left("station")) {
            // ~18 s of sound, then a break.
            for (s in 1..18) t.playing("station", now + s * 1000L, playingFrom = start)
            now += 20_000L
            t.use("station", now)
            drops++
            assertTrue("never ends", drops < 10)
        }
        assertEquals(Recovery.WAITS_MS.size, drops)
        assertEquals(Recovery.WAITS_MS.size, t.count)
        // Build 198's rule (a minute from the first sound) would have wiped the count by now.
        assertTrue(now - start >= Recovery.STEADY_MS)
        assertFalse(Recovery.steady(now + 1_000L, start, lastDrop = now))
    }

    /** A drop the station came back from by itself still counts until a minute of steady play after it. */
    @Test fun triesComeBackOnlyAMinuteAfterTheLastDrop() {
        val t = Recovery.Tries()
        t.use("a", 100_000L)
        t.playing("a", 150_000L, playingFrom = 0L)
        assertEquals(1, t.count)
        t.playing("a", 100_000L + Recovery.STEADY_MS - 1, playingFrom = 0L)
        assertEquals(1, t.count)
        t.playing("a", 100_000L + Recovery.STEADY_MS, playingFrom = 0L)
        assertEquals(0, t.count)
        assertNull(t.stationId)
    }

    /** And from when the sound started again, if that was later (the app's own reconnect). */
    @Test fun steadyCountsFromTheLaterOfStartAndDrop() {
        assertFalse(Recovery.steady(100_000L, playingFrom = 50_000L, lastDrop = 10_000L))
        assertTrue(Recovery.steady(110_000L, playingFrom = 50_000L, lastDrop = 10_000L))
        assertFalse(Recovery.steady(110_000L, playingFrom = 0L, lastDrop = 60_000L))
    }

    /** Another station starts with all its tries; forgetting (a tune-in, moving on) clears them. */
    @Test fun eachStationHasItsOwnTries() {
        val t = Recovery.Tries()
        repeat(Recovery.WAITS_MS.size) { t.use("a", 1_000L * it) }
        assertFalse(t.left("a"))
        assertTrue(t.left("b"))
        assertEquals(0, t.count)
        t.use("b", 5_000L)
        t.forget()
        assertTrue(t.left("b"))
        assertEquals(0, t.of("b"))
    }

    /** The app's own waits still follow the tries: 2, 5, 10 s, then move on. */
    @Test fun theWaitsFollowTheTries() {
        val t = Recovery.Tries()
        val waits = mutableListOf<Long>()
        while (true) {
            val wait = Recovery.nextWait(t.of("a"), httpCode = null, wasPlaying = true, networkTrouble = false) ?: break
            waits += wait
            t.use("a", 0L)
        }
        assertEquals(Recovery.WAITS_MS, waits)
    }
}
