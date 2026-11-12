package com.frequencies.core

import java.io.File
import java.nio.file.Files
import java.text.SimpleDateFormat
import java.util.Locale
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Artist info, photos, overrides and thumbs down, checked against answers the desktop's own code gave. */
class ArtistTest {
    private fun resource(name: String) = File(javaClass.getResource("/artist/$name")!!.toURI())
    private val answers = FreqJson.parseToJsonElement(resource("answers.json").readText()).jsonObject
    private val http = OkHttpClient()

    private fun server(routes: (RecordedRequest) -> MockResponse): MockWebServer =
        MockWebServer().apply {
            dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest) = routes(request)
            }
            start()
        }

    private fun json(text: String) = MockResponse().setHeader("Content-Type", "application/json").setBody(text)

    @Test
    fun artistNamesMatchTheDesktop() {
        val table = answers["table"]!!.jsonArray.associate { it.jsonPrimitive.content to JsonObject(emptyMap()) }
        for ((credit, want) in answers["find_artist"]!!.jsonObject) {
            val w = want.jsonArray.map { it.jsonPrimitive.content }
            assertEquals("find_artist($credit)", w[0] to w[1], ArtistOverrides.findArtist(table, credit))
        }
        for ((credit, want) in answers["artist_keys"]!!.jsonObject) {
            val w = want.jsonArray.map { p -> p.jsonArray.let { it[0].jsonPrimitive.content to it[1].jsonPrimitive.content } }
            assertEquals("artist_keys($credit)", w, ArtistOverrides.artistKeys(credit))
        }
        for ((credit, want) in answers["loose"]!!.jsonObject) {
            assertEquals("loose($credit)", want.jsonPrimitive.content, ArtistOverrides.looseArtist(credit))
        }
        for ((url, want) in answers["norm_stream"]!!.jsonObject) {
            assertEquals("norm_stream($url)", want.jsonPrimitive.content, ArtistOverrides.normStream(url))
        }
        for ((raw, want) in answers["format_span"]!!.jsonObject) {
            assertEquals(want.jsonPrimitive.content, ArtistInfo.formatSpan(raw))
        }
        for (row in answers["looks_musical"]!!.jsonArray) {
            val (d, s, w) = row.jsonArray
            assertEquals(w.jsonPrimitive.content.toBoolean(), ArtistInfo.looksMusical(d.jsonPrimitive.content, s.jsonPrimitive.content))
        }
    }

    @Test
    fun talkStationsMatchTheDesktop() {
        val entries = answers["talk_entries"]!!.jsonObject
        val dir = Files.createTempDirectory("talk").toFile()
        val overrides = ArtistOverrides({ JsonObject(emptyMap()) }, { entries }, { JsonObject(emptyMap()) }, { JsonObject(emptyMap()) }, PhotoPackStore(dir))
        for (row in answers["talk_entry"]!!.jsonArray) {
            val station = Station(LinkedHashMap(row.jsonArray[0].jsonObject))
            val want = row.jsonArray[1].jsonArray
            val got = overrides.talkEntry(station)
            if (want[0].jsonPrimitive.content.isEmpty()) assertNull(got)
            else {
                assertEquals(want[0].jsonPrimitive.content, got!!.first)
                assertEquals(want[2].jsonPrimitive.content, got.third)
            }
        }
        val kbem = Station(LinkedHashMap(mapOf("name" to JsonPrimitive("Mine"), "url" to JsonPrimitive("https://kbem-live.streamguys1.com/kbem_mp3"))))
        val info = overrides.stationInfo(kbem)
        assertTrue(truthy(info["talk"]))
        assertEquals("stream address", info["_matched"].str())
        // A plain station correction isn't a talk station, and still holds the tab.
        val corrected = overrides.stationInfo(Station(LinkedHashMap(mapOf("name" to JsonPrimitive("Plain correction"), "url" to JsonPrimitive("http://z/")))))
        assertEquals("Typed in.", corrected["bio"].str())
        assertFalse(truthy(corrected["talk"]))
    }

    @Test
    fun photoPackFromTheDesktopUnpacksAndLayers() {
        val pack = resource("frequencies-photo-pack.b32").readBytes()
        val manifest = resource("photo-pack.json").readText()
        val bios = resource("bio-fixes.json").readText()
        var packGets = 0
        val web = server { r ->
            when (r.path) {
                "/latest/photo-pack.json" -> json(manifest)
                "/latest/frequencies-photo-pack.b32" -> { packGets++; MockResponse().setBody(Buffer().write(pack)) }
                "/latest/bio-fixes.json" -> json(bios)
                else -> MockResponse().setResponseCode(404)
            }
        }
        val dir = Files.createTempDirectory("pack").toFile()
        val store = PhotoPackStore(dir)
        val updater = PackUpdater(http, store)
        val address = PackAddresses(JsonObject(mapOf("pack" to JsonPrimitive(web.url("/latest/photo-pack.json").toString()))))
        val progress = ArrayList<Int>()
        val first = updater.updatePack(address.pack) { progress += it }
        assertEquals(first.text, "updated", first.state)
        assertEquals("3 photos, 3 artists, made 2026-10-29", first.text)  // 2 artist photo files (one is a link) + 1 talk photo
        assertEquals(0, progress.first())
        assertEquals("current", updater.updatePack(address.pack).state)
        assertEquals(1, packGets)
        assertEquals("updated", updater.updateBios(address.bioFixes).state)
        assertEquals("current", updater.updateBios(address.bioFixes).state)

        // Photos and talk photos as files here; the eminem bytes came through both base32 layers.
        val eminem = store.artistEntry("eminem")
        assertEquals("ÿØÿ eminem".repeat(50), File(eminem["photo"].str()!!).readBytes().toString(Charsets.ISO_8859_1))
        assertEquals("Photo: Somebody", eminem["photo_source"].str())
        assertNotNull(store.talkPhoto("kbem.jpg"))
        assertEquals("https://example.com/b.jpg", store.artistEntry("beyoncé")["photo"].str())

        val own = JsonObject(mapOf("barenaked ladies" to JsonObject(mapOf("bio" to JsonPrimitive("Mine."), "photo" to JsonPrimitive("/not/on/this/phone.jpg")))))
        val shipped = JsonObject(mapOf("dracula" to JsonObject(mapOf("wikipedia" to JsonPrimitive("Dracula (band)")))))
        val overrides = ArtistOverrides({ shipped }, { JsonObject(emptyMap()) }, { own }, { JsonObject(emptyMap()) }, store)
        // Featured guests and spellings find the pack's photo; a credit matched by its first artist keeps its own name.
        assertTrue(overrides.artistOverride("Eminem feat. Dido")["photo"].str()!!.replace('\\', '/').endsWith("photos/eminem.jpg"))
        assertEquals("rock", overrides.artistOverride("Dracula")["genres"].str())
        assertEquals("Dracula (band)", overrides.artistOverride("Dracula")["wikipedia"].str())
        // The user's own bio wins; their photo isn't on this phone, so the pack's shows.
        val bnl = overrides.artistOverride("The Barenaked Ladies")
        assertEquals("Mine.", bnl["bio"].str())
        assertTrue(bnl["photo"].str()!!.replace('\\', '/').endsWith("photos/barenaked-ladies.png"))
        assertEquals("talk_photos/kbem.jpg", overrides.talkPhotoPath("talk_photos/kbem.jpg")!!.relativeTo(store.packDir).path.replace('\\', '/'))
        assertEquals(
            "photo pack, photos/eminem.jpg (https://example.com/eminem)",
            ThumbsDown.photoLink(eminem["photo"].str()!!, store, overrides.artistOverride("Eminem")),
        )

        // A broken upload is refused and the old pack kept.
        web.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = when (request.path) {
                "/latest/photo-pack.json" -> json(manifest.replace(Regex("\"sha256\": \"[0-9a-f]+\""), "\"sha256\": \"00\""))
                else -> MockResponse().setBody("AAAA")
            }
        }
        val broken = updater.updatePack(address.pack)
        assertEquals("failed", broken.state)
        assertTrue(store.artistEntry("eminem").containsKey("photo"))
        assertTrue(store.remove())
        assertTrue(store.artistEntry("eminem").isEmpty())
        assertEquals("Mine.", overrides.artistOverride("Barenaked Ladies")["bio"].str())
        web.shutdown()
    }

    @Test
    fun base32Streams() {
        val plain = ByteArray(1000) { (it * 7).toByte() }
        for (n in listOf(0, 1, 2, 3, 4, 5, 6, 999, 1000)) {
            val text = b32(plain.copyOf(n))
            val back = Base32Input(text.byteInputStream()).readBytes()
            assertEquals("length $n", plain.copyOf(n).toList(), back.toList())
        }
    }

    /** RFC 4648 base32, the slow way, for the round trip. */
    private fun b32(bytes: ByteArray): String {
        val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        val bits = bytes.joinToString("") { String.format("%8s", Integer.toBinaryString(it.toInt() and 0xff)).replace(' ', '0') }
        val padded = bits + "0".repeat((5 - bits.length % 5) % 5)
        val text = padded.chunked(5).joinToString("") { alphabet[it.toInt(2)].toString() }
        return text + "=".repeat((8 - text.length % 8) % 8)
    }

    @Test
    fun thumbsDownWordingMatchesTheDesktop() {
        val at = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).parse("2026-10-29 12:00")!!
        for (row in answers["issues"]!!.jsonArray.map { it.jsonObject }) {
            val (title, body) = ThumbsDown.issueText("Dracula", "Biography from Wikipedia", "B".repeat(250), row["what"].str()!!, "photo pack, photos/dracula.jpg", at)
            assertEquals(row["title"].str(), title)
            assertEquals(row["body"].str(), body)
            assertEquals(row["address"].str(), ThumbsDown.issueAddress("https://github.com/someone/frequencies-reports", title, body))
        }
        val settings = Settings(LinkedHashMap())
        assertTrue(ThumbsDown.due(settings, "Dracula", at))
        // Asking doesn't mark (desktop 152): only a report that went does.
        assertTrue(ThumbsDown.due(settings, "Dracula", at))
        ThumbsDown.markSent(settings, "Dracula", at)
        assertFalse(ThumbsDown.due(settings, "dracula", at))
        val tomorrow = SimpleDateFormat("yyyy-MM-dd", Locale.US).parse("2026-10-30")!!
        assertTrue(ThumbsDown.due(settings, "Dracula", tomorrow))
        ThumbsDown.noteWrong(settings, "Dracula", "Biography from Wikipedia", "bio")
        ThumbsDown.noteWrong(settings, "Dracula", "Biography from Wikipedia", "both")
        val entry = (settings["artist_reports"] as JsonObject)["dracula"] as JsonObject
        assertEquals(2, entry["wrong"].num()!!.toInt())
        assertEquals("both", entry["what"].str())
        assertEquals(
            "https://github.com/me/frequencies-reports",
            PackAddresses(JsonObject(mapOf("pack" to JsonPrimitive("https://github.com/me/frequencies-photos/releases/latest/download/photo-pack.json"),
                "reports" to JsonPrimitive("https://github.com/me/frequencies-reports")))).reportRepo,
        )
        assertEquals(
            "https://github.com/me/frequencies-photos",
            PackAddresses(JsonObject(mapOf("pack" to JsonPrimitive("https://github.com/me/frequencies-photos/releases/latest/download/photo-pack.json")))).reportRepo,
        )
    }

    @Test
    fun reportsGoToTheWorker() {
        // cloudflare/README.md (desktop build 151): POST <address>/report, JSON fields, answers 201 / 200 already / error.
        var answer = MockResponse().setResponseCode(201).setBody("{\"ok\": true, \"url\": \"https://github.com/me/frequencies-reports/issues/9\"}")
        val web = server { r -> if (r.path == "/report" && r.method == "POST") answer else MockResponse().setResponseCode(404) }
        val address = web.url("").toString()
        val poster = ReportPoster(http)
        val fields = ReportPoster.fields("Styx", "Wikipedia", "x".repeat(300), "bio", "", "KXCI", "154")
        assertEquals(ReportPoster.Result.Filed("https://github.com/me/frequencies-reports/issues/9"), poster.send(address, fields))
        val sent = web.takeRequest()
        assertTrue(sent.getHeader("Content-Type")!!.startsWith("application/json"))
        assertEquals(null, sent.getHeader("Authorization"))
        val body = FreqJson.parseToJsonElement(sent.body.readUtf8()) as JsonObject
        assertEquals(listOf("artist", "what", "source", "bio", "photo", "station", "build", "os"), body.keys.toList())
        assertEquals("Styx", body["artist"].str())
        assertEquals("bio", body["what"].str())
        assertEquals(200, body["bio"].str()!!.length)
        assertEquals("KXCI", body["station"].str())
        assertEquals("android", body["os"].str())
        assertEquals("both", ReportPoster.fields("A", "", "", "nonsense")["what"].str())
        assertEquals("test", ReportPoster.fields("A", "", "", "test")["what"].str())

        answer = MockResponse().setResponseCode(200).setBody("{\"ok\": true, \"already\": true}")
        assertEquals(ReportPoster.Result.Already, poster.send(address, fields))
        answer = MockResponse().setResponseCode(429).setBody("{\"error\": \"too many reports today\"}")
        assertEquals(ReportPoster.Result.Failed("the report service answered 429 (too many reports today)"), poster.send(address, fields))
        answer = MockResponse().setResponseCode(200).setBody("not json")
        assertTrue(poster.send(address, fields) is ReportPoster.Result.Failed)
        web.shutdown()
        assertEquals(ReportPoster.Result.Failed("no report address"), poster.send("", fields))
        assertEquals(ReportPoster.Result.Failed("couldn't reach the report service (offline?)"), poster.send("http://127.0.0.1:9", fields))

        assertEquals("https://r.example.workers.dev", PackAddresses(JsonObject(mapOf("report_service" to JsonPrimitive(" https://r.example.workers.dev/ ")))).reportService)
        assertEquals("", PackAddresses(JsonObject(mapOf("report_service" to JsonPrimitive("r.example.workers.dev")))).reportService)
    }

    @Test
    fun whichHalfIsTheArtist() {
        var asked = 0
        val web = server { r ->
            asked++
            val q = r.requestUrl!!.queryParameter("query")!!
            when (q) {
                "artist:\"Enya\"" -> json("{\"artists\":[{\"name\":\"Enya\",\"score\":100}]}")
                "artist:\"Orinoco Flow\"" -> json("{\"artists\":[{\"name\":\"Orinoco\",\"score\":90}]}")
                "artist:\"Morning Show\"" -> json("{\"artists\":[]}")
                "artist:\"KXCI\"" -> json("{\"artists\":[{\"name\":\"KXCI\",\"score\":40}]}")
                else -> json("{\"artists\":[]}")
            }
        }
        val info = ArtistInfo(http, musicbrainz = web.url("/ws/2").toString().trimEnd('/'), musicbrainzGapMs = 0)
        assertEquals("Enya" to "Orinoco Flow", info.splitArtistTitle("Enya - Orinoco Flow"))
        assertEquals("Enya" to "Orinoco Flow", info.splitArtistTitle("Orinoco Flow - Enya"))
        assertNull(info.splitArtistTitle("KXCI - Morning Show"))
        assertNull(info.splitArtistTitle("No dash here"))
        val before = asked
        info.splitArtistTitle("Enya - Orinoco Flow")
        assertEquals("answers are kept", before, asked)
        web.shutdown()
    }

    @Test
    fun biographyRoutes() {
        val web = server { r ->
            val p = java.net.URLDecoder.decode(r.path!!, "UTF-8")
            when {
                p.startsWith("/mb/artist/abc-id?") && "url-rels" in p ->
                    json("{\"relations\":[{\"type\":\"wikipedia\",\"url\":{\"resource\":\"https://en.wikipedia.org/wiki/Muse_%28band%29\"}}]}")
                p.startsWith("/mb/artist?") && r.requestUrl!!.queryParameter("query") == "Muse" ->
                    json("{\"artists\":[{\"id\":\"abc-id\",\"name\":\"Muse\",\"score\":100,\"area\":{\"name\":\"United Kingdom\"},\"life-span\":{\"begin\":\"1994\"},\"tags\":[{\"name\":\"rock\"},{\"name\":\"alternative rock\"}]}]}")
                p.startsWith("/mb/artist?") -> json("{\"artists\":[{\"id\":\"x\",\"name\":\"Somebody\",\"score\":60}]}")
                p == "/wiki/Muse_(band)" ->
                    json("{\"type\":\"standard\",\"title\":\"Muse (band)\",\"extract\":\"Muse are an English rock band.\",\"description\":\"English rock band\",\"thumbnail\":{\"source\":\"https://upload.example/muse.jpg\"},\"content_urls\":{\"desktop\":{\"page\":\"https://en.wikipedia.org/wiki/Muse_(band)\"}}}")
                p == "/wiki/Prince_(band)" -> MockResponse().setResponseCode(404)
                p == "/wiki/Prince_(musician)" ->
                    json("{\"type\":\"standard\",\"title\":\"Prince (musician)\",\"extract\":\"Prince was an American singer.\",\"description\":\"American musician (1958-2016)\",\"content_urls\":{\"desktop\":{\"page\":\"https://en.wikipedia.org/wiki/Prince_(musician)\"}}}")
                p == "/wiki/Dracula_(band)" ->
                    json("{\"type\":\"standard\",\"title\":\"Dracula (band)\",\"extract\":\"Dracula is a band.\",\"description\":\"\",\"content_urls\":{\"desktop\":{\"page\":\"https://w/Dracula_(band)\"}}}")
                p.startsWith("/discogs/database/search") ->
                    json("{\"results\":[{\"title\":\"Tiny Act\",\"resource_url\":\"${'$'}{BASE}/discogs/artists/1\"}]}".replace("${'$'}{BASE}", "http://${r.requestUrl!!.host}:${r.requestUrl!!.port}"))
                p == "/discogs/artists/1" -> json("{\"name\":\"Tiny Act\",\"profile\":\"[b]Tiny[/b] act from [a123] somewhere [url=http://x]here[/url].\",\"uri\":\"https://www.discogs.com/artist/1\"}")
                else -> MockResponse().setResponseCode(404)
            }
        }
        val base = web.url("").toString().trimEnd('/')
        val info = ArtistInfo(http, musicbrainz = "$base/mb", wikipedia = "$base/wiki", wikidata = "$base/wd", discogs = "$base/discogs", musicbrainzGapMs = 0)
        val muse = info.biography("Muse")!!
        assertEquals("Muse", muse.name)
        assertEquals("United Kingdom", muse.area)
        assertEquals("1994 - present", muse.years)
        assertEquals(listOf("rock", "alternative rock"), muse.tags)
        assertEquals("Muse are an English rock band.", muse.summary)
        assertEquals("https://upload.example/muse.jpg", muse.photo)
        assertEquals("", muse.source)
        val prince = info.biography("Prince")!!
        assertEquals("Prince", prince.name)
        assertEquals("Wikipedia", prince.source)
        val forced = info.biography("Dracula", mapOf("wikipedia" to JsonPrimitive("Dracula (band)")))!!
        assertEquals("Dracula is a band.", forced.summary)
        val typed = info.biography("Anyone", mapOf("bio" to JsonPrimitive("Typed."), "name" to JsonPrimitive("Any One")))!!
        assertEquals("Any One" to "Typed.", typed.name to typed.summary)
        val tiny = info.biography("Tiny Act")!!
        assertEquals("Discogs", tiny.source)
        assertEquals("Tiny act from  somewhere here.", tiny.summary)
        assertNull(info.biography("Nobody At All"))
        web.shutdown()
    }

    @Test
    fun artistPanelPicksLikeTheDesktop() {
        val web = server { r ->
            val p = java.net.URLDecoder.decode(r.path!!, "UTF-8")
            when {
                p.startsWith("/wiki/Some_Band_(band)") ->
                    json("{\"type\":\"standard\",\"title\":\"Some Band\",\"extract\":\"Some Band is a band.\",\"description\":\"band\",\"content_urls\":{\"desktop\":{\"page\":\"https://en.wikipedia.org/wiki/Some_Band\"}}}")
                p.startsWith("/deezer") -> json("{\"data\":[{\"name\":\"Some Band\",\"picture_xl\":\"http://${r.requestUrl!!.host}:${r.requestUrl!!.port}/img/big.png\"}]}")
                p == "/img/big.png" -> MockResponse().setBody(Buffer().write(png(800, 600)))
                else -> json("{}")
            }
        }
        val base = web.url("").toString().trimEnd('/')
        val dir = Files.createTempDirectory("panel").toFile()
        val info = ArtistInfo(http, musicbrainz = "$base/mb", wikipedia = "$base/wiki", wikidata = "$base/wd", discogs = "$base/discogs", musicbrainzGapMs = 0)
        val photos = ArtistPhotos(http, dir, wikidataApi = "$base/wd", wikidataEntity = "$base/wde", deezer = "$base/deezer", itunes = "$base/itunes")
        val overrides = ArtistOverrides({ JsonObject(emptyMap()) }, { JsonObject(emptyMap()) }, { JsonObject(emptyMap()) }, { JsonObject(emptyMap()) }, PhotoPackStore(dir))
        val lookup = ArtistLookup(info, photos, overrides)
        val panel = lookup.biography("Some Band")
        assertEquals("Some Band is a band.", panel.bio)
        assertEquals(listOf(Credit("Biography from", "Wikipedia", "https://en.wikipedia.org/wiki/Some_Band")), panel.credits)
        assertEquals("Biography from Wikipedia", panel.bioSource)
        assertNull(panel.photo)
        assertTrue(panel.canJudge)
        val (file, credit) = lookup.findPhoto("Some Band", emptySet())!!
        assertTrue(File(file).isFile)
        assertEquals("photo from", credit!!.prefix)
        // Kept: the second time is a file, no credit, nothing asked.
        assertEquals(file, lookup.findPhoto("Some Band", emptySet())!!.first)
        // The cover already showing is not used again.
        assertNull(ArtistLookup(info, ArtistPhotos(http, Files.createTempDirectory("p2").toFile(), wikidataApi = "$base/wd", wikidataEntity = "$base/wde", deezer = "$base/deezer", itunes = "$base/itunes"), overrides)
            .findPhoto("Some Band", setOf("$base/img/big.png")))
        web.shutdown()
    }

    /** The smallest PNG header ImageSize reads. */
    private fun png(w: Int, h: Int): ByteArray {
        val b = ByteArray(33)
        byteArrayOf(0x89.toByte(), 'P'.code.toByte(), 'N'.code.toByte(), 'G'.code.toByte(), 13, 10, 26, 10).copyInto(b)
        b[11] = 13; b[12] = 'I'.code.toByte(); b[13] = 'H'.code.toByte(); b[14] = 'D'.code.toByte(); b[15] = 'R'.code.toByte()
        for (i in 0 until 4) { b[16 + i] = (w shr (24 - 8 * i)).toByte(); b[20 + i] = (h shr (24 - 8 * i)).toByte() }
        return b
    }

}
