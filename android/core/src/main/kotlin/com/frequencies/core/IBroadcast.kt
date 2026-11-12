package com.frequencies.core

import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.FormBody
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * iBroadcast (the desktop's ibroadcast.py and ibtoken.py): OAuth 2 with
 * PKCE, the library, and stream links signed per track at play time.
 *
 * The sign-in uses the desktop's own app registration and its loopback
 * redirect (http://127.0.0.1:8731/callback): the phone listens there for
 * the few seconds a sign-in takes ([RedirectCatcher]), so iBroadcast
 * needs no new redirect address. No client secret, as on the desktop.
 */
object IBroadcast {
    const val CLIENT_ID = "38f3f34ca8ac11f1b50eb49691aa2236"
    const val CLIENT = "frequencies"
    const val VERSION = "1.5"
    const val SCOPES = "user.library:read"
    const val REDIRECT_PORT = 8731
    const val REDIRECT_URI = "http://127.0.0.1:$REDIRECT_PORT/callback"
    const val USER_AGENT = "Mozilla/5.0 (Linux; Android) Frequencies/$VERSION"
    const val ARTWORK = "https://artwork.ibroadcast.com"
    const val STREAMING = "https://streaming.ibroadcast.com"

    /** What the streaming server transcodes to; "orig" is the upload. No 192 (it never plays). */
    val BITRATES = listOf("96", "128", "256", "320", "orig")
    private val KNOWN_RATES = BITRATES + "192"
    val BITRATE_LABELS = linkedMapOf(
        "96" to "96 kbps — smallest", "128" to "128 kbps", "256" to "256 kbps",
        "320" to "320 kbps", "orig" to "Original — as uploaded",
    )
    /** bridge.IB_JOIN_MAX: a shuffled station is joined up to 60% into its first track. */
    const val JOIN_MAX = 0.6
    /** ibtoken.TokenKeeper: look every minute, renew with ten minutes left, five minutes between automatic renewals. */
    const val CHECK_SECONDS = 60L
    const val AHEAD_SECONDS = 600L
    const val COOLDOWN_SECONDS = 300L

    class IBroadcastError(message: String) : Exception(message)

    // -- PKCE ------------------------------------------------------------------

    private val random = SecureRandom()
    private fun b64(bytes: ByteArray) = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)

    fun makeVerifier(): String = b64(ByteArray(64).also(random::nextBytes))
    fun makeState(): String = b64(ByteArray(16).also(random::nextBytes))
    fun challengeFor(verifier: String): String =
        b64(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))

    /** "S256" in capitals: iBroadcast's own docs say s256, which its server refuses. */
    fun authorizeUrl(verifier: String, state: String, oauth: String = OAUTH): String =
        "$oauth/authorize".toHttpUrl().newBuilder()
            .addQueryParameter("client_id", CLIENT_ID)
            .addQueryParameter("state", state)
            .addQueryParameter("response_type", "code")
            .addQueryParameter("code_challenge", challengeFor(verifier))
            .addQueryParameter("code_challenge_method", "S256")
            .addQueryParameter("scope", SCOPES)
            .addQueryParameter("redirect_uri", REDIRECT_URI)
            .build().toString()

    const val OAUTH = "https://oauth.ibroadcast.com"
    const val API = "https://api.ibroadcast.com/s/JSON/"
    const val LIBRARY = "https://library.ibroadcast.com/"

    // -- the library's shape ----------------------------------------------------

    /**
     * ibroadcast.rows: a column-mapped section as rows. Only the map's
     * entries that really are column numbers count (some point at nested
     * maps, which once took the whole add-a-station flow down).
     */
    fun rows(section: JsonElement?): Map<String, Map<String, JsonElement?>> {
        val obj = section as? JsonObject ?: return emptyMap()
        val columns = (obj["map"] as? JsonObject)?.mapNotNull { (name, i) ->
            val p = i as? JsonPrimitive ?: return@mapNotNull null
            if (p.isString || p.content == "true" || p.content == "false") return@mapNotNull null
            p.content.toIntOrNull()?.let { name to it }
        }.orEmpty()
        val out = LinkedHashMap<String, Map<String, JsonElement?>>()
        for ((key, values) in obj) {
            if (key == "map" || values !is JsonArray) continue
            out[key] = columns.associate { (name, i) -> name to values.getOrNull(i) }
        }
        return out
    }

    fun nameFor(rows: Map<String, Map<String, JsonElement?>>, key: JsonElement?): String =
        rows[key.str().orEmpty()]?.get("name").str().orEmpty().trim()

    /** ibroadcast.artwork_url: the track's own cover, else its album's; "" for none. */
    fun artworkUrl(settings: JsonObject?, track: Map<String, JsonElement?>, albums: Map<String, Map<String, JsonElement?>> = emptyMap()): String {
        var art = track["artwork_id"].str().orEmpty()
        if (art.isEmpty() || art == "0") art = albums[track["album_id"].str().orEmpty()]?.get("artwork_id").str().orEmpty()
        if (art.isEmpty() || art == "0") return ""
        val server = (settings?.get("artwork_server").str()?.ifEmpty { null } ?: ARTWORK).trimEnd('/')
        return "$server/artwork/$art-300"
    }

    /**
     * ibroadcast.stream_url: a playable link for one track. The rate is the
     * path's first segment; `file` already starts with one, which is
     * replaced, never stacked. Paths are escaped, slashes kept.
     */
    fun streamUrl(settings: JsonObject?, track: Map<String, JsonElement?>, token: String, userId: String, bitrate: String = "128", nowSeconds: Long = System.currentTimeMillis() / 1000): String {
        val server = settings?.get("streaming_server").str()?.ifEmpty { null } ?: STREAMING
        var name = track["file"].str().orEmpty()
        val folder = track["path"].str().orEmpty()
        if (name.isNotEmpty() && folder.isNotEmpty() && !name.startsWith("/") && !name.startsWith(folder)) {
            name = folder.trimEnd('/') + "/" + name.trimStart('/')
        } else if (name.isEmpty()) {
            name = folder
        }
        val fileId = track["file_id"].str()?.ifEmpty { null } ?: track["id"].str().orEmpty()
        if (name.isEmpty() && fileId.isEmpty()) return ""
        val rate = if (bitrate in BITRATES) bitrate else "128"
        val query = listOf(
            "Expires" to (nowSeconds + 86400).toString(),
            "Signature" to token,
            "file_id" to fileId,
            "user_id" to userId,
            "platform" to CLIENT,
            "version" to VERSION,
        ).joinToString("&") { (k, v) -> "$k=${form(v)}" }
        if (name.isNotEmpty()) {
            var cleaned = name.trimStart('/')
            val head = cleaned.substringBefore('/', "")
            val rest = cleaned.substringAfter('/', "")
            if (head in BITRATES && rest.isNotEmpty()) cleaned = rest
            val safe = cleaned.split('/').joinToString("/") { pathPart(it) }
            return "${server.trimEnd('/')}/$rate/$safe?$query"
        }
        return "${server.trimEnd('/')}/$rate/$fileId?$query"
    }

    /**
     * ibroadcast.refresh_url: a saved link signed for now: a new expiry a
     * day ahead and the current sign-in as its signature (the token that
     * signed it when the station was added has long run out).
     */
    fun refreshUrl(url: String, token: String, nowSeconds: Long = System.currentTimeMillis() / 1000): String {
        if (url.isEmpty() || "Expires=" !in url) return url
        val (head, query, fragment) = split(url) ?: return url
        // Python's parse_qsl + urlencode: the same keys in the same order, two values replaced.
        val fields = LinkedHashMap<String, String>()
        for (part in query.split('&')) {
            if (part.isEmpty()) continue
            fields[unform(part.substringBefore('='))] = unform(part.substringAfter('=', ""))
        }
        fields["Expires"] = (nowSeconds + 86400).toString()
        if (token.isNotEmpty()) fields["Signature"] = token
        return head + "?" + fields.entries.joinToString("&") { (k, v) -> form(k) + "=" + form(v) } + fragment
    }

    /** ibroadcast.at_bitrate: the same track at another quality (the rate is a path segment). */
    fun atBitrate(url: String, bitrate: String): String {
        if (url.isEmpty() || bitrate.isEmpty()) return url
        val (head, query, fragment) = split(url) ?: return url
        val scheme = head.substringBefore("://", "")
        if (scheme.isEmpty()) return url
        val rest = head.substringAfter("://")
        val host = rest.substringBefore('/')
        val pieces = rest.substringAfter('/', "").split('/').toMutableList()
        val i = pieces.indexOfFirst { it in KNOWN_RATES }
        if (i < 0) return url
        pieces[i] = bitrate
        return "$scheme://$host/" + pieces.joinToString("/") + (if (query.isNotEmpty() || '?' in url) "?$query" else "") + fragment
    }

    /** (everything before the query, the query, "#fragment" or ""), or null for something that isn't a link. */
    private fun split(url: String): Triple<String, String, String>? {
        if ("://" !in url) return null
        val fragment = if ('#' in url) "#" + url.substringAfter('#') else ""
        val body = url.substringBefore('#')
        return Triple(body.substringBefore('?'), body.substringAfter('?', ""), fragment)
    }

    private fun unform(v: String) = runCatching { java.net.URLDecoder.decode(v, "UTF-8") }.getOrDefault(v)

    /** The file_id a link asks for, or "". */
    fun fileIdOf(url: String): String =
        split(url)?.second?.split('&')?.firstOrNull { it.startsWith("file_id=") }?.substringAfter('=')?.let(::unform).orEmpty()

    /** A link with its signature hidden, for the log. */
    fun shown(url: String): String = url.substringBefore('?')

    /** Python's quote_plus (what urlencode does): "~" kept, "*" escaped, unlike Java's URLEncoder. */
    private fun form(v: String) = URLEncoder.encode(v, "UTF-8").replace("%7E", "~").replace("*", "%2A")
    /** Python's quote(safe="/") for one path segment. */
    private fun pathPart(v: String) = form(v).replace("+", "%20")

    // -- stations ----------------------------------------------------------------

    fun isIBroadcast(s: Station?): Boolean = s != null && (truthy(s["ibroadcast_urls"]) || s.kind == "ibroadcast")
    fun urls(s: Station): List<String> = (s["ibroadcast_urls"] as? JsonArray)?.mapNotNull { it.str() }.orEmpty()

    /** One queued track's names: (artist, title, album, cover), any of them "". */
    data class Track(val artist: String, val title: String, val album: String, val cover: String)

    fun track(s: Station, index: Int): Track? {
        val row = (s["ibroadcast_titles"] as? JsonArray)?.getOrNull(index) as? JsonArray ?: return null
        val f = (row.map { it.str().orEmpty() } + listOf("", "", "", "")).take(4)
        return Track(f[0], f[1], f[2], f[3])
    }

    /** bridge._shuffles: the station's own choice, else Settings' "shuffle_contents" (on). */
    fun shuffles(s: Station, settings: Settings?): Boolean = when {
        s["shuffle_contents"] != null -> truthy(s["shuffle_contents"])
        s["ibroadcast_shuffle"] != null -> truthy(s["ibroadcast_shuffle"])
        else -> settings?.bool("shuffle_contents", true) ?: true
    }

    /**
     * bridge._next_ibroadcast_url's choice of track: at random when the
     * station shuffles, else the one after [at] (-1 = the top), wrapping.
     */
    fun nextIndex(count: Int, at: Int, shuffle: Boolean, random: kotlin.random.Random = kotlin.random.Random): Int =
        if (count <= 0) -1 else if (shuffle) random.nextInt(count) else (at + 1).mod(count)

    /** bridge.retry_ibroadcast: in order, step back one so the refused track is tried again. */
    fun retryFrom(at: Int, shuffle: Boolean): Int = if (shuffle) at else maxOf(-1, at - 1)

    /** bridge._fresh_ibroadcast: a saved link signed for now, at the station's chosen rate. */
    fun fresh(url: String, station: Station, token: String, nowSeconds: Long = System.currentTimeMillis() / 1000): String {
        var out = refreshUrl(url, token, nowSeconds)
        val wanted = station["ibroadcast_bitrate"].str().orEmpty()
        if (wanted.isNotEmpty()) out = atBitrate(out, wanted)
        return out
    }

    // -- when iBroadcast says no (build 176) -------------------------------------------

    /** Tracks refused in a row before the station is marked down (a YouTube station's three). */
    const val TRACK_TRIES = 3

    /** A refusal the sign-in may cause: renew it and try the same track once more. Anything else: another track. */
    fun signInRefusal(code: Int): Boolean = code == 401 || code == 403

    /**
     * iBroadcast's own reason from an error reply, short and readable, or
     * "": a JSON "message" / "error_description" / "error", else the plain
     * text when it is one short line (never a web page).
     */
    fun said(body: String): String {
        val text = body.trim()
        if (text.isEmpty()) return ""
        if (text.startsWith("{")) {
            val obj = runCatching { FreqJson.parseToJsonElement(text) as? JsonObject }.getOrNull() ?: return ""
            for (key in listOf("message", "error_description", "error")) {
                obj[key].str()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it.take(140) }
            }
            return ""
        }
        if ('<' in text || '\n' in text || text.length > 140) return ""
        return text
    }

    /**
     * A refused track in plain words, with iBroadcast's own reason when it
     * gave one (the player's own words were only "The station's server
     * said no.").
     */
    fun refusal(code: Int, body: String = ""): String {
        val base = when {
            code == 401 || code == 403 -> "iBroadcast didn't accept this phone's sign-in for that track (HTTP $code). Sign in to iBroadcast again in Settings."
            code == 404 || code == 410 -> "iBroadcast couldn't find that track (HTTP $code)."
            code == 429 -> "iBroadcast says it's had too many requests (HTTP 429). Try again in a minute."
            code >= 500 -> "iBroadcast's streaming server had a problem (HTTP $code). Try again later."
            else -> "iBroadcast refused that track (HTTP $code)."
        }
        val reason = said(body)
        return if (reason.isEmpty()) base else "$base iBroadcast said: \u201c$reason\u201d"
    }

    /** A failed renewal ([IBroadcastAccount.Renewal.problem]) in plain words. */
    fun renewalProblem(problem: String): String = when {
        problem == "signed out" -> "You're signed out of iBroadcast. Sign in again in Settings."
        problem.startsWith("rejected") -> {
            val inner = problem.removePrefix("rejected").trim().removePrefix("(").removeSuffix(")").trim()
            // "HTTP 400 {json}" (the reply's body after the code) or the token reply's own "error".
            val reason = said(if (inner.startsWith("HTTP ")) inner.substringAfter(' ').substringAfter(' ', "") else inner)
            "iBroadcast ended this phone's sign-in" + (if (reason.isEmpty()) "" else " (\u201c$reason\u201d)") + ". Sign in to iBroadcast again in Settings."
        }
        problem.startsWith("HTTP ") -> "iBroadcast's sign-in server had a problem ($problem). Try again in a few minutes."
        else -> "Couldn't reach iBroadcast to renew the sign-in ($problem)."
    }

    /** One playlist the picker offers (the "Everything" choice is [LIBRARY_CHOICE]). */
    data class Playlist(val id: String, val name: String, val trackIds: List<String>)
    const val LIBRARY_CHOICE = "__library__"
    private val SKIPPED = setOf("most recent uploads", "recently played")

    /** The account and its library, read once for the picker. */
    class Account(val status: JsonObject, val library: JsonObject) {
        val settings: JsonObject? get() = status["settings"] as? JsonObject
        val userId: String
            get() = (status["user"] as? JsonObject)?.let { it["id"].str()?.ifEmpty { null } ?: it["user_id"].str() }.orEmpty()
        private val lib: JsonObject get() = (library["library"] as? JsonObject) ?: library
        val tracks by lazy { rows(lib["tracks"]) }
        val artists by lazy { rows(lib["artists"]) }
        val albums by lazy { rows(lib["albums"]) }
        /** The picker's playlists by name (the two automatic lists left out). */
        val playlists: List<Playlist> by lazy {
            rows(lib["playlists"]).mapNotNull { (id, row) ->
                val name = row["name"].str()?.ifEmpty { null } ?: "Playlist $id"
                if (name.trim().lowercase() in SKIPPED) return@mapNotNull null
                val ids = (row["tracks"] as? JsonArray)?.mapNotNull { it.str() ?: it.toString() }.orEmpty()
                Playlist(id, name, ids)
            }.sortedBy { it.name.lowercase() }
        }
    }

    /**
     * mainwindow._ib_library_read, step 3: a playlist (or everything, in a
     * random order) as a station: every track's signed link and its
     * (artist, title, album, cover), in the desktop's keys.
     */
    fun station(
        account: Account,
        playlistId: String,
        name: String,
        bitrate: String,
        shuffle: Boolean,
        token: String,
        newId: () -> String,
        now: Double,
        random: kotlin.random.Random = kotlin.random.Random,
    ): Station? {
        val byId = account.tracks
        val (ids, fallbackName) = if (playlistId == LIBRARY_CHOICE) byId.keys.shuffled(random) to "iBroadcast"
        else account.playlists.firstOrNull { it.id == playlistId }.let { (it?.trackIds ?: emptyList()) to (it?.name ?: "iBroadcast") }
        val urls = ArrayList<String>()
        val titles = ArrayList<JsonElement>()
        val nowSeconds = now.toLong()
        for (id in ids) {
            val track = byId[id] ?: continue
            val made = streamUrl(account.settings, track, token, account.userId, bitrate, nowSeconds)
            if (made.isEmpty()) continue
            urls += made
            titles.add(stringArray(
                listOf(
                    nameFor(account.artists, track["artist_id"]),
                    track["title"].str().orEmpty(),
                    nameFor(account.albums, track["album_id"]),
                    artworkUrl(account.settings, track, account.albums),
                ),
            ))
        }
        if (urls.isEmpty()) return null
        val d = LinkedHashMap<String, JsonElement>()
        d["name"] = JsonPrimitive(name.trim().ifEmpty { fallbackName })
        d["url"] = JsonPrimitive(urls[0])
        val s = Station.normalize(Station(d), now, newId)
        s["ibroadcast_urls"] = stringArray(urls)
        s["ibroadcast_titles"] = JsonArray(titles)
        s["ibroadcast_art_checked"] = JsonPrimitive(true)
        s["kind"] = JsonPrimitive("ibroadcast")
        s["ibroadcast_playlist"] = JsonPrimitive(playlistId)
        s["ibroadcast_shuffle"] = JsonPrimitive(shuffle || playlistId == LIBRARY_CHOICE)
        s["ibroadcast_bitrate"] = JsonPrimitive(bitrate)
        s["ibroadcast_cap"] = JsonPrimitive(false)
        return s
    }
}

/**
 * The iBroadcast sign-in on this phone (ibroadcast.json beside
 * settings.json, as on the desktop), and the calls that use it. The
 * file is swapped in whole: the renewal writes it on one thread while
 * the player reads it on another to sign each track.
 */
class IBroadcastAccount(
    private val file: File,
    private val http: OkHttpClient = OkHttpClient.Builder().connectTimeout(20, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build(),
    private val oauth: String = IBroadcast.OAUTH,
    private val api: String = IBroadcast.API,
    private val libraryHost: String = IBroadcast.LIBRARY,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    private val lock = Any()

    fun load(): JsonObject = (readJson(file) as? JsonObject) ?: JsonObject(emptyMap())

    private fun save(data: JsonObject) = synchronized(lock) {
        writeJsonAtomic(file, data)
        runCatching { file.setReadable(false, false); file.setReadable(true, true) }
    }

    /** ibroadcast.signed_in: a sign-in is saved (no network). */
    val signedIn: Boolean get() = load().let { it["access_token"].str().orEmpty().isNotEmpty() || it["refresh_token"].str().orEmpty().isNotEmpty() }

    /** ibroadcast.current_token: the saved access token, from the file only (signs a track as it starts). */
    fun currentToken(): String = load()["access_token"].str().orEmpty()

    /** ibroadcast.needs_refresh: no network; false when signed out or the expiry is unknown. */
    fun needsRefresh(margin: Long = IBroadcast.AHEAD_SECONDS): Boolean {
        val saved = load()
        if (saved["refresh_token"].str().isNullOrEmpty()) return false
        if (saved["access_token"].str().isNullOrEmpty()) return true
        val expires = saved["expires_at"].num() ?: 0.0
        if (expires == 0.0) return false
        return expires < clock() + margin
    }

    /** ibroadcast.store: keep the tokens and work out when the access one dies. */
    fun store(payload: JsonObject): JsonObject = synchronized(lock) {
        val data = LinkedHashMap<String, JsonElement>(load())
        for ((k, v) in payload) if (truthy(v)) data[k] = v
        payload["expires_in"].num()?.takeIf { it > 0 }?.let { data["expires_at"] = floatJson(clock() + it) }
        val out = JsonObject(data)
        save(out)
        out
    }

    fun clear() = synchronized(lock) { file.delete(); Unit }

    private fun postForm(path: String, fields: Map<String, String>): JsonObject {
        val body = FormBody.Builder().apply { fields.forEach { (k, v) -> add(k, v) } }.build()
        val request = Request.Builder().url(oauth + path).post(body).header("User-Agent", IBroadcast.USER_AGENT).build()
        http.newCall(request).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw HttpRefused(r.code, text.take(200))
            return FreqJson.parseToJsonElement(text) as? JsonObject ?: throw IBroadcast.IBroadcastError("not JSON")
        }
    }

    class HttpRefused(val code: Int, val body: String) : Exception("HTTP $code")

    /** ibroadcast.exchange_code: the sign-in's code for tokens, kept. */
    fun exchangeCode(code: String, verifier: String): JsonObject = store(
        postForm(
            "/token",
            mapOf(
                "grant_type" to "authorization_code", "code" to code, "client_id" to IBroadcast.CLIENT_ID,
                "redirect_uri" to IBroadcast.REDIRECT_URI, "code_verifier" to verifier,
            ),
        ),
    )

    /** What a renewal came to: the token ("" on failure), the problem ("" when fine), the new token's lifetime. */
    data class Renewal(val token: String, val problem: String, val lifetime: Long, val dead: Boolean = false)

    /**
     * ibroadcast.fresh_token: renew when the saved token is close to
     * running out, or [force]d after the streaming server refused it.
     * Network: never on the main thread.
     */
    fun freshToken(force: Boolean = false, margin: Long = IBroadcast.AHEAD_SECONDS): Renewal {
        val saved = load()
        val token = saved["access_token"].str().orEmpty()
        val expires = saved["expires_at"].num() ?: 0.0
        if (!force && token.isNotEmpty() && expires > clock() + margin) return Renewal(token, "", 0)
        val refresh = saved["refresh_token"].str().orEmpty()
        if (refresh.isEmpty()) return Renewal("", "signed out", 0)
        val payload = try {
            postForm("/token", mapOf("grant_type" to "refresh_token", "refresh_token" to refresh, "client_id" to IBroadcast.CLIENT_ID, "redirect_uri" to IBroadcast.REDIRECT_URI))
        } catch (e: HttpRefused) {
            // OAuth's "invalid_grant" (or a 401): the refresh token itself is no good, only a new sign-in helps.
            val dead = e.code == 401 || (e.code == 400 && "invalid_grant" in e.body)
            return if (e.code in setOf(400, 401, 403)) Renewal("", "rejected (HTTP ${e.code} ${e.body.trim()})", 0, dead) else Renewal("", "HTTP ${e.code}", 0)
        } catch (e: Exception) {
            return Renewal("", e.message ?: e.javaClass.simpleName, 0)
        }
        val fresh = payload["access_token"].str().orEmpty()
        if (fresh.isEmpty()) return Renewal("", "rejected (${payload["error"].str() ?: "no token came back"})", 0, payload["error"].str() == "invalid_grant")
        store(payload)
        return Renewal(fresh, "", payload["expires_in"].num()?.toLong() ?: 0)
    }

    /** ibroadcast.revoke, then forget it here. Signing out always works. */
    fun signOut() {
        val refresh = load()["refresh_token"].str().orEmpty()
        if (refresh.isNotEmpty()) runCatching { postForm("/revoke", mapOf("refresh_token" to refresh, "client_id" to IBroadcast.CLIENT_ID)) }
        clear()
    }

    /** ibroadcast.call: one JSON API request; throws when the token is no longer good. */
    fun call(token: String, mode: String, host: String = api, extra: Map<String, JsonElement> = emptyMap()): JsonObject {
        val body = buildJsonObject {
            put("client", IBroadcast.CLIENT); put("version", IBroadcast.VERSION)
            put("device_name", "Frequencies"); put("user_agent", IBroadcast.USER_AGENT); put("mode", mode)
            extra.forEach { (k, v) -> put(k, v) }
        }
        val request = Request.Builder().url(host)
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .header("User-Agent", IBroadcast.USER_AGENT)
            .header("Authorization", "Bearer $token")
            .build()
        http.newCall(request).execute().use { r ->
            if (!r.isSuccessful) throw HttpRefused(r.code, "")
            val payload = FreqJson.parseToJsonElement(r.body!!.string()) as? JsonObject ?: throw IBroadcast.IBroadcastError("not JSON")
            if (payload["authenticated"].let { it is JsonPrimitive && !it.isString && it.content == "false" }) throw IBroadcast.IBroadcastError("token expired")
            if (payload["result"].let { it is JsonPrimitive && !it.isString && it.content == "false" }) throw IBroadcast.IBroadcastError(payload["message"].str() ?: "request failed")
            return payload
        }
    }

    /** The account and its whole library (two hosts, as iBroadcast's docs say), with a token fresh enough to sign every track. */
    fun readAccount(): Pair<IBroadcast.Account, String> {
        val renewal = freshToken()
        if (renewal.token.isEmpty()) throw IBroadcast.IBroadcastError(if (renewal.problem == "signed out") "Sign in to iBroadcast first." else "iBroadcast didn't renew the sign-in: ${renewal.problem}")
        val status = call(renewal.token, "status")
        val library = call(renewal.token, "library", host = libraryHost)
        return IBroadcast.Account(status, library) to renewal.token
    }

    /** ibroadcast.set_bitrate_pref: a nicety; every failure swallowed. */
    fun setBitratePref(token: String, bitrate: String) {
        val n = bitrate.toIntOrNull() ?: return
        runCatching { call(token, "setbitratepref", extra = mapOf("bitrate" to JsonPrimitive(n))) }
    }
}

/**
 * ibroadcast.RedirectCatcher: a one-request web server on the loopback
 * address, open only while a sign-in is in progress. The browser lands
 * on it with ?code=…&state=…; it answers with a short page and stops.
 */
class RedirectCatcher(port: Int = IBroadcast.REDIRECT_PORT) : AutoCloseable {
    data class Answer(val code: String, val state: String, val error: String)

    /** Room for the browser's spare connections beside the real one. */
    private val server = ServerSocket(port, 16, InetAddress.getByName("127.0.0.1"))
    val port: Int get() = server.localPort

    /** What the browser asked for, for the log: "/callback (code, state)" and the like. */
    @Volatile var lastSeen: String = ""
        private set

    /**
     * Wait up to [seconds] for the browser; null when nothing came (or
     * [close] was called).
     *
     * A browser opens spare connections ahead of time (a preconnect) that
     * may stay silent for as long as the sign-in page is open, and asks
     * for /favicon.ico besides. Each connection is read on a thread of its
     * own, so a silent one never holds up the real answer, and a spare one
     * going quiet or away is no error. (Before build 168 one silent spare
     * connection ended the wait with an error after 5 s: the sign-in never
     * finished, or the app closed.)
     */
    fun await(seconds: Int = 180, page: String = PAGE): Answer? {
        val until = System.currentTimeMillis() + seconds * 1000L
        val result = java.util.concurrent.atomic.AtomicReference<Answer?>(null)
        val done = java.util.concurrent.CountDownLatch(1)
        val open = java.util.Collections.synchronizedSet(HashSet<java.net.Socket>())
        val acceptor = Thread({
            try {
                while (done.count > 0) {
                    val socket = server.accept()
                    open.add(socket)
                    Thread({
                        try {
                            socket.use { s ->
                                s.soTimeout = (until - System.currentTimeMillis()).coerceIn(1, Int.MAX_VALUE.toLong()).toInt()
                                serve(s, page)?.let { if (result.compareAndSet(null, it)) done.countDown() }
                            }
                        } catch (e: Exception) {
                            // A spare connection that said nothing, or went away: keep waiting.
                        } finally {
                            open.remove(socket)
                        }
                    }, "ibroadcast-redirect-conn").apply { isDaemon = true }.start()
                }
            } catch (e: Exception) {
                // Closed (the answer came, the wait ran out, or the sign-in was canceled).
            } finally {
                done.countDown()
            }
        }, "ibroadcast-redirect").apply { isDaemon = true }
        acceptor.start()
        try {
            done.await((until - System.currentTimeMillis()).coerceAtLeast(0), java.util.concurrent.TimeUnit.MILLISECONDS)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
        } finally {
            close()
            synchronized(open) { open.toList() }.forEach { runCatching { it.close() } }
        }
        return result.get()
    }

    /** One connection: the answer when it was the redirect, else null. */
    private fun serve(s: java.net.Socket, page: String): Answer? {
        val reader = s.getInputStream().bufferedReader(Charsets.ISO_8859_1)
        val first = reader.readLine() ?: return null
        while (reader.readLine()?.isNotEmpty() == true) { /* headers */ }
        val path = first.split(' ').getOrNull(1).orEmpty()
        val fields = query(path)
        lastSeen = path.substringBefore('?') + if (fields.isEmpty()) "" else " (" + fields.keys.joinToString(", ") + ")"
        // A browser's own request for /favicon.ico isn't the answer.
        val isAnswer = path.startsWith("/callback")
        val body = (if (isAnswer) page else "").toByteArray(Charsets.UTF_8)
        val status = if (isAnswer) "200 OK" else "404 Not Found"
        s.getOutputStream().apply {
            write("HTTP/1.1 $status\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ${body.size}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".toByteArray(Charsets.ISO_8859_1))
            write(body)
            flush()
        }
        if (!isAnswer) return null
        return Answer(fields["code"].orEmpty(), fields["state"].orEmpty(), fields["error"].orEmpty())
    }

    override fun close() { runCatching { server.close() } }

    companion object {
        /** The redirect's query as fields ("+" and %XX decoded, as a browser sends them). */
        fun query(path: String): Map<String, String> {
            val q = path.substringAfter('?', "").substringBefore('#')
            if (q.isEmpty()) return emptyMap()
            return q.split('&').filter { it.isNotEmpty() }.associate {
                val k = it.substringBefore('=')
                val v = if ('=' in it) it.substringAfter('=') else ""
                fun dec(x: String) = runCatching { URLDecoder.decode(x, "UTF-8") }.getOrDefault(x)
                dec(k) to dec(v)
            }
        }

        /** Signed in: back to the app with its link (or the tab's close button). */
        const val PAGE = "<!doctype html><html><head><meta name=viewport content=\"width=device-width,initial-scale=1\">" +
            "<title>Signed in</title></head><body style=\"font-family:sans-serif;background:#131315;color:#ececee;padding:2em\">" +
            "<h2>Signed in.</h2><p>Close this tab to go back to Frequencies.</p>" +
            "<p><a style=\"color:#9ecaff\" href=\"frequencies://signed-in\">Back to Frequencies</a></p></body></html>"
    }
}
