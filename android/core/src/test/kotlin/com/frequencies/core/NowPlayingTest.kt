package com.frequencies.core

import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NowPlayingTest {
    private fun station(vararg pairs: Pair<String, Any>): Station {
        val s = Station(LinkedHashMap())
        s["id"] = JsonPrimitive("abc")
        s["name"] = JsonPrimitive("Nightwave Plaza")
        s["url"] = JsonPrimitive("https://radio.plaza.one/mp3")
        for ((k, v) in pairs) s[k] = when (v) {
            is Boolean -> JsonPrimitive(v)
            is Number -> JsonPrimitive(v)
            is List<*> -> JsonArray(v.map { JsonPrimitive(it as Number) })
            else -> JsonPrimitive(v.toString())
        }
        return Station.normalize(s, 1.0) { "id" }
    }

    // -- history ----------------------------------------------------------

    @Test
    fun historySkipsTheSamePlayAndKeepsStars() {
        var h = JsonArray(emptyList())
        h = SongHistory.add(h, "Dreams", "Fleetwood Mac", "KTDT", 100.0)!!
        assertNull(SongHistory.add(h, "Dreams", "Fleetwood Mac", "KTDT", 130.0))
        h = SongHistory.add(h, "Dreams", "Fleetwood Mac", "WBER", 160.0)!!
        assertEquals(2, h.size)
        val key = SongHistory.trackKey(" fleetwood mac", "DREAMS ")
        h = SongHistory.setFavorite(h, key, true, 170.0)!!
        h = SongHistory.add(h, "Africa", "Toto", "KTDT", 200.0)!!
        // A starred song heard again moves up with its star, no second row.
        h = SongHistory.add(h, "Dreams", "Fleetwood Mac", "Lush", 300.0)!!
        val first = h[0] as JsonObject
        assertEquals("Lush", SongHistory.station(first))
        assertTrue(SongHistory.isFavorite(first))
        assertEquals(3, h.size)
        assertEquals(1, SongHistory.clear(h).size)
    }

    @Test
    fun historyCapLeavesFavoritesAlone() {
        var h = JsonArray(emptyList())
        h = SongHistory.setFavorite(h, SongHistory.trackKey("A", "Starred"), true, 1.0, "Starred", "A", "X")!!
        for (i in 0 until 15) h = SongHistory.add(h, "Song $i", "B", "X", 10.0 + i, limit = 10)!!
        assertEquals(11, h.size)
        assertEquals("Starred", SongHistory.title(SongHistory.sorted(h)[0]))
        assertEquals(10, SongHistory.limit(Settings.from(null).apply { this["shazam_history_limit"] = JsonPrimitive(3) }))
    }

    @Test
    fun searchLinksAreTheDesktops() {
        val links = SongHistory.searchLinks("AC/DC", "T.N.T. & more").toMap()
        assertEquals("https://music.apple.com/us/search?term=AC/DC%20T.N.T.%20%26%20more", links["Apple Music"])
        assertEquals("https://open.spotify.com/search/AC/DC%20T.N.T.%20%26%20more", links["Spotify"])
    }

    // -- now-playing line and art ----------------------------------------

    @Test
    fun readoutFollowsTheStationsChoice() {
        val song = "Midnight City" to "M83"
        val auto = station()
        assertEquals(Readout("Midnight City", "M83", "shazam"), NowPlayingRules.readout(auto, song, "M83 - Something"))
        // No song: Automatic shows the custom text, which is the station's name when there is none.
        assertEquals("Nightwave Plaza", NowPlayingRules.readout(auto, null, "M83 - Something")!!.title)
        val streamFirst = station("readout_source" to "auto_stream")
        assertEquals(Readout("Something", "M83", "stream"), NowPlayingRules.readout(streamFirst, null, "M83 - Something"))
        assertEquals("Nightwave Plaza", NowPlayingRules.readout(streamFirst, null, null)!!.title)
        val custom = station("readout_source" to "custom", "custom_readout" to "The Morning Show")
        assertEquals("The Morning Show", NowPlayingRules.readout(custom, song, "x - y")!!.title)
        assertNull(NowPlayingRules.readout(station("readout_source" to "shazam"), null, "x - y"))
        assertEquals("Just words", NowPlayingRules.readout(station("readout_source" to "stream"), song, "Just words")!!.title)
    }

    @Test
    fun artOrderIsResolveOrders() {
        assertEquals(listOf("shazam", "custom", "stream"), NowPlayingRules.artOrder(station()))
        assertEquals(listOf("stream", "shazam", "custom"), NowPlayingRules.artOrder(station("art_source" to "stream")))
        assertEquals(listOf("custom"), NowPlayingRules.artOrder(station("art_source" to "custom", "art_fallback" to false)))
    }

    @Test
    fun coverSearchWantsTheSameArtist() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"results":[
                {"artistName":"Someone Else","artworkUrl100":"https://x/wrong/100x100bb.jpg"},
                {"artistName":"M83","artworkUrl100":"https://x/right/100x100bb.jpg"}]}"""))
            val url = CoverSearch(OkHttpClient(), server.url("/search").toString()).find("M83", "Midnight City")
            assertEquals("https://x/right/600x600bb.jpg", url)
            val asked = server.takeRequest().requestUrl!!
            assertEquals("M83 Midnight City", asked.queryParameter("term"))
            assertEquals("song", asked.queryParameter("entity"))
        }
    }

    // -- lyrics -------------------------------------------------------------

    @Test
    fun lrcParsesLikeLrclibPy() {
        val lines = LyricsClient.parseLrc("[ar: Someone]\n[00:12.34]First\n[00:15.5]\n[01:02.345][00:05.00]Both\nplain")
        assertEquals(listOf(5000L, 12340L, 15500L, 62345L), lines.map { it.ms })
        assertEquals("", lines[2].text)
        assertEquals("Both", lines[0].text)
        assertEquals(1, LyricsClient.lineAt(lines, 13000))
        assertEquals(-1, LyricsClient.lineAt(lines, 100))
    }

    @Test
    fun cleanDropsRadioNoise() {
        assertEquals("Song", LyricsClient.clean("Song (Official Video)"))
        assertEquals("Song", LyricsClient.clean("Song - 2011 Remastered Version"))
        assertEquals("Song", LyricsClient.clean("Song [HD]  "))
    }

    private fun lyricsServer(routes: (RecordedRequest) -> MockResponse): MockWebServer =
        MockWebServer().apply { dispatcher = object : Dispatcher() { override fun dispatch(request: RecordedRequest) = routes(request) } }

    @Test
    fun timedLyricsFromTheSecondHostWhenTheFirstIsDown() {
        lyricsServer { r ->
            when {
                r.path!!.startsWith("/mux/") -> MockResponse().setResponseCode(503)
                r.path!!.startsWith("/lrclib/search") -> MockResponse().setBody(
                    """[{"plainLyrics":"a"},{"syncedLyrics":"[00:01.00]Hello\n[00:03.00]World","plainLyrics":"Hello\nWorld"}]""")
                else -> MockResponse().setResponseCode(404)
            }
        }.use { server ->
            val client = LyricsClient(OkHttpClient(), listOf(server.url("/mux").toString(), server.url("/lrclib").toString()),
                server.url("/lrclib").toString(), server.url("/ovh").toString(), retryWaitsMs = emptyList())
            val found = client.fetch("Artist", "Title") as LyricsResult.Found
            assertTrue(found.timed)
            assertEquals(listOf(1000L, 3000L), found.lines.map { it.ms })
            assertEquals("LRCLIB", found.source)
        }
    }

    @Test
    fun plainLyricsFromLyricsOvhAndBusyWhenAllFail() {
        lyricsServer { r ->
            when {
                r.path!!.startsWith("/ovh/Artist/Title") -> MockResponse().setBody("""{"lyrics":"line one\nline two"}""")
                r.path!!.contains("/search") -> MockResponse().setBody("[]")
                else -> MockResponse().setResponseCode(404)
            }
        }.use { server ->
            val base = server.url("/lrclib").toString()
            val client = LyricsClient(OkHttpClient(), listOf(base), base, server.url("/ovh").toString(), retryWaitsMs = emptyList())
            val found = client.fetch("Artist", "Title (Official Audio)") as LyricsResult.Found
            assertFalse(found.timed)
            assertEquals("lyrics.ovh", found.source)
            assertEquals(listOf("line one", "line two"), found.lines.map { it.text })
        }
        lyricsServer { MockResponse().setResponseCode(500) }.use { server ->
            val base = server.url("/lrclib").toString()
            val client = LyricsClient(OkHttpClient(), listOf(base), base, server.url("/ovh").toString(), retryWaitsMs = emptyList())
            assertEquals(LyricsResult.Failed(LyricsClient.BUSY), client.fetch("Artist", "Title"))
        }
        lyricsServer { r -> if (r.path!!.contains("search")) MockResponse().setBody("[]") else MockResponse().setResponseCode(404) }.use { server ->
            val base = server.url("/lrclib").toString()
            val client = LyricsClient(OkHttpClient(), listOf(base), base, server.url("/ovh").toString(), retryWaitsMs = emptyList())
            assertEquals(LyricsResult.None, client.fetch("Artist", "Title"))
        }
    }

    // -- equalizer ----------------------------------------------------------

    @Test
    fun equalizerChoosesBandsLikePlayerPy() {
        val s = station("eq_bass" to 3, "eq_bands" to listOf(0, 0, 0, 0, 0, 6, 0, 0, 0, -4))
        assertEquals(listOf(100.0, 1000.0, 8000.0), Equalizer.bands(s, advanced = false).map { it.hz })
        val ten = Equalizer.bands(s, advanced = true)
        assertEquals(listOf(1000.0 to 6.0, 16000.0 to -4.0), ten.map { it.hz to it.gainDb })
        // Ten-band on, all ten flat: bass/mid/treble still apply.
        assertEquals(3, Equalizer.bands(station("eq_bass" to 3), advanced = true).size)
        assertTrue(Equalizer.bands(station(), advanced = true).isEmpty())
        assertEquals(listOf("31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"), Equalizer.BAND_LABELS)
    }

    @Test
    fun peakingFilterHasItsGainAtTheCenter() {
        for (rate in listOf(44100, 48000)) for (hz in Equalizer.BANDS) {
            val c = Equalizer.peaking(Equalizer.Band(hz.toDouble(), 9.0, 1.0), rate)!!
            assertEquals(9.0, Equalizer.responseDb(c, hz.toDouble(), rate), 0.01)
            // One octave wide: about half the gain an octave... well, well down two octaves out.
            assertTrue(Equalizer.responseDb(c, hz * 4.0, rate) < 3.0 || hz * 4 > rate / 2)
        }
        assertNull(Equalizer.peaking(Equalizer.Band(16000.0, 6.0, 1.0), 22050))
    }

    @Test
    fun chainBoostsASineByTheBandsGainPlusTheTrim() {
        val rate = 48000
        val chain = Equalizer.Chain(listOf(Equalizer.Band(1000.0, 6.0, 1.0)), gainDb = -2.0, sampleRate = rate, channels = 2)
        val frames = rate
        val buf = FloatArray(frames * 2) { i -> (0.1 * kotlin.math.sin(2 * Math.PI * 1000 * (i / 2) / rate)).toFloat() }
        chain.process(buf, frames)
        val tail = (frames / 2 until frames).maxOf { kotlin.math.abs(buf[2 * it]) }
        assertEquals(4.0, 20 * kotlin.math.log10(tail / 0.1), 0.05)
    }

    // -- art upgrade ----------------------------------------------------------

    private fun logo(size: Int, hue: Int = 0, shift: Int = 0): BufferedImage {
        val img = BufferedImage(size, size, BufferedImage.TYPE_INT_RGB)
        val g = img.createGraphics()
        g.setRenderingHint(java.awt.RenderingHints.KEY_ANTIALIASING, java.awt.RenderingHints.VALUE_ANTIALIAS_ON)
        g.color = java.awt.Color(20 + hue, 20, 60)
        g.fillRect(0, 0, size, size)
        g.color = java.awt.Color(240, 120, 200)
        g.fillOval(size / 5 + shift * size / 10, size / 5, size * 3 / 5, size * 3 / 5)
        g.color = java.awt.Color(250, 240, 90)
        g.fillRect(size / 10, size * 7 / 10, size * 8 / 10, size / 8)
        g.dispose()
        return img
    }

    private fun BufferedImage.art() = ArtImage(width, height, getRGB(0, 0, width, height, null, 0, width), colorModel.hasAlpha())
    private fun BufferedImage.png(): ByteArray = ByteArrayOutputStream().also { ImageIO.write(this, "png", it) }.toByteArray()
    private val decode: (ByteArray) -> ArtImage? = { bytes -> ImageIO.read(bytes.inputStream())?.art() }

    @Test
    fun theSamePictureBiggerMatchesAndAnotherDoesNot() {
        val thumb = logo(96).art()
        val big = ArtMatch.compare(thumb, logo(600).art())
        assertTrue(big.toString(), big.same)
        val other = ArtMatch.compare(thumb, logo(600, shift = 2).art())
        assertFalse(other.toString(), other.same)
        assertTrue(ArtMatch.detail(BufferedImage(64, 64, BufferedImage.TYPE_INT_RGB).art()) < ArtMatch.DETAIL_MIN)
    }

    @Test
    fun pageImagesAndTuneInLogos() {
        val page = """<head><meta property="og:image" content="/img/logo.png?a=1&amp;b=2">
            <link rel="apple-touch-icon" sizes="180x180" href="https://cdn.example/touch.png"></head>"""
        assertEquals(listOf("https://plaza.one/img/logo.png?a=1&b=2", "https://cdn.example/touch.png"),
            ArtMatch.pageImages(page, "https://plaza.one/"))
        val tunein = FreqJson.parseToJsonElement("""{"body":[
            {"type":"audio","text":"Other","image":"https://cdn-radiotime-logos.tunein.com/s1q.png"},
            {"type":"audio","text":"Nightwave Plaza","image":"https://cdn-profiles.tunein.com/s2/images/logoq.jpg"}]}""") as JsonObject
        assertEquals("https://cdn-profiles.tunein.com/s2/images/logog.jpg", ArtMatch.tuneInLogos(tunein, "Nightwave Plaza")[0])
    }

    @Test
    fun searchTakesTheFullSizeCopyFromTheDirectory() {
        val big = logo(512).png()
        val wrong = logo(512, shift = 2).png()
        lyricsServer { r ->
            val p = r.path!!
            when {
                p.startsWith("/json/stations/byurl") -> MockResponse().setBody("[]")
                p.startsWith("/json/stations/search") -> MockResponse().setBody(
                    """[{"stationuuid":"1","name":"Nightwave Plaza","url":"u","favicon":"${r.requestUrl!!.newBuilder("/wrong.png")}"},
                        {"stationuuid":"2","name":"Nightwave Plaza","url":"u2","favicon":"${r.requestUrl!!.newBuilder("/right.png")}"}]""")
                p == "/wrong.png" -> MockResponse().setBody(okio.Buffer().write(wrong))
                p == "/right.png" -> MockResponse().setBody(okio.Buffer().write(big))
                else -> MockResponse().setResponseCode(404)
            }
        }.use { server ->
            val host = "http://${server.hostName}:${server.port}"
            val directory = RadioBrowser(OkHttpClient(), listOf(host))
            val finder = ArtFinder(decode, directory, OkHttpClient(), server.url("/tunein?q=").toString())
            val s = station("homepage" to "")
            s["url"] = JsonPrimitive("ftp://nowhere")       // no byurl, no site icons
            val result = finder.search(s, logo(100).png())
            assertEquals(result.notes.toString(), "found", result.status)
            assertTrue(result.url.endsWith("/right.png"))
            assertNotNull(result.bytes)
        }
    }
}
