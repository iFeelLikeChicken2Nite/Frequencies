package com.frequencies.core

import java.io.File
import java.net.Socket
import java.nio.file.Files
import kotlin.concurrent.thread
import kotlin.random.Random
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
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

/**
 * AP10b: iBroadcast signing, the Twitch resolver, YouTube rules, the
 * featured list, band logos and hints. Expected links come from the
 * desktop's own ibroadcast.py / youtube.py run with the same inputs
 * (clock fixed at 1800000000). The Twitch answers are fixtures in the
 * shape Twitch sends (the sandbox can't reach Twitch); a live check runs
 * on the build PC (LiveCheckTest, asked for with -Pfrequencies.live=true).
 */
class ServicesTest {
    private fun resource(path: String) = javaClass.getResource("/$path")!!.readText()

    // -- iBroadcast ---------------------------------------------------------------

    private val ibSettings = FreqJson.parseToJsonElement("""{"streaming_server":"https://streaming.ibroadcast.com"}""").jsonObject
    private fun track(vararg pairs: Pair<String, String>) = pairs.associate { (k, v) -> k to (JsonPrimitive(v) as kotlinx.serialization.json.JsonElement?) }

    @Test fun streamLinksMatchTheDesktop() {
        val t = 1800000000L
        assertEquals(
            "https://streaming.ibroadcast.com/320/11a/dad/255993361?Expires=1800086400&Signature=tok%2Fen%2B1&file_id=255993361&user_id=u5&platform=frequencies&version=1.5",
            IBroadcast.streamUrl(ibSettings, track("file" to "128/11a/dad/255993361", "file_id" to "255993361"), "tok/en+1", "u5", "320", t),
        )
        assertEquals(
            "https://streaming.ibroadcast.com/320/Albums/Imaginal%20Disk/Misc/mini%20mix%20vol.%202%20%5Bx%5D.mp3?Expires=1800086400&Signature=tok%2Fen%2B1&file_id=77&user_id=u5&platform=frequencies&version=1.5",
            IBroadcast.streamUrl(ibSettings, track("file" to "Misc/mini mix vol. 2 [x].mp3", "path" to "Albums/Imaginal Disk", "file_id" to "77"), "tok/en+1", "u5", "320", t),
        )
        assertEquals(
            "https://streaming.ibroadcast.com/320/musics?Expires=1800086400&Signature=tok%2Fen%2B1&file_id=9&user_id=u5&platform=frequencies&version=1.5",
            IBroadcast.streamUrl(ibSettings, track("path" to "musics", "file_id" to "9"), "tok/en+1", "u5", "320", t),
        )
        assertEquals(
            "https://streaming.ibroadcast.com/320/42?Expires=1800086400&Signature=tok%2Fen%2B1&file_id=42&user_id=u5&platform=frequencies&version=1.5",
            IBroadcast.streamUrl(ibSettings, track("id" to "42"), "tok/en+1", "u5", "320", t),
        )
    }

    @Test fun tracksAreSignedAtPlayTimeWithTheCurrentToken() {
        val saved = IBroadcast.streamUrl(ibSettings, track("file" to "128/11a/dad/255993361", "file_id" to "255993361"), "old", "u5", "128", 1800000000L)
        val resigned = IBroadcast.refreshUrl(saved, "newtok", 1800003600L)
        assertEquals("https://streaming.ibroadcast.com/128/11a/dad/255993361?Expires=1800090000&Signature=newtok&file_id=255993361&user_id=u5&platform=frequencies&version=1.5", resigned)
        assertEquals("https://streaming.ibroadcast.com/orig/11a/dad/255993361?Expires=1800090000&Signature=newtok&file_id=255993361&user_id=u5&platform=frequencies&version=1.5", IBroadcast.atBitrate(resigned, "orig"))
        // The station's chosen rate is applied as it is signed.
        val station = Station(linkedMapOf("ibroadcast_bitrate" to JsonPrimitive("orig")))
        assertEquals(IBroadcast.atBitrate(resigned, "orig"), IBroadcast.fresh(saved, station, "newtok", 1800003600L))
        assertEquals("255993361", IBroadcast.fileIdOf(saved))
        // Not one of ours: left alone.
        assertEquals("https://example.com/a.mp3", IBroadcast.refreshUrl("https://example.com/a.mp3", "x"))
    }

    @Test fun pkceMatchesTheDesktop() {
        assertEquals("y6ZhjM9sk5Aeknr80eYoO4jhKwS6QKRPCOwKxbo40nk", IBroadcast.challengeFor("dBjftJeZ4CVP-mJ92K9qUkuiT0Avv9kxfM8fsk1M4qw"))
        val url = IBroadcast.authorizeUrl("v", "s")
        assertTrue("code_challenge_method=S256" in url)
        assertTrue("redirect_uri=http%3A%2F%2F127.0.0.1%3A8731%2Fcallback" in url)
    }

    private val library = FreqJson.parseToJsonElement(
        """{"library":{"tracks":{"map":{"title":0,"artist_id":1,"album_id":2,"file":3,"artwork_id":4,"nested":{"x":1}},
           "5":["Song",7,8,"128/a/b/5",0],"6":["Other",7,8,"128/a/b/6",123]},
           "albums":{"map":{"name":0,"artwork_id":1},"8":["Album",555]},
           "artists":{"map":{"name":0},"7":["The Band"]},
           "playlists":{"map":{"name":0,"tracks":1},"1":["Road trip",[6,5,99]],"2":["Recently Played",[5]]}}}""",
    ).jsonObject
    private val status = FreqJson.parseToJsonElement("""{"user":{"id":"u5"},"settings":{"streaming_server":"https://streaming.ibroadcast.com"}}""").jsonObject

    @Test fun libraryRowsAndCovers() {
        val account = IBroadcast.Account(status, library)
        assertEquals("Song", account.tracks["5"]!!["title"].str())
        assertEquals("https://artwork.ibroadcast.com/artwork/555-300", IBroadcast.artworkUrl(null, account.tracks["5"]!!, account.albums))
        assertEquals("https://art.example/artwork/123-300", IBroadcast.artworkUrl(JsonObject(mapOf("artwork_server" to JsonPrimitive("https://art.example/"))), account.tracks["6"]!!, account.albums))
        assertEquals("The Band", IBroadcast.nameFor(account.artists, JsonPrimitive(7)))
        // "Recently Played" isn't offered.
        assertEquals(listOf("Road trip"), account.playlists.map { it.name })
    }

    @Test fun aPlaylistBecomesAStationWithFourFieldTitles() {
        val account = IBroadcast.Account(status, library)
        var n = 0
        val s = IBroadcast.station(account, "1", "", "320", false, "tok", { "id${n++}" }, 1800000000.0)!!
        assertEquals("Road trip", s.name)
        assertEquals("ibroadcast", s.kind)
        assertEquals(2, IBroadcast.urls(s).size)                // track 99 isn't in the library
        assertEquals(IBroadcast.urls(s)[0], s.url)
        assertEquals(IBroadcast.Track("The Band", "Other", "Album", "https://artwork.ibroadcast.com/artwork/123-300"), IBroadcast.track(s, 0))
        assertTrue(IBroadcast.urls(s)[1].startsWith("https://streaming.ibroadcast.com/320/a/b/5?"))
        // Like the desktop: every station carries shuffle_contents (on by default), which wins over ibroadcast_shuffle.
        assertTrue(IBroadcast.shuffles(s, null))
        s["shuffle_contents"] = JsonPrimitive(false)
        assertFalse(IBroadcast.shuffles(s, null))
        assertTrue(Health.isIbroadcast(s))
        assertFalse(Health.skippedWhenDown(s))                 // never auto-skipped
        val all = IBroadcast.station(account, IBroadcast.LIBRARY_CHOICE, "", "128", false, "tok", { "id${n++}" }, 1800000000.0)!!
        assertEquals("iBroadcast", all.name)
        assertTrue(IBroadcast.shuffles(all, null))
    }

    @Test fun queueOrderAndRetry() {
        assertEquals(0, IBroadcast.nextIndex(3, -1, false))
        assertEquals(0, IBroadcast.nextIndex(3, 2, false))
        assertEquals(1, IBroadcast.retryFrom(2, false).let { IBroadcast.nextIndex(3, it, false) - 1 })
        assertEquals(2, IBroadcast.nextIndex(3, IBroadcast.retryFrom(2, false), false))   // the refused track again
        assertEquals(-1, IBroadcast.nextIndex(0, -1, true))
    }

    @Test fun tokenRenewal() {
        val server = MockWebServer()
        var answer = MockResponse().setBody("""{"access_token":"fresh","refresh_token":"r2","expires_in":3600}""")
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = answer
        }
        server.start()
        val dir = Files.createTempDirectory("ib").toFile()
        var now = 1800000000.0
        val account = IBroadcastAccount(File(dir, "ibroadcast.json"), OkHttpClient(), oauth = server.url("/").toString().trimEnd('/'), clock = { now })
        assertEquals("signed out", account.freshToken().problem)
        assertFalse(account.needsRefresh())
        account.store(JsonObject(mapOf("access_token" to JsonPrimitive("old"), "refresh_token" to JsonPrimitive("r1"), "expires_in" to JsonPrimitive(3600))))
        assertTrue(account.signedIn)
        assertEquals("old", account.currentToken())
        // Plenty of time left: no network.
        assertEquals("old", account.freshToken().token)
        assertEquals(0, server.requestCount)
        now += 3100.0                                           // under ten minutes left
        assertTrue(account.needsRefresh())
        val r = account.freshToken()
        assertEquals("fresh", r.token)
        assertEquals(3600L, r.lifetime)
        assertEquals("fresh", account.currentToken())
        val sent = server.takeRequest().body.readUtf8()
        assertTrue("grant_type=refresh_token" in sent && "refresh_token=r1" in sent)
        // Refused on purpose: a real sign-in is needed.
        answer = MockResponse().setResponseCode(400).setBody("""{"error":"invalid_grant"}""")
        assertTrue(account.freshToken(force = true).problem.startsWith("rejected (HTTP 400"))
        assertEquals("fresh", account.currentToken())           // the old one stays until signing out
        account.signOut()
        assertFalse(account.signedIn)
        server.shutdown()
    }

    @Test fun theRedirectCatcherCatchesTheCode() {
        val catcher = RedirectCatcher(0)
        val port = catcher.port
        thread {
            Thread.sleep(150)
            Socket("127.0.0.1", port).use { s ->
                s.getOutputStream().write("GET /favicon.ico HTTP/1.1\r\nHost: x\r\n\r\n".toByteArray())
                s.getInputStream().readBytes()
            }
            Socket("127.0.0.1", port).use { s ->
                s.getOutputStream().write("GET /callback?code=abc%2F1&state=xyz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".toByteArray())
                assertTrue(String(s.getInputStream().readBytes()).contains("Signed in."))
            }
        }
        assertEquals(RedirectCatcher.Answer("abc/1", "xyz", ""), catcher.await(10))
    }

    // -- Twitch -------------------------------------------------------------------

    @Test fun channelNames() {
        assertEquals("lofigirl", Twitch.channelName("https://www.twitch.tv/lofigirl?sr=a"))
        assertEquals("some_dj", Twitch.channelName("@some_dj"))
        assertEquals("", Twitch.channelName("no spaces allowed"))
        assertEquals("", Twitch.channelName("ab"))
    }

    @Test fun playbackTokenAndRenditions() {
        val (token, sig) = Twitch.parseToken(resource("twitch/gql-live.json"), "lofigirl")
        assertTrue(token.startsWith("{\"adblock\":false"))
        assertEquals("0d1f3a9c2b7e4f6a8c0e2d4b6a8f0c1e3d5b7a9f", sig)
        val e = runCatching { Twitch.parseToken(resource("twitch/gql-nouser.json"), "nobody") }.exceptionOrNull() as Twitch.TwitchError
        assertEquals("there's no Twitch channel called nobody", e.message)
        val f = runCatching { Twitch.parseToken(resource("twitch/gql-forbidden.json"), "x") }.exceptionOrNull() as Twitch.TwitchError
        assertTrue(f.message!!.contains("GEOBLOCK"))

        val all = Twitch.variants(resource("twitch/master.m3u8"), "https://usher.ttvnw.net/api/channel/hls/lofigirl.m3u8")
        assertEquals(listOf("1080p60 (source)", "720p60", "160p", "audio_only"), all.map { it.name })
        val pick = Twitch.pick(all)!!
        assertEquals("audio_only", pick.name)
        assertTrue(pick.audioOnly)
        assertEquals("https://video-weaver.sea02.hls.ttvnw.net/v1/playlist/Cs0Faudio_only.m3u8", pick.url)
        // No audio-only rendition: the best one (yt-dlp's "bestaudio/best").
        assertEquals("1080p60 (source)", Twitch.pick(all.filter { !it.audioOnly })!!.name)

        val usher = Twitch.usherUrl("LofiGirl", token, sig, Random(1))
        assertTrue(usher.startsWith("https://usher.ttvnw.net/api/channel/hls/lofigirl.m3u8?"))
        assertTrue("allow_audio_only=true" in usher && "sig=$sig" in usher && "token=%7B%22adblock" in usher)
    }

    @Test fun resolverAgainstAFakeTwitch() {
        val server = MockWebServer()
        var live = true
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when {
                request.path!!.startsWith("/gql") -> {
                    assertEquals(Twitch.WEB_CLIENT_ID, request.getHeader("Client-Id"))
                    assertTrue(request.body.readUtf8().contains("\"login\":\"lofigirl\""))
                    MockResponse().setBody(resource("twitch/gql-live.json"))
                }
                request.path!!.startsWith("/api/channel/hls/lofigirl.m3u8") ->
                    if (live) MockResponse().setBody(resource("twitch/master.m3u8"))
                    else MockResponse().setResponseCode(404).setBody("""[{"url":"...","error":"twirp error not_found: transcode does not exist","error_code":"transcode_does_not_exist","type":"error"}]""")
                else -> MockResponse().setResponseCode(500)
            }
        }
        server.start()
        val resolver = Twitch.Resolver(OkHttpClient(), gql = server.url("/gql").toString(), usher = server.url("/").toString().trimEnd('/'))
        val found = resolver.resolve("https://twitch.tv/lofigirl")
        assertEquals("audio_only", found.rendition)
        assertEquals(4, found.renditions)
        live = false
        val off = runCatching { resolver.resolve("lofigirl") }.exceptionOrNull() as Twitch.TwitchError
        assertTrue(off.notLive)
        assertEquals("not live right now", off.message)
        server.shutdown()
    }

    @Test fun adMarksInTheMediaList() {
        val ad = Twitch.summarize(resource("twitch/media-ad.m3u8"))
        assertEquals(3, ad.segments)
        assertEquals(2, ad.ads)
        assertEquals(setOf("twitch-stitched-ad"), ad.marks)
        assertTrue(ad.adNow)
        assertEquals("3 segments, 2 ad (twitch-stitched-ad), last 'live'", ad.toString())
        assertFalse(Twitch.summarize(resource("twitch/media-live.m3u8")).adNow)
    }

    @Test fun adCoverRulesAndCandidates() {
        var n = 0
        val tw = Twitch.stationFor("lofigirl", { "t${n++}" }, 1.0)
        assertTrue(Twitch.adCoverOn(tw))
        assertEquals(30.0, Twitch.adCoverSeconds(tw), 0.0)
        fun plain(id: String, url: String) = Station(linkedMapOf("id" to JsonPrimitive(id), "url" to JsonPrimitive(url), "name" to JsonPrimitive(id)))
        val a = plain("a", "https://a.example/stream")
        val down = plain("d", "https://d.example/stream")
        val ads = plain("x", "https://x.example/stream").apply { this["ad_override"] = JsonPrimitive(true) }
        val yt = YouTube.stationFor("https://www.youtube.com/playlist?list=PL1", "Mix", { "y" }, 1.0)
        val got = Twitch.coverCandidates(tw, listOf(tw, a, down, ads, yt), isDown = { it == "d" })
        assertEquals(listOf("a"), got.map { it.id })
        tw["ad_filler_kind"] = JsonPrimitive("chosen")
        tw["ad_filler_station_id"] = JsonPrimitive("a")
        assertEquals("a", Twitch.coverCandidates(tw, listOf(tw, a), isDown = { false }).first().id)
    }

    // -- YouTube ------------------------------------------------------------------

    @Test fun youTubeRules() {
        assertTrue(YouTube.looksLikePlaylist("https://www.youtube.com/playlist?list=PL123"))
        assertFalse(YouTube.looksLikePlaylist("https://youtu.be/abc"))
        assertTrue(YouTube.looksLikePlaylist("https://www.youtube.com/@chan"))
        assertTrue(YouTube.looksLikePlaylist("https://music.youtube.com/watch?v=x&list=RD1"))
        assertFalse(YouTube.looksLikePlaylist("https://example.com/playlist"))
        val s = YouTube.stationFor("https://www.youtube.com/playlist?list=PL1", "", { "y" }, 1.0)
        assertEquals("YouTube", s.name)
        assertEquals("youtube://https://www.youtube.com/playlist?list=PL1", s.url)
        assertEquals("https://www.youtube.com/playlist?list=PL1", YouTube.playlistOf(s))
        val entries = YouTube.keep(listOf(YouTube.Entry("a", "One", 200.0), YouTube.Entry("b", "[Private video]", 0.0), YouTube.Entry("c", "Two", 20.0)))
        assertEquals(listOf("a", "c"), entries.map { it.id })
        repeat(20) { assertEquals("c", YouTube.pick(entries, "a")!!.id) }
        assertEquals(0.0, YouTube.joinFraction(20.0), 0.0)
        assertTrue(YouTube.joinFraction(200.0) < YouTube.JOIN_MAX)

        val dir = Files.createTempDirectory("yt").toFile()
        var now = 1000.0
        val cache = YouTube.Cache(dir) { now }
        cache.write("u", entries, "My mix")
        assertEquals(entries to "My mix", cache.read("u"))
        now += YouTube.CACHE_TTL_SECONDS + 1
        assertNull(cache.read("u"))
    }

    // -- Featured -----------------------------------------------------------------

    @Test fun featuredListDailyCacheAndCards() {
        val server = MockWebServer()
        val body = """{"stations":[{"name":"Night Owl","url":"https://owl.example/live","codec":"mp3","bitrate":128,
            "settings":{"name":"Night Owl","url":"https://owl.example/live","card_title":"After midnight","card_body":"Slow records for late drives.","tags":["ambient","night"]}},
            {"name":"Plain","url":"https://plain.example:8000/s","codec":"aac","bitrate":64}]}"""
        server.enqueue(MockResponse().setBody(body))
        server.enqueue(MockResponse().setResponseCode(503))
        server.start()
        val dir = Files.createTempDirectory("feat").toFile()
        var day = "2026-11-02"
        val list = FeaturedList(dir, OkHttpClient(), server.url("/featured.json").toString()) { day }
        val first = list.load()
        assertEquals(2, first.size)
        assertEquals(2, list.load().size)                       // the same day: from the cache
        assertEquals(1, server.requestCount)
        day = "2026-11-03"
        assertEquals(2, list.load().size)                       // the fetch failed: yesterday's picks
        val card = FeaturedList.card(first[0])
        assertEquals("Night Owl", card.name)
        assertEquals("ambient, night", card.subtitle)
        assertEquals("After midnight", card.heading)
        assertEquals("Slow records for late drives.", card.body)
        assertTrue(card.bodyIsBlurb)
        // No blurb: what is known about the stream, nothing made up.
        val plain = FeaturedList.card(first[1])
        assertEquals("AAC · 64 kbps", plain.body)          // never where it streams from (build 172)
        assertFalse(plain.bodyIsBlurb)
        server.shutdown()
    }

    // -- band logos ---------------------------------------------------------------

    @Test fun bandLogosFromTheLogoPack() {
        val dir = Files.createTempDirectory("logos").toFile()
        val pack = File(dir, BandLogos.FOLDER).apply { mkdirs() }
        File(pack, "logos").mkdirs()
        File(pack, "logos/the-band.png").writeBytes(byteArrayOf(1, 2, 3))
        File(pack, "pack.json").writeText("""{"format":1,"artists":{"the band":{"logo":"logos/the-band.png","logo_scale":3.0,"logo_x":0.25},"ghost":{"logo":"logos/missing.png"},"the beatles":{"logo":"../escape.png"}}}""")
        val store = PhotoPackStore(dir, BandLogos.FOLDER)
        val logo = BandLogos.find(store, "Band", on = true)!!            // "The" doesn't matter
        assertEquals("the-band.png", logo.file.name)
        assertEquals(2.0, logo.scale, 0.0)                               // clamped
        assertEquals(0.25, logo.x, 0.0)
        assertNull(BandLogos.find(store, "The Band", on = false))        // switched off
        assertNull(BandLogos.find(store, "Ghost", on = true))            // file isn't here
        assertNull(BandLogos.find(store, "The Beatles", on = true))      // never outside the pack
        assertNull(BandLogos.find(store, "The Band & Friends", on = true)) // not a collaboration's first artist

        val box = BandLogos.Box(12.0, 100.0, 300.0, 96.0)
        val r = BandLogos.place(box, 400.0, 100.0, 1.0, 0.0, 0.0)!!
        assertEquals(BandLogos.Box(12.0, 121.0, 300.0, 75.0), r)
        val kept = BandLogos.place(box, 400.0, 100.0, 2.0, 0.5, 0.0, BandLogos.Box(4.0, 4.0, 320.0, 200.0))!!
        assertTrue(kept.right <= 324.0 + 1e-9 && kept.width <= 320.0)
        assertNull(BandLogos.needsOutline(listOf(0.9, 0.95, 0.1), 0.05))   // stands apart
        assertEquals(false, BandLogos.needsOutline(listOf(0.05, 0.1, 0.1), 0.08)) // dark on dark: a light outline
    }

    @Test fun hintsShowOnceUntilReset() {
        val s = Settings.from(null)
        assertTrue(Hints.shouldShow(s, Hints.SEARCH))
        Hints.dismiss(s, Hints.SEARCH)
        assertFalse(Hints.shouldShow(s, Hints.SEARCH))
        assertTrue(Hints.shouldShow(s, Hints.SHARE))
        Hints.reset(s)
        assertTrue(Hints.shouldShow(s, Hints.SEARCH))
        assertTrue(s[Hints.KEY] is JsonArray)
    }

    @Test fun browseModes() {
        assertEquals(listOf("Most listened", "Trending", "Random", "Most voted", "New"), RadioBrowser.BROWSE.map { it.second })
        assertNotNull(FeaturedList.EMPTY)
    }
}
