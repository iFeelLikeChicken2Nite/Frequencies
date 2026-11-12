package com.frequencies.core

import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * Twitch as a radio source (the desktop's twitch.py, twitchads.py). The
 * desktop asks yt-dlp; the phone does what yt-dlp does: ask Twitch's
 * GraphQL endpoint for a playback token, then load the channel's HLS list
 * from usher with it and take the audio-only rendition (yt-dlp's
 * "bestaudio/best": audio only when Twitch offers it, else the best).
 * The links are signed and expire, so a channel is resolved every time it
 * is tuned, never saved.
 */
object Twitch {
    private val CHANNEL = Regex("^[A-Za-z0-9_]{3,25}$")
    const val GQL_URL = "https://gql.twitch.tv/gql"
    const val USHER = "https://usher.ttvnw.net"
    /** The web player's public Client-Id (twitch.WEB_CLIENT_ID, also what yt-dlp sends). */
    const val WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
    const val BROWSER_AGENT = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
    /** storage.TWITCH_AD_SECONDS: a Twitch channel covers its opening ad for 30 s unless set otherwise. */
    const val AD_SECONDS = 30.0
    /** The headers Twitch checks on the media requests (twitch.resolve). */
    val HEADERS = mapOf("Referer" to "https://www.twitch.tv/", "Origin" to "https://www.twitch.tv")

    class TwitchError(message: String, val notLive: Boolean = false) : Exception(message)

    /** twitch.channel_name: a name, a link, or something pasted from the address bar; "" if it isn't one. */
    fun channelName(text: String?): String {
        var t = text.orEmpty().trim()
        if (t.isEmpty()) return ""
        if ("twitch.tv/" in t) t = t.substringAfter("twitch.tv/").substringBefore('/').substringBefore('?')
        t = t.trimStart('@')
        return if (CHANNEL.matches(t)) t else ""
    }

    fun channelUrl(name: String) = "https://www.twitch.tv/$name"

    fun isTwitch(s: Station?): Boolean = s != null && (s.kind == "twitch" || truthy(s["twitch_channel"]))

    fun channelOf(s: Station): String = s["twitch_channel"].str()?.ifEmpty { null } ?: channelName(s.url)

    /** twitch.station_for: the channel is kept beside the link, because the link expires. */
    fun stationFor(name: String, newId: () -> String, now: Double): Station {
        val d = LinkedHashMap<String, JsonElement>()
        d["name"] = JsonPrimitive(name)
        d["kind"] = JsonPrimitive("twitch")
        d["twitch_channel"] = JsonPrimitive(name)
        d["url"] = JsonPrimitive(channelUrl(name))
        d["video"] = JsonPrimitive(false)
        d["art_source"] = JsonPrimitive("stream")
        return Station.normalize(Station(d), now, newId)
    }

    // -- the ad cover's settings (storage.ad_cover_on / ad_cover_seconds) --------

    /** The user's own choice wins; a Twitch channel never set covers its ad. */
    fun adCoverOn(s: Station): Boolean {
        val mine = s["ad_override"]
        if (mine == null || mine is kotlinx.serialization.json.JsonNull) return isTwitch(s)
        return truthy(mine)
    }

    fun adCoverSeconds(s: Station): Double {
        val mine = s["ad_override"]
        if ((mine == null || mine is kotlinx.serialization.json.JsonNull) && isTwitch(s)) return AD_SECONDS
        return s["ad_seconds"].num() ?: 0.0
    }

    /**
     * Station settings offers the ad cover here (desktop 181 shows it for
     * every station): every station but a music folder, a YouTube list or
     * an iBroadcast library, which have no ads of their own.
     */
    fun adCoverOffered(s: Station): Boolean =
        s.kind !in setOf("local", "youtube") && !IBroadcast.isIBroadcast(s)

    /**
     * bridge._ad_cover_for: this station's opening ad is covered when it
     * tunes in (on, longer than 0 s, and not a folder or YouTube list).
     */
    fun coversAd(s: Station?): Boolean =
        s != null && s.kind !in setOf("local", "youtube") && adCoverOn(s) && adCoverSeconds(s) > 0

    /** bridge._playable_cover: what can play at once (a plain stream; no Twitch, YouTube, iBroadcast or folder here). */
    fun playableCover(c: Station): Boolean = !isTwitch(c) && !IBroadcast.isIBroadcast(c) &&
        c.kind !in setOf("local", "youtube") && !truthy(c["playlist_url"]) && c.url.startsWith("http")

    /**
     * bridge.can_cover_ads + _ad_cover_for: the frequencies that may cover
     * [station]'s ad, in the order to try them: the chosen one first when
     * "Cover it with" says so, then others without ads of their own, not
     * down, never the station itself.
     */
    fun coverCandidates(station: Station, all: List<Station>, isDown: (String) -> Boolean, tries: Int = 3, random: kotlin.random.Random = kotlin.random.Random): List<Station> {
        fun can(c: Station) = !truthy(c["ad_override"]) && c.id != station.id && !(c.url.isNotEmpty() && c.url == station.url) && !isDown(c.id) && playableCover(c)
        val kind = station["ad_filler_kind"].str()?.ifEmpty { null } ?: "any"
        val chosenId = station["ad_filler_station_id"].str().orEmpty()
        val out = ArrayList<Station>()
        if (chosenId.isNotEmpty() && kind in setOf("chosen", "music", "stream")) {
            all.firstOrNull { it.id == chosenId }?.takeIf(::can)?.let { out += it }
        }
        out += all.filter { it.id != chosenId && can(it) }.shuffled(random).take(maxOf(0, tries - out.size))
        return out
    }

    // -- resolving ----------------------------------------------------------------

    /** yt-dlp's (and streamlink's) PlaybackAccessToken query, live only. */
    private const val TOKEN_QUERY = "query PlaybackAccessToken_Template(\$login: String!, \$isLive: Boolean!, \$vodID: ID!, \$isVod: Boolean!, \$playerType: String!) {" +
        "  streamPlaybackAccessToken(channelName: \$login, params: {platform: \"web\", playerBackend: \"mediaplayer\", playerType: \$playerType}) @include(if: \$isLive) {" +
        "    value    signature   authorization {      isForbidden      forbiddenReasonCode    }   __typename  }" +
        "  videoPlaybackAccessToken(id: \$vodID, params: {platform: \"web\", playerBackend: \"mediaplayer\", playerType: \$playerType}) @include(if: \$isVod) {" +
        "    value    signature   __typename  }}"

    fun tokenRequestBody(channel: String): String = buildJsonObject {
        put("operationName", "PlaybackAccessToken_Template")
        put("query", TOKEN_QUERY)
        putJsonObject("variables") {
            put("isLive", true); put("login", channel.lowercase()); put("isVod", false); put("vodID", ""); put("playerType", "site")
        }
    }.toString()

    /** (token, signature) from the GraphQL answer; a TwitchError says why there is none. */
    fun parseToken(text: String, channel: String): Pair<String, String> {
        val root = runCatching { FreqJson.parseToJsonElement(text) }.getOrNull()
            ?: throw TwitchError("Twitch's answer wasn't readable")
        val body = (root as? JsonArray)?.firstOrNull() as? JsonObject ?: root as? JsonObject
            ?: throw TwitchError("Twitch's answer wasn't readable")
        val token = (body["data"] as? JsonObject)?.get("streamPlaybackAccessToken") as? JsonObject
        if (token == null) {
            val errors = (body["errors"] as? JsonArray)?.mapNotNull { (it as? JsonObject)?.get("message").str() }.orEmpty()
            if (errors.isNotEmpty()) throw TwitchError("Twitch said: ${errors.first().take(80)}")
            throw TwitchError("there's no Twitch channel called $channel")
        }
        val auth = token["authorization"] as? JsonObject
        if (auth != null && truthy(auth["isForbidden"])) {
            throw TwitchError("Twitch won't play this channel here (${auth["forbiddenReasonCode"].str() ?: "forbidden"})")
        }
        val value = token["value"].str().orEmpty()
        val signature = token["signature"].str().orEmpty()
        if (value.isEmpty() || signature.isEmpty()) throw TwitchError("Twitch sent no playback token")
        return value to signature
    }

    /** The channel's HLS list (what yt-dlp asks usher for), audio-only renditions allowed. */
    fun usherUrl(channel: String, token: String, signature: String, random: kotlin.random.Random = kotlin.random.Random, base: String = USHER): String =
        "$base/api/channel/hls/${channel.lowercase()}.m3u8".toHttpUrl().newBuilder()
            .addQueryParameter("allow_source", "true")
            .addQueryParameter("allow_audio_only", "true")
            .addQueryParameter("allow_spectre", "false")
            .addQueryParameter("p", random.nextInt(1_000_000, 10_000_000).toString())
            .addQueryParameter("platform", "web")
            .addQueryParameter("player", "twitchweb")
            .addQueryParameter("playlist_include_framerate", "true")
            .addQueryParameter("supported_codecs", "avc1")
            .addQueryParameter("sig", signature)
            .addQueryParameter("token", token)
            .build().toString()

    /** One rendition in a master list. */
    data class Variant(val url: String, val bandwidth: Long, val name: String, val audioOnly: Boolean)

    /**
     * The renditions of an HLS master list, with Twitch's names
     * (#EXT-X-MEDIA NAME, or the VIDEO group: "audio_only", "720p60"…).
     */
    fun variants(master: String, base: String): List<Variant> {
        val lines = master.lines().map { it.trim() }.filter { it.isNotEmpty() }
        val names = HashMap<String, String>()
        for (l in lines) if (l.startsWith("#EXT-X-MEDIA:")) {
            val group = attr(l, "GROUP-ID")
            if (group.isNotEmpty()) names[group] = attr(l, "NAME").ifEmpty { group }
        }
        val out = ArrayList<Variant>()
        for ((i, l) in lines.withIndex()) {
            if (!l.startsWith("#EXT-X-STREAM-INF:")) continue
            val uri = lines.getOrNull(i + 1)?.takeIf { !it.startsWith("#") } ?: continue
            val group = attr(l, "VIDEO")
            val name = names[group] ?: group
            val codecs = attr(l, "CODECS")
            val audio = "audio_only" in name.lowercase() || "audio_only" in group.lowercase() || "audio_only" in uri ||
                (codecs.isNotEmpty() && codecs.split(',').all { it.trim().startsWith("mp4a") })
            out += Variant(absolute(base, uri), attr(l, "BANDWIDTH").toLongOrNull() ?: 0, name, audio)
        }
        return out
    }

    /** "bestaudio/best": the audio-only rendition when there is one, else the best one. */
    fun pick(variants: List<Variant>): Variant? =
        variants.filter { it.audioOnly }.maxByOrNull { it.bandwidth } ?: variants.maxByOrNull { it.bandwidth }

    private fun attr(line: String, name: String): String {
        val m = Regex("(?:^|[:,])" + Regex.escape(name) + "=(\"[^\"]*\"|[^,]*)").find(line) ?: return ""
        return m.groupValues[1].trim('"')
    }

    private fun absolute(base: String, uri: String): String =
        if (uri.startsWith("http://") || uri.startsWith("https://")) uri else runCatching { base.toHttpUrl().resolve(uri).toString() }.getOrNull() ?: uri

    /** What a resolve found: the rendition's media list, its name, and the whole master list's size. */
    data class Resolved(val url: String, val rendition: String, val renditions: Int)

    /**
     * twitch.resolve: a live channel's audio. Throws [TwitchError] with the
     * desktop's reasons ("not live right now"…). Network: never on the
     * main thread.
     */
    class Resolver(
        private val http: OkHttpClient = OkHttpClient.Builder().connectTimeout(12, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build(),
        private val gql: String = GQL_URL,
        private val usher: String = USHER,
    ) {
        fun resolve(channel: String): Resolved {
            val name = channelName(channel).ifEmpty { throw TwitchError("that isn't a Twitch channel name") }
            val request = Request.Builder().url(gql)
                .post(tokenRequestBody(name).toRequestBody("text/plain;charset=UTF-8".toMediaType()))
                .header("Client-Id", WEB_CLIENT_ID)
                .header("User-Agent", BROWSER_AGENT)
                .build()
            val (token, sig) = http.newCall(request).execute().use { r ->
                if (!r.isSuccessful) throw TwitchError("could not read the channel (HTTP ${r.code})")
                parseToken(r.body!!.string(), name)
            }
            val master = usherUrl(name, token, sig, base = usher)
            val text = http.newCall(get(master)).execute().use { r ->
                val body = r.body?.string().orEmpty()
                // usher answers 404 (transcode_does_not_exist / "Can not find channel") for a channel that isn't live.
                if (r.code == 404 || r.code == 403 && "offline" in body.lowercase()) throw TwitchError("not live right now", notLive = true)
                if (!r.isSuccessful) throw TwitchError("could not read the stream list (HTTP ${r.code})")
                body
            }
            if (!text.trimStart().startsWith("#EXTM3U")) throw TwitchError("not live right now", notLive = true)
            val all = variants(text, master)
            val best = pick(all) ?: throw TwitchError("live, but no playable stream was offered")
            return Resolved(best.url, best.name, all.size)
        }

        fun get(url: String): Request = Request.Builder().url(url).header("User-Agent", BROWSER_AGENT)
            .apply { HEADERS.forEach { (k, v) -> header(k, v) } }.build()

        /** A media list's text (for the ad watch's log), or null. */
        fun read(url: String): String? = runCatching {
            http.newCall(get(url)).execute().use { r -> if (r.isSuccessful) r.body?.string() else null }
        }.getOrNull()
    }

    // -- what the stream is sending (twitchads.summarize) ---------------------------

    private val AD_MARKS = listOf("stitched-ad", "twitch-ad", "x-tv-twitch-ad", "amazon")

    /** A media list in a few words: segments, how many are ads, Twitch's ad marks. */
    data class Summary(val segments: Int, val ads: Int, val marks: Set<String>, val lastTitle: String) {
        val adNow: Boolean get() = ads > 0 || marks.isNotEmpty()
        override fun toString(): String = buildString {
            append("$segments segments, $ads ad")
            if (marks.isNotEmpty()) append(" (${marks.sorted().joinToString(", ")})")
            if (lastTitle.isNotEmpty()) append(", last '${lastTitle.take(40)}'")
        }
    }

    fun summarize(text: String): Summary {
        var segments = 0
        var ads = 0
        val marks = HashSet<String>()
        var nextIsAd = false
        var lastTitle = ""
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            val low = line.lowercase()
            if (line.startsWith("#EXT-X-DATERANGE") && AD_MARKS.any { it in low }) {
                marks += Regex("CLASS=\"([^\"]+)\"").find(line)?.groupValues?.get(1) ?: "daterange ad"
            }
            if (line.startsWith("#EXTINF")) {
                val title = line.substringAfter(',', "")
                lastTitle = title
                nextIsAd = title.isNotEmpty() && !title.equals("live", ignoreCase = true)
            } else if (!line.startsWith("#")) {
                segments++
                if (nextIsAd) ads++
                nextIsAd = false
            }
        }
        return Summary(segments, ads, marks, lastTitle)
    }
}
