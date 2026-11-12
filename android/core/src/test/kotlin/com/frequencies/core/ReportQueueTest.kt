package com.frequencies.core

import java.io.File
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Desktop build 152 (task 215) on the phone: a thumbs down is a snapshot
 * taken at the press, waits in a file, survives a restart and failures,
 * and marks the artist only once the Worker took it.
 */
class ReportQueueTest {
    private lateinit var worker: MockWebServer
    private lateinit var dir: File
    private val start = System.currentTimeMillis() / 1000
    private var clock = start
    private val http = OkHttpClient.Builder().readTimeout(5, TimeUnit.SECONDS).build()
    private val repo = "https://github.com/me/frequencies-reports"

    @Before
    fun start() {
        worker = MockWebServer().apply { start() }
        dir = Files.createTempDirectory("queue").toFile()
    }

    @After
    fun stop() = worker.shutdown()

    private fun queue() = ReportQueue(File(dir, ReportQueue.FILE)) { clock }
    private val service get() = worker.url("").toString()
    private fun send(fields: kotlinx.serialization.json.JsonObject) = ReportPoster(http).send(service, fields)
    private fun answer(code: Int, body: String) = worker.enqueue(MockResponse().setResponseCode(code).setBody(body))
    private val styx = ArtistPanel("Styx", "Styx", bio = "An American rock band.", photo = "https://img.example/styx.jpg", bioSource = "Biography from Wikipedia")

    @Test
    fun snapshotIsWhatWasOnScreenAtThePress() {
        val settings = Settings(LinkedHashMap())
        val snap = ThumbsSnapshot.of(styx, "KXCI")
        // The station and song change right after the press: the snapshot is what goes.
        val next = ArtistPanel("Toto", "Toto", bio = "Another band.")
        assertEquals(ReportQueue.Added.Queued, queue().add(settings, snap, "both", snap.photo, service, repo, "155"))
        val item = queue().items().single()
        val fields = item["fields"] as kotlinx.serialization.json.JsonObject
        assertEquals("Styx", fields["artist"].str())
        assertEquals("KXCI", fields["station"].str())
        assertEquals("An American rock band.", fields["bio"].str())
        assertEquals("https://img.example/styx.jpg", fields["photo"].str())
        assertEquals("android", fields["os"].str())
        assertFalse(fields.toString().contains(next.name))
    }

    @Test
    fun holdThenSendMarksOnlyAfterTheWorkerTookIt() {
        val settings = Settings(LinkedHashMap())
        queue().add(settings, ThumbsSnapshot.of(styx, "KXCI"), "bio", "", service, repo, "155")
        // A second press on the same artist while it waits: nothing new.
        assertEquals(ReportQueue.Added.Already, queue().add(settings, ThumbsSnapshot.of(styx, "Other"), "both", "", service, repo, "155"))
        clock += 5
        assertEquals(ReportQueue.Sent.Held(start + 15), queue().sendNext(settings, service, repo, ::send))
        assertTrue("not marked before it went", ThumbsDown.due(settings, "Styx"))
        clock += 11
        answer(500, "{\"ok\": false, \"error\": \"GitHub is down\"}")
        assertTrue(queue().sendNext(settings, service, repo, ::send) is ReportQueue.Sent.Retry)
        assertEquals(1, queue().items().size)
        assertTrue("a failed send doesn't mark", ThumbsDown.due(settings, "Styx"))
        // Offline: the Worker can't be reached at all; still kept.
        val offline = ReportQueue(File(dir, ReportQueue.FILE)) { clock }.sendNext(settings, "http://127.0.0.1:9", repo) { ReportPoster(http).send("http://127.0.0.1:9", it) }
        assertTrue(offline is ReportQueue.Sent.Retry)
        // The app restarts: a new queue on the same file still has it, and sends it.
        answer(201, "{\"ok\": true, \"url\": \"$repo/issues/12\"}")
        assertEquals(ReportQueue.Sent.Filed("Styx", "$repo/issues/12"), queue().sendNext(settings, service, repo, ::send))
        assertTrue(queue().items().isEmpty())
        assertFalse("marked once filed", ThumbsDown.due(settings, "Styx"))
        assertEquals(ReportQueue.Sent.Empty, queue().sendNext(settings, service, repo, ::send))
        // What the Worker got is the desktop's request.
        worker.takeRequest()
        val sent = worker.takeRequest()
        assertEquals("/report", sent.path)
        assertTrue(Regex("\"what\":\\s*\"bio\"").containsMatchIn(sent.body.readUtf8()))
    }

    @Test
    fun thumbsUpInTheHoldTakesItBack() {
        val settings = Settings(LinkedHashMap())
        queue().add(settings, ThumbsSnapshot.of(styx, "KXCI"), "both", "", service, repo, "155")
        clock += 10
        assertTrue(queue().takeBack("styx"))
        assertTrue(queue().items().isEmpty())
        // After the hold it can't be taken back.
        queue().add(settings, ThumbsSnapshot.of(styx, "KXCI"), "both", "", service, repo, "155")
        clock += 20
        assertFalse(queue().takeBack("Styx"))
    }

    @Test
    fun anotherArtistReleasesTheEarlierHold() {
        val settings = Settings(LinkedHashMap())
        queue().add(settings, ThumbsSnapshot.of(styx, "KXCI"), "both", "", service, repo, "155")
        queue().releaseHolds()
        answer(200, "{\"ok\": true, \"already\": true}")
        assertEquals(ReportQueue.Sent.Filed("Styx", "already reported today"), queue().sendNext(settings, service, repo, ::send))
    }

    @Test
    fun refusedForGoodOpensTheBrowserIssue() {
        val settings = Settings(LinkedHashMap())
        queue().add(settings, ThumbsSnapshot.of(styx, "KXCI"), "photo", "https://img.example/styx.jpg", service, repo, "155")
        clock += 16
        answer(400, "{\"ok\": false, \"error\": \"artist is required\"}")
        val r = queue().sendNext(settings, service, repo, ::send)
        assertTrue(r is ReportQueue.Sent.GaveUp)
        assertTrue((r as ReportQueue.Sent.GaveUp).address.startsWith("$repo/issues/new?title="))
        assertTrue(queue().items().isEmpty())
        assertFalse(ThumbsDown.due(settings, "Styx"))
    }

    @Test
    fun noWorkerAddressAndOldReports() {
        val settings = Settings(LinkedHashMap())
        val browser = queue().add(settings, ThumbsSnapshot.of(styx, "KXCI"), "bio", "", "", repo, "155")
        assertTrue(browser is ReportQueue.Added.Browser)
        assertEquals(ReportQueue.Added.Nowhere, queue().add(Settings(LinkedHashMap()), ThumbsSnapshot.of(styx, ""), "bio", "", "", "", "155"))
        // A week-old report that never went is dropped.
        val fresh = Settings(LinkedHashMap())
        queue().add(fresh, ThumbsSnapshot.of(ArtistPanel("Yes", "Yes", bio = "x"), ""), "bio", "", service, repo, "155")
        clock += 8 * 86_400
        assertEquals(ReportQueue.Sent.Empty, queue().sendNext(fresh, service, repo, ::send))
    }

    @Test
    fun takenBackNoteGoesBackDown() {
        val settings = Settings(LinkedHashMap())
        ThumbsDown.noteWrong(settings, "Styx", "Biography from Wikipedia", "bio")
        ThumbsDown.takeBackNote(settings, "Styx")
        assertEquals(null, (settings["artist_reports"] as kotlinx.serialization.json.JsonObject)["styx"])
    }
}
