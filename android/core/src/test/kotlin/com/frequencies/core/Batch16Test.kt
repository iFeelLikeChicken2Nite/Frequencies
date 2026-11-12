package com.frequencies.core

import java.io.File
import java.net.Socket
import java.nio.file.Files
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Build 168 (batch 16): the iBroadcast redirect, tag pills, the ad
 * hourglass and sync runs. With -Pfrequencies.desktop=<desktop>/src the
 * tables are also checked against the desktop's own source.
 */
class Batch16Test {
    private val desk = System.getProperty("frequencies.desktop").orEmpty().let { File(it) }.takeIf { it.path.isNotEmpty() && it.isDirectory }

    // ---- iBroadcast: the redirect catcher -------------------------------------------------

    private fun ask(port: Int, path: String): String = Socket("127.0.0.1", port).use { s ->
        s.getOutputStream().write("GET $path HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".toByteArray())
        s.getInputStream().bufferedReader().readText()
    }

    @Test fun aSilentSpareConnectionDoesNotEndTheSignIn() {
        // Build 167 and before: Chrome's preconnect sat silent, the read timed out and the sign-in failed.
        RedirectCatcher(0).use { catcher ->
            val port = catcher.port
            var answer: RedirectCatcher.Answer? = null
            val waiter = Thread { answer = catcher.await(20) }.apply { start() }
            val silent = Socket("127.0.0.1", port)               // a preconnect: says nothing, stays open
            Thread.sleep(6_500)                                   // longer than the old 5 s read timeout
            assertTrue(ask(port, "/favicon.ico").startsWith("HTTP/1.1 404"))
            assertTrue(waiter.isAlive)                            // neither one is the answer
            val page = ask(port, "/callback?code=abc%2B1&state=s+t")
            assertTrue(page.startsWith("HTTP/1.1 200"))
            assertTrue("Signed in." in page)
            waiter.join(5_000)
            assertFalse(waiter.isAlive)
            assertEquals(RedirectCatcher.Answer("abc+1", "s t", ""), answer)
            assertEquals("/callback (code, state)", catcher.lastSeen)
            silent.close()
        }
    }

    @Test fun aSpareConnectionThatGoesAwayIsNoError() {
        RedirectCatcher(0).use { catcher ->
            var answer: RedirectCatcher.Answer? = null
            val waiter = Thread { answer = catcher.await(10) }.apply { start() }
            Socket("127.0.0.1", catcher.port).close()
            Socket("127.0.0.1", catcher.port).use { it.getOutputStream().write("GET /call".toByteArray()) }
            Thread.sleep(300)
            assertTrue(waiter.isAlive)
            ask(catcher.port, "/callback?error=access_denied")
            waiter.join(5_000)
            assertEquals("access_denied", answer?.error)
        }
    }

    @Test fun nothingComingIsNullAndClosingEndsTheWait() {
        RedirectCatcher(0).use { assertNull(it.await(1)) }
        val catcher = RedirectCatcher(0)
        var finished = false
        val waiter = Thread { catcher.await(30); finished = true }.apply { start() }
        Thread.sleep(200)
        catcher.close()                                           // Cancel
        waiter.join(3_000)
        assertTrue(finished)
    }

    @Test fun theRedirectQueryIsDecodedAsABrowserSendsIt() {
        assertEquals(mapOf("code" to "a b", "state" to "x/y"), RedirectCatcher.query("/callback?code=a+b&state=x%2Fy#frag"))
        assertEquals(emptyMap<String, String>(), RedirectCatcher.query("/callback"))
        assertEquals(mapOf("flag" to ""), RedirectCatcher.query("/callback?flag&"))
    }

    // ---- Tag pills ----------------------------------------------------------------------------

    private val desktopPalette = listOf(
        "#d96c6c", "#d9914e", "#d9b54e", "#c9c04a", "#a3c94a", "#6fbf73", "#4ebf9e", "#4ea3c9",
        "#5a8fd9", "#7b7bd9", "#a86fd9", "#c96fbf", "#d96f9a", "#a67c52", "#6c7a89", "#8a8a8a",
    )

    @Test fun thePaletteIsTheDesktops() {
        assertEquals(desktopPalette, Settings.TAG_PALETTE)
        val src = desk?.let { File(it, "storage.py") }?.takeIf { it.isFile } ?: return
        val block = src.readText().substringAfter("TAG_COLOR_CHOICES = [").substringBefore("]")
        val theirs = Regex("\"(#[0-9a-fA-F]{6})\"").findAll(block).map { it.groupValues[1].lowercase() }.toList()
        assertEquals(theirs, Settings.TAG_PALETTE)
    }

    @Test fun pillsUseTheDesktopsAlphasAndSizes() {
        // stationlist._draw_tags
        assertEquals(TagLook.Style(34, 0, TagLook.Text.TAG, 18, 9, 9, 10), TagLook.ROW)
        // shell._paint_tag: off / include / exclude, and the hover fills
        assertEquals(34 to TagLook.Text.TAG, TagLook.OFF.fillAlpha to TagLook.OFF.text)
        assertEquals(80 to TagLook.Text.THEME, TagLook.INCLUDE.fillAlpha to TagLook.INCLUDE.text)
        assertEquals(18 to TagLook.Text.FAINT, TagLook.EXCLUDE.fillAlpha to TagLook.EXCLUDE.text)
        assertTrue(TagLook.EXCLUDE.struck && !TagLook.INCLUDE.struck && !TagLook.OFF.struck)
        assertEquals(listOf(62, 108, 44), listOf(TagLook.OFF, TagLook.INCLUDE, TagLook.EXCLUDE).map(TagLook::pressedAlpha))
        // widgets.draw_tag_pill
        assertEquals(52 to 150, TagLook.PICK.fillAlpha to TagLook.PICK.borderAlpha)
        assertEquals(230 to 255, TagLook.PICKED.fillAlpha to TagLook.PICKED.borderAlpha)
        assertEquals(TagLook.Text.DARK, TagLook.PICKED.text)
        assertEquals(0xFF101010.toInt(), TagLook.DARK)
        assertEquals(2, TagLook.ROW_MAX)
        assertEquals(0x22d96c6c, TagLook.argb("#D96C6C", 34))
        assertEquals(0xFFd96c6c.toInt(), TagLook.argb("nonsense", 255))   // unreadable: the first color
        assertEquals(0xaabbcc, TagLook.parse("#abc"))
    }

    @Test fun tagsShowTheirSavedColorAndTheRestTakeTheNextOnes() {
        val s = Settings.from(null)
        s.data["tag_colors"] = JsonObject(mapOf("jazz" to JsonPrimitive("#5a8fd9"), "old" to JsonPrimitive("#e2564b")))
        val colors = TagLook.colors(listOf("Rock", "Jazz", "old", "ambient", "rock"), s)
        assertEquals("#5a8fd9", colors["jazz"])
        assertEquals("#d96c6c", colors["old"])                    // an old Tags-dialog color, moved to the palette
        assertEquals(Settings.TAG_PALETTE[2], colors["ambient"])  // two saved, so the next is the third
        assertEquals(Settings.TAG_PALETTE[3], colors["rock"])
        assertEquals(4, colors.size)
        assertFalse((s.data["tag_colors"] as JsonObject).containsKey("rock"))   // nothing saved by looking
        assertEquals("#5a8fd9", TagLook.colorOf(" JAZZ ", colors, s))
    }

    // ---- The ad hourglass ---------------------------------------------------------------------

    @Test fun theSandFallsByArea() {
        val full = AdGlass.area(AdGlass.sand(0f).upper)
        assertTrue(full > 0)
        assertNull(AdGlass.sand(0f).lower)
        for (run in listOf(0.25f, 0.5f, 0.75f)) {
            val sand = AdGlass.sand(run)
            assertEquals("upper at $run", (1 - run) * full, AdGlass.area(sand.upper), full * 0.01)
            assertEquals("lower at $run", run * full, AdGlass.area(sand.lower), full * 0.01)
            assertNotNull(sand.stream)
        }
        val over = AdGlass.sand(1f)
        assertNull(over.upper); assertNull(over.stream)
        assertEquals(full, AdGlass.area(over.lower), full * 0.01)
    }

    @Test fun theGlassFollowsThePlayersAdClock() {
        assertEquals(0f, AdGlass.run(30.0, 30.0))                 // still connecting: the whole ad
        assertEquals(0.5f, AdGlass.run(30.0, 15.0))
        assertEquals(1f, AdGlass.run(30.0, 0.0))
        assertEquals(1f, AdGlass.run(0.0, 5.0))
        assertEquals("Ad playing. Time remaining: 1:05", AdGlass.timeText(64.6))
        assertEquals("Ad over", AdGlass.timeText(0.0))
        assertEquals("Ad over", AdGlass.timeText(null))
        assertEquals(1_500L, AdGlass.EMPTY_HOLD_MS)
    }

    // ---- Sync runs ----------------------------------------------------------------------------

    @Test fun theIntervalIsThisDevicesOwnAndDefaultsToHalfAnHour() {
        val s = Settings.from(null)
        assertEquals(30, SyncRuns.interval(s))
        s.data[SyncRuns.INTERVAL_KEY] = JsonPrimitive(0)
        assertEquals(0, SyncRuns.interval(s))
        s.data[SyncRuns.INTERVAL_KEY] = JsonPrimitive(7)
        assertEquals(30, SyncRuns.interval(s))
        assertFalse(SyncRuns.INTERVAL_KEY in SyncData.SHARED_SETTINGS)
        assertTrue("eq_advanced" in SyncData.SHARED_SETTINGS)
        assertEquals(SettingsTabs.LIBRARY, SettingsTabs.tabOf(SyncRuns.INTERVAL_KEY))
        // The launch run, then at most once an interval; never when off.
        assertTrue(SyncRuns.dueOnForeground(30, 0, 5))
        assertFalse(SyncRuns.dueOnForeground(30, 1_000, 1_000 + 29 * 60_000))
        assertTrue(SyncRuns.dueOnForeground(30, 1_000, 1_000 + 30 * 60_000))
        assertFalse(SyncRuns.dueOnForeground(0, 0, 5))
    }

    @Test fun theNumbersAndWordsAreTheDesktops() {
        assertEquals(listOf(5, 15, 30, 60, 120, 0), SyncRuns.INTERVAL_CHOICES)
        assertEquals("Couldn't find Den PC or Laptop on this network. Check that it's on the same network with Frequencies open.",
            SyncRuns.notFound(listOf("Den PC", "Laptop")))
        assertEquals("Syncing with A, B and C…", SyncRuns.busyText(listOf("A", "B", "C")))
        val d = desk ?: return
        val page = File(d, "syncpage.py").readText()
        for ((minutes, label) in SyncRuns.INTERVAL_LABELS) assertTrue(label, "$minutes: \"$label\"" in page)
        val sync = File(d, "sync.py").readText()
        fun num(name: String) = Regex("^$name = ([0-9.]+)", RegexOption.MULTILINE).find(sync)!!.groupValues[1].toDouble()
        assertEquals(SyncRuns.LOOK_FOR_MS, (num("RUN_LOOK_FOR") * 1000).toLong())
        assertEquals(SyncRuns.ART_FOR_MS, (num("RUN_ART_FOR") * 1000).toLong())
        assertEquals(SyncRuns.SHOW_AT_LEAST_MS, (num("RUN_SHOW_AT_LEAST") * 1000).toLong())
        assertEquals(SyncRuns.RETRY_MS, (num("RUN_RETRY") * 1000).toLong())
        assertTrue("ART_SEND_MAX = 8 * 1024 * 1024" in File(d, "sync_core.py").readText())
    }

    @Test fun aRunEndsWhenEveryoneAnsweredAndThePicturesCame() {
        val run = SyncRun("launch", 1_000)
        run.add(listOf("pc", "deck"), manual = false, now = 1_000)
        val paired = setOf("pc", "deck")
        val at = HashMap<String, Long>()
        assertEquals(SyncRun.Verdict.Running, run.check(1_100, paired, { at[it] }, false) { false })
        at["pc"] = 2_000; at["deck"] = 2_100
        // Pictures still coming from a connected device: keep turning.
        assertEquals(SyncRun.Verdict.Running, run.check(2_200, paired, { at[it] }, true) { true })
        assertEquals(SyncRun.Verdict.Over(emptyList()), run.check(2_300, paired, { at[it] }, false) { true })
        // A state from before the run doesn't count.
        val again = SyncRun("interval", 5_000)
        again.add(listOf("pc"), false, 5_000)
        assertEquals(SyncRun.Verdict.Running, again.check(5_500, paired, { at[it] }, false) { false })
    }

    @Test fun theSpinnerShowsAtLeastAMomentAndGivesUpAfterTheLook() {
        val run = SyncRun("Sync now", 0)
        run.add(listOf("pc"), manual = true, now = 0)
        val at = mapOf("pc" to 10L)
        assertEquals(SyncRun.Verdict.Running, run.check(500, setOf("pc"), { at[it] }, false) { false })
        assertEquals(SyncRun.Verdict.Over(emptyList()), run.check(800, setOf("pc"), { at[it] }, false) { false })
        val lost = SyncRun("Sync now", 0)
        lost.add(listOf("pc"), manual = true, now = 0)
        assertEquals(SyncRun.Verdict.Running, lost.check(24_999, setOf("pc"), { null }, false) { false })
        assertEquals(SyncRun.Verdict.Over(listOf("pc")), lost.check(25_000, setOf("pc"), { null }, false) { false })
        // A device forgotten during the run is no longer waited for.
        val forgot = SyncRun("launch", 0)
        forgot.add(listOf("pc"), false, 0)
        assertEquals(SyncRun.Verdict.Over(emptyList()), forgot.check(900, emptySet(), { null }, false) { false })
    }

    @Test fun onlySyncNowComplainsAboutADeviceThatIsntOn() {
        val names = mapOf("pc" to "Den PC")
        val auto = SyncRun("interval", 0).apply { add(listOf("pc"), false, 0) }
        assertNull(auto.problem(listOf("pc"), names::get))                       // leave the old line
        assertEquals("", auto.problem(emptyList(), names::get))                  // reached everyone: clear it
        val manual = SyncRun("interval", 0).apply { add(listOf("pc"), false, 0); add(listOf("pc"), true, 10, "Sync now") }
        assertTrue(manual.manual)
        assertEquals("Sync now", manual.why)
        // Build 198 (task 245): the short note instead of the network advice.
        assertEquals("Den PC isn't running Frequencies right now.", manual.problem(listOf("pc"), names::get))
        val repaired = SyncRun("launch", 0).apply { add(listOf("pc"), false, 0); failed("pc", SyncRuns.pairedAgain("Den PC")) }
        assertEquals("Den PC was paired again with something else. Forget it here and pair again.", repaired.problem(emptyList(), names::get))
    }

    // ---- Sync: pictures -----------------------------------------------------------------------

    private fun station(id: String, art: String) = Station.normalize(Station(linkedMapOf(
        "id" to JsonPrimitive(id), "url" to JsonPrimitive("http://$id/"), "name" to JsonPrimitive(id),
        "custom_art_path" to JsonPrimitive(art),
    )), 1.0) { "x" }

    @Test fun aBigPictureIsSentAsItsSmallerCopy() {
        val dir = Files.createTempDirectory("art16").toFile()
        val big = File(dir, "big.jpg").apply { writeBytes(ByteArray((SyncData.ART_SEND_MAX + 1).toInt()) { (it % 251).toByte() }) }
        val bigSha = SyncCrypto.sha256Hex(big.readBytes())
        val s = station("s1", big.path)
        val old = SyncData.shrink
        try {
            SyncData.shrink = { _, _ -> null }
            assertNull(SyncData.artFile(s))                       // no way to shrink it: not sent
            var asked: String? = null
            SyncData.shrink = { file, sha ->
                asked = sha
                File(dir, SyncData.smallCopyName(sha, "jpg")).apply { if (!isFile) writeBytes(file.readBytes().copyOf(1000)) }
            }
            val sent = SyncData.artFile(s)!!
            assertEquals(bigSha, asked)
            assertEquals("sync-small-${bigSha.take(16)}.jpg", sent.file.name)
            assertEquals(SyncCrypto.sha256Hex(sent.file.readBytes()), sent.sha)
            assertEquals(sent.file, SyncData.artIndex(listOf(s))[sent.sha])
            assertEquals(sent.sha, (SyncData.artOf(s) as JsonObject)["sha"].str())
            // A copy that is still too big isn't sent.
            SyncData.shrink = { _, _ -> big }
            assertNull(SyncData.artFile(s))
        } finally {
            SyncData.shrink = old
        }
        // A picture under the limit goes as it is.
        val small = File(dir, "cover.PNG").apply { writeBytes(byteArrayOf(9, 8, 7)) }
        val plain = SyncData.artFile(station("s2", small.path))!!
        assertEquals(small, plain.file)
        assertEquals("png", plain.ext)
    }

    @Test fun eachStationGetsAPictureFileOfItsOwn() {
        val dir = Files.createTempDirectory("art16b").toFile()
        val bytes = byteArrayOf(1, 2, 3, 4, 5)
        val sha = SyncCrypto.sha256Hex(bytes)
        val one = SyncData.saveArt(dir, sha, "JPG", bytes, "s-1")
        val two = SyncData.saveArt(dir, sha, "jpg", bytes, "s/2")
        assertEquals("sync-s-1-${sha.take(16)}.jpg", one.name)
        assertEquals("sync-s2-${sha.take(16)}.jpg", two.name)
        assertTrue(one.isFile && two.isFile && one != two)
        assertEquals("sync-${sha.take(16)}.png", SyncData.saveArt(dir, sha, "", bytes).name)
        assertTrue(runCatching { SyncData.saveArt(dir, "0".repeat(64), "jpg", bytes, "s3") }.isFailure)
    }
}
