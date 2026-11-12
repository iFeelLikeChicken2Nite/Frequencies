package com.frequencies.core

import java.util.concurrent.CountDownLatch
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android build 198: batch 26 (savePeers off the caller's thread) and batch 27. */
class Build198Test {
    /**
     * The ANR: Settings' Forget and Frequensync's What to sync called SyncKeeper.savePeers
     * on the main thread, which waited for the lock a sync run held. Now the caller only
     * hands the newest state to a LatestSaver; the write waits for the lock, not the caller.
     */
    @Test fun saveReturnsWhileASyncRunHoldsTheLock() {
        val keeperLock = Object()
        val written = CopyOnWriteArrayList<String>()
        val saver = LatestSaver<String>("test-saver") { v -> synchronized(keeperLock) { written.add(v) } }
        val held = CountDownLatch(1)
        val release = CountDownLatch(1)
        val run = thread(isDaemon = true, name = "sync-run") {
            synchronized(keeperLock) { held.countDown(); release.await(10, TimeUnit.SECONDS) }
        }
        assertTrue(held.await(5, TimeUnit.SECONDS))
        var took = 0L
        val caller = thread(name = "main") {
            val t0 = System.nanoTime()
            saver.save("forgot A")
            saver.save("what to sync B")
            saver.save("what to sync C")
            took = (System.nanoTime() - t0) / 1_000_000
        }
        caller.join(2_000)
        assertTrue("the caller must not wait for the sync run's lock", !caller.isAlive)
        assertTrue("save took $took ms", took < 200)
        assertTrue("nothing written while the run holds the lock", written.isEmpty())
        release.countDown()
        run.join(5_000)
        assertTrue(saver.flush(5_000))
        // The newest value is written last, and nothing older comes after it.
        assertEquals("what to sync C", written.last())
        assertTrue(written.size <= 2)
    }

    @Test fun aFailedWriteDoesNotStopLaterOnes() {
        val written = CopyOnWriteArrayList<Int>()
        val errors = CopyOnWriteArrayList<Throwable>()
        val saver = LatestSaver<Int>("test-saver-2", onError = { errors.add(it) }) { v -> if (v == 1) error("disk full") else written.add(v) }
        saver.save(1)
        assertTrue(saver.flush(5_000))
        saver.save(2)
        assertTrue(saver.flush(5_000))
        assertEquals(listOf(2), written.toList())
        assertEquals(1, errors.size)
    }

    private fun station(vararg pairs: Pair<String, String>) = Station(JsonObject(pairs.associate { (k, v) -> k to JsonPrimitive(v) }))

    /** Batch 27 item 1: the art retracts only once the page being looked at has something to show. */
    @Test fun theArtWaitsForThePageBeingViewed() {
        assertTrue(Comfort.viewedReady("lyrics", lyricsFound = true, bioShown = false))   // no bio doesn't matter on Lyrics
        assertFalse(Comfort.viewedReady("lyrics", lyricsFound = false, bioShown = true))
        assertFalse(Comfort.viewedReady("artist", lyricsFound = true, bioShown = false))  // no bio: stays big
        assertTrue(Comfort.viewedReady("artist", lyricsFound = false, bioShown = true))
    }

    /** Batch 27 item 3: talk radio opens on Artist; the next station that isn't talk gets the user's own choice back. */
    @Test fun talkStationsOpenOnArtist() {
        assertEquals("artist", Comfort.pageFor(talk = true, lastNonTalk = "lyrics"))
        assertEquals("lyrics", Comfort.pageFor(talk = false, lastNonTalk = "lyrics"))
        assertEquals("artist", Comfort.pageFor(talk = false, lastNonTalk = "artist"))
        assertEquals("lyrics", Comfort.pageFor(talk = false, lastNonTalk = "something old"))
    }

    /** Task 243: a short bio leaves the photo big; one that takes most of the room retracts it. */
    @Test fun aShortBioKeepsThePhotoBig() {
        val hero = 500f
        val room = hero - Comfort.Upright.BODY_TOP_COMFORT
        assertFalse(Comfort.bioFills(room * 0.3f, hero))
        assertTrue(Comfort.bioFills(room * Comfort.BIO_FILLS, hero))   // build 206: the bar is BIO_FILLS now
        assertTrue(Comfort.bioFills(room * 2f, hero))
        assertFalse(Comfort.bioFills(100f, 0f))    // not measured yet
    }

    /** Task 241: no favorites from a music folder or iBroadcast; radio (and an unknown preview) can. */
    @Test fun onlyRadioSongsCanBeFavorites() {
        assertTrue(SongHistory.canFavorite(station("url" to "https://x/stream")))
        assertTrue(SongHistory.canFavorite(station("kind" to "youtube", "url" to "https://youtube.com/playlist?list=x")))
        assertTrue(SongHistory.canFavorite(null))
        assertFalse(SongHistory.canFavorite(station("kind" to "local", "local_path" to "/sdcard/Music")))
        assertFalse(SongHistory.canFavorite(station("kind" to "ibroadcast")))
    }

    /** Tasks 239 and 240: reconnect a station that was playing, a few times and further apart; never one a refusal won't change. */
    @Test fun aDroppedStationIsReconnectedAFewTimesThenLeft() {
        assertEquals(listOf(2_000L, 5_000L, 10_000L), (0..2).map { Recovery.nextWait(it, null, wasPlaying = true, networkTrouble = false) })
        assertNull(Recovery.nextWait(3, null, wasPlaying = true, networkTrouble = false))     // then the next station
        assertNull(Recovery.nextWait(0, null, wasPlaying = false, networkTrouble = false))    // never played: move on at once
        assertEquals(2_000L, Recovery.nextWait(0, null, wasPlaying = false, networkTrouble = true))  // the network, not the station
        assertEquals(5_000L, Recovery.nextWait(1, null, wasPlaying = false, networkTrouble = false)) // already reconnecting
        assertNull(Recovery.nextWait(0, 404, wasPlaying = true, networkTrouble = false))
        assertNull(Recovery.nextWait(0, 403, wasPlaying = true, networkTrouble = false))
        assertEquals(2_000L, Recovery.nextWait(0, 503, wasPlaying = true, networkTrouble = false))
        assertEquals(2_000L, Recovery.nextWait(0, 429, wasPlaying = true, networkTrouble = false))
    }

    /** The loop guard: a full lap of stations failing in a row ends in a clean stop. */
    @Test fun movingOnStopsAfterAFullLap() {
        assertTrue(Recovery.keepMovingOn(1, 19))
        assertTrue(Recovery.keepMovingOn(18, 19))
        assertFalse(Recovery.keepMovingOn(19, 19))
        assertFalse(Recovery.keepMovingOn(1, 1))
        assertEquals("No station could be reached. Press Play to try again.", Recovery.stoppedNotice("KNDS", lap = true))
        assertEquals("KNDS couldn't be reached. Press Play to try again.", Recovery.stoppedNotice("KNDS", lap = false))
    }

    /** Task 245: Sync now that finds nobody says so in a short note. */
    @Test fun syncNowSaysWhoIsntRunning() {
        assertEquals("Den PC isn't running Frequencies right now.", SyncRuns.notRunning(listOf("Den PC")))
        assertEquals("Den PC and Laptop aren't running Frequencies right now.", SyncRuns.notRunning(listOf("Den PC", "Laptop")))
    }
}
