package com.frequencies.core

import java.util.concurrent.CountDownLatch
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android build 203, batch 28: the health sweep really runs (task 246) and Settings switches never wait for a sync. */
class Build203Test {
    private val min = 60_000L

    // -- when sweeps run ------------------------------------------------------

    @Test fun backOnScreenSweepsOnlyAfterTheGap() {
        val clock = SweepClock()
        assertTrue(clock.claim())               // the launch sweep
        assertEquals(SweepClock.Next.Nothing, clock.foreground(0))   // still waiting or running: nothing, no log line
        clock.done(0, ran = true)
        val soon = clock.foreground(10 * min)
        assertTrue(soon is SweepClock.Next.Skip)
        assertEquals("the last sweep was 10 minutes ago (at most one every 30 minutes)", (soon as SweepClock.Next.Skip).reason)
        val later = clock.foreground(30 * min)
        assertEquals(SweepClock.Next.Sweep("back on screen"), later)
        // Only one at a time: another return while it runs does nothing.
        assertEquals(SweepClock.Next.Nothing, clock.foreground(31 * min))
        clock.done(32 * min, ran = true)
        assertTrue(clock.foreground(40 * min) is SweepClock.Next.Skip)
        assertEquals(SweepClock.Next.Sweep("back on screen"), clock.foreground(62 * min))
    }

    @Test fun aShorterGapForDevTesting() {
        val clock = SweepClock()
        clock.gapMs = 2 * min
        assertTrue(clock.claim()); clock.done(0, ran = true)
        assertEquals("the last sweep was less than a minute ago (at most one every 2 minutes)", (clock.foreground(30_000) as SweepClock.Next.Skip).reason)
        assertEquals(SweepClock.Next.Sweep("back on screen"), clock.foreground(2 * min))
    }

    @Test fun anOfflineStartIsRetriedWhenTheInternetIsBack() {
        val clock = SweepClock()
        // Nothing owed: the internet coming back starts nothing.
        assertEquals(SweepClock.Next.Nothing, clock.online())
        assertTrue(clock.claim())
        clock.done(8_000, ran = false)          // launched offline
        assertTrue(clock.owed)
        assertEquals(SweepClock.Next.Sweep("internet back, retrying"), clock.online())
        assertEquals(SweepClock.Next.Nothing, clock.online())       // one at a time
        clock.done(20_000, ran = true)
        assertFalse(clock.owed)
        assertEquals(SweepClock.Next.Nothing, clock.online())
        assertEquals(SweepClock.Next.Nothing, clock.retry())
    }

    @Test fun anOfflineStartIsRetriedOnTheNextReturnEvenWithinTheGap() {
        val clock = SweepClock()
        assertTrue(clock.claim())
        clock.done(0, ran = false)
        assertEquals(SweepClock.Next.Sweep("back on screen, retrying"), clock.foreground(1 * min))
        clock.done(2 * min, ran = true)
        assertTrue(clock.foreground(3 * min) is SweepClock.Next.Skip)
    }

    @Test fun theTimedRetryOnlyRunsWhenOwed() {
        val clock = SweepClock()
        assertTrue(clock.claim()); clock.done(0, ran = false)
        assertEquals(SweepClock.Next.Sweep("retrying"), clock.retry())
        clock.done(5 * min, ran = true)
        assertEquals(SweepClock.Next.Nothing, clock.retry())
    }

    @Test fun aSweepCutShortAsOfflineIsOwed() {
        // Health.sweep's own offline guess (the first 8 streams all fail) is what makes the app owe one.
        val targets = (1..10).map { "s$it" to "http://x/$it" }
        val summary = Health.sweep(targets, probe = { false to "ConnectionError" }) { _, _, _ -> error("nothing is marked") }
        assertTrue(summary.offline)
        val clock = SweepClock()
        assertTrue(clock.claim())
        clock.done(0, ran = !summary.offline)
        assertTrue(clock.owed)
    }

    // -- the log --------------------------------------------------------------

    @Test fun sweepLinesSayWhatHappened() {
        val started = HealthLog.started(1, "launch", 40, 5)
        assertEquals("started", started.what)
        assertEquals("Started: 40 saved stream(s) to check, 5 skipped", started.reason)
        val done = HealthLog.finished(2, "launch", 40, 37, 5)
        assertEquals("Finished: 40 checked, 37 answered, 3 no answer, 5 skipped", done.reason)
        assertTrue(done.isSweep)
        val skip = HealthLog.skipped(3, "daily", "Re-check frequencies that are down is off.")
        assertEquals("skipped", skip.what)
        assertEquals("Skipped: Re-check frequencies that are down is off.", skip.reason)
        assertEquals("Skipped: the phone is offline. Trying again when the internet is back (or in 5 minutes).", HealthLog.offline(5, "launch").reason)
        assertEquals("Cut short: the first 8 streams all failed, looks offline, nothing marked. Trying again when the internet is back (or in 5 minutes).", HealthLog.looksOffline(6, "launch", 8).reason)
        assertFalse(HealthLog.cutShort(4, "launch", "no network").ok)
    }

    @Test fun repeatedTooSoonLinesCollapse() {
        var log = listOf<HealthLog.Entry>()
        log = HealthLog.add(log, HealthLog.finished(1, "launch", 3, 3, 0))
        log = HealthLog.add(log, HealthLog.skipped(2, "back on screen", "the last sweep was 2 minutes ago (at most one every 30 minutes)"))
        log = HealthLog.add(log, HealthLog.skipped(3, "back on screen", "the last sweep was 5 minutes ago (at most one every 30 minutes)"))
        assertEquals(2, log.size)
        assertEquals(3L, log[0].at)
        // Other skips are never folded away.
        log = HealthLog.add(log, HealthLog.offline(4, "launch"))
        log = HealthLog.add(log, HealthLog.offline(5, "internet back, retrying"))
        assertEquals(4, log.size)
    }

    @Test fun theLogLastsAcrossLaunches() {
        val log = listOf(
            HealthLog.Entry(10, "KEXP", ok = false, reason = "ReadTimeout"),
            HealthLog.finished(9, "launch", 1, 0, 2),
            HealthLog.Entry(8, "The Pulse", ok = true),
        )
        val back = HealthLog.fromJson(FreqJson.parseToJsonElement(encodeJson(HealthLog.toJson(log))))
        assertEquals(log, back)
        assertEquals(emptyList<HealthLog.Entry>(), HealthLog.fromJson(null))
    }

    @Test fun theLogKeepsAtMost500Lines() {
        var log = listOf<HealthLog.Entry>()
        repeat(520) { log = HealthLog.add(log, HealthLog.Entry(it.toLong(), "s$it")) }
        assertEquals(HealthLog.MAX, log.size)
        assertEquals(519L, log.first().at)
    }

    @Test fun sweepLinesOutlastStationLinesWhenFull() {
        var log = listOf<HealthLog.Entry>()
        log = HealthLog.add(log, HealthLog.finished(0, "launch", 3, 3, 0), max = 5)
        repeat(10) { log = HealthLog.add(log, HealthLog.Entry(it + 1L, "s$it"), max = 5) }
        assertEquals(5, log.size)
        assertTrue(log.last().isSweep)
        assertEquals(10L, log.first().at)
    }

    // -- Settings switches during a sync ----------------------------------------

    /**
     * Build 198's audit leftover: Settings switches called Library.updateSettings on the main
     * thread, which waited for the lock a sync merge held. Now the switch shows at once and
     * the change waits for the lock on a thread of its own, in order.
     */
    @Test fun aSwitchNeverWaitsForASyncMerge() {
        val libraryLock = Object()
        val shown = AtomicReference(mapOf<String, Boolean>())
        val saved = CopyOnWriteArrayList<Map<String, Boolean>>()
        val changes = QuickChanges("test-settings")
        val held = CountDownLatch(1)
        val release = CountDownLatch(1)
        val merge = thread(isDaemon = true, name = "sync-merge") {
            synchronized(libraryLock) { held.countDown(); release.await(10, TimeUnit.SECONDS) }
        }
        assertTrue(held.await(5, TimeUnit.SECONDS))
        fun flip(key: String, on: Boolean) = changes.change(
            show = { shown.updateAndGet { it + (key to on) } },
            apply = { synchronized(libraryLock) { saved.add(shown.updateAndGet { it + (key to on) }) } },
        )
        var took = 0L
        val main = thread(name = "main") {
            val t0 = System.nanoTime()
            flip("lyrics_on", false)
            flip("health_recheck", false)
            flip("lyrics_on", true)
            took = (System.nanoTime() - t0) / 1_000_000
        }
        main.join(2_000)
        assertFalse("the switch must not wait for the merge's lock", main.isAlive)
        assertTrue("took $took ms", took < 200)
        assertEquals(mapOf("lyrics_on" to true, "health_recheck" to false), shown.get())
        assertTrue("nothing saved while the merge holds the lock", saved.isEmpty())
        release.countDown()
        merge.join(5_000)
        assertTrue(changes.flush(5_000))
        assertEquals(3, saved.size)
        assertEquals(mapOf("lyrics_on" to true, "health_recheck" to false), saved.last())
    }

    @Test fun aFailedChangeDoesNotStopLaterOnes() {
        val errors = CopyOnWriteArrayList<Throwable>()
        val done = CopyOnWriteArrayList<Int>()
        val changes = QuickChanges("test-settings-2", onError = { errors.add(it) })
        changes.change(show = {}, apply = { error("disk full") })
        changes.change(show = {}, apply = { done.add(2) })
        assertTrue(changes.flush(5_000))
        assertEquals(listOf(2), done.toList())
        assertEquals(1, errors.size)
    }
}
