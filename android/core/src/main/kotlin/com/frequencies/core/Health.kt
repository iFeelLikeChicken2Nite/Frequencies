package com.frequencies.core

import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * health.json and the rules around it, as the desktop has them
 * (storage.py's health functions, workers.probe_stream / LaunchCheck,
 * desktop._start_launch_check / _down_targets, bridge._step_past_down).
 *
 * health[station_id] = {last_ok, last_check, fail_count, down, last_error};
 * keys the desktop adds later are kept as they are. "down" is sticky: it
 * stays until the station is reached again.
 */
object Health {
    /** If the first eight probes all fail, the phone is most likely offline: nothing is marked. */
    const val OFFLINE_AFTER = 8
    /** Probes at a time in the launch sweep. */
    const val PARALLEL = 4
    /**
     * workers.PROBE_TIMEOUT since desktop build 204: a probe gives a stream
     * the time the player gives it to connect and to send its first byte
     * (one constant, [Playlists.CONNECT_TIMEOUT_MS]). Until Android build
     * 205 this was 3 s / 5 s: servers that take a few seconds to send their
     * first byte (every StreamTheWorld station, CJLM among them, waits
     * about 5.5 s) played fine but every check marked them down.
     */
    const val PROBE_TIMEOUT_MS = Playlists.CONNECT_TIMEOUT_MS
    /** One more try, this long after a failure, before a station is called down (never after an HTTP 4xx). */
    const val RETRY_PAUSE_MS = 2_000L
    /** A .pls / .m3u: how many of its streams are tried, and how deep playlists may nest. */
    const val LISTED_MAX = 4
    const val NEST_MAX = 2
    /** What a server calls a playlist file (an HLS manifest is told apart by its text). */
    val PLAYLIST_TYPES = setOf(
        "audio/x-scpls", "audio/scpls", "application/pls+xml",
        "audio/x-mpegurl", "audio/mpegurl", "application/x-mpegurl", "application/vnd.apple.mpegurl",
    )
    private const val PLAYLIST_BYTES = 64L * 1024

    fun blank(): LinkedHashMap<String, JsonElement> = linkedMapOf(
        "last_ok" to JsonNull,
        "last_check" to JsonNull,
        "fail_count" to JsonPrimitive(0),
        "down" to JsonPrimitive(false),
        "last_error" to JsonPrimitive(""),
    )

    /** storage.health_for, read only: the entry with the blank fields filled in. */
    fun entry(health: JsonObject, id: String?): JsonObject {
        val have = id?.let { health[it] } as? JsonObject
        return JsonObject(blank().apply { if (have != null) putAll(have) })
    }

    fun isDown(health: JsonObject, id: String?): Boolean = id != null && truthy((health[id] as? JsonObject)?.get("down"))

    private fun change(health: JsonObject, id: String, edit: (LinkedHashMap<String, JsonElement>) -> Unit): JsonObject {
        val e = LinkedHashMap(entry(health, id)).also(edit)
        return JsonObject(LinkedHashMap(health).apply { put(id, JsonObject(e)) })
    }

    /** storage.mark_ok: reached; the down mark and the failures go. */
    fun markOk(health: JsonObject, id: String, now: Double): JsonObject = change(health, id) {
        it["last_ok"] = floatJson(now)
        it["last_check"] = floatJson(now)
        it["fail_count"] = JsonPrimitive(0)
        it["down"] = JsonPrimitive(false)
        it["last_error"] = JsonPrimitive("")
    }

    /** storage.mark_failed: one more failure; down once [downAfter] are in a row (the app always uses 1). */
    fun markFailed(health: JsonObject, id: String, reason: String, now: Double, downAfter: Int = 1): JsonObject = change(health, id) {
        it["last_check"] = floatJson(now)
        val count = (it["fail_count"].num() ?: 0.0).toLong() + 1
        it["fail_count"] = JsonPrimitive(count)
        it["last_error"] = JsonPrimitive(reason.ifEmpty { "unreachable" })
        if (count >= downAfter) it["down"] = JsonPrimitive(true)
    }

    /** Whether marking would change anything (connecting clears a mark only when there is one). */
    fun needsClearing(health: JsonObject, id: String): Boolean {
        val e = entry(health, id)
        return truthy(e["down"]) || (e["fail_count"].num() ?: 0.0) > 0
    }

    /** storage.humanize_age: '3m', '5h', '2d'. */
    fun humanizeAge(seconds: Double?): String {
        if (seconds == null) return "never"
        val s = maxOf(0L, seconds.toLong())
        return when {
            s < 60 -> "${s}s"
            s < 3600 -> "${s / 60}m"
            s < 86400 -> "${s / 3600}h"
            else -> "${s / 86400}d"
        }
    }

    /** storage.describe_health: the row's second line, or "" when it's fine. */
    fun describe(health: JsonObject, id: String?, now: Double): String {
        val e = (id?.let { health[it] } as? JsonObject) ?: return ""
        if (!truthy(e["down"])) return ""
        val lastOk = e["last_ok"].num() ?: return "never reached"
        return "down ${humanizeAge(now - lastOk)}"
    }

    fun isIbroadcast(s: Station): Boolean = truthy(s["ibroadcast_urls"]) || s.kind == "ibroadcast"
    fun isTwitch(s: Station): Boolean = s.kind == "twitch" || truthy(s["twitch_channel"])
    fun isYoutubeUrl(url: String): Boolean {
        val host = runCatching { java.net.URI(url.trim()).host }.getOrNull()?.lowercase().orEmpty()
        return host == "youtu.be" || host.endsWith("youtube.com")
    }

    /** storage.skipped_when_down: never an iBroadcast station (the user's own library; task 156). */
    fun skippedWhenDown(s: Station?): Boolean = s == null || !isIbroadcast(s)

    /**
     * bridge.checkable: a Twitch channel is judged by its own watcher, and
     * an iBroadcast station only with a sign-in behind it. The phone
     * signs each iBroadcast track as it plays (AP10b), so playing it is the
     * check; the background sweep never probes one.
     */
    fun checkable(s: Station): Boolean = !isTwitch(s) && !isIbroadcast(s)

    /** What a launch sweep probes, and the stations whose stale mark it clears. */
    data class Sweep(val targets: List<Pair<String, String>>, val cleared: List<String>)

    /**
     * desktop._start_launch_check: every saved stream, except local files
     * and YouTube; an iBroadcast or YouTube address is never probed (a
     * plain fetch of it proves nothing), and a "down" an older check left
     * on one is cleared unless it went down for its sign-in.
     */
    fun launchTargets(stations: List<Station>, health: JsonObject): Sweep {
        val targets = ArrayList<Pair<String, String>>()
        val cleared = ArrayList<String>()
        for (s in stations) {
            if (s.kind == "local" || s.kind == "youtube") continue
            if (isIbroadcast(s) || isYoutubeUrl(s.url)) {
                val e = entry(health, s.id)
                val why = e["last_error"].str().orEmpty().lowercase()
                if (truthy(e["down"]) && !why.startsWith("signed out") && !why.startsWith("not signed in")) cleared += s.id
                continue
            }
            if (!checkable(s)) continue
            if (s.url.startsWith("http")) targets += s.id to s.url
        }
        return Sweep(targets, cleared)
    }

    /** desktop._down_targets: what the background re-check looks at (only stations marked down). */
    fun downTargets(stations: List<Station>, health: JsonObject): List<Pair<String, String>> =
        stations.filter { checkable(it) && isDown(health, it.id) && it.url.startsWith("http") && !isYoutubeUrl(it.url) && it.kind !in setOf("local", "youtube") }
            .map { it.id to it.url }

    // -- probing -------------------------------------------------------------

    internal val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .readTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .callTimeout(2 * PROBE_TIMEOUT_MS + 4_000, TimeUnit.MILLISECONDS)
            .build()
    }

    private val insecureClient: OkHttpClient by lazy { StationTls.insecureCopy(client) }

    /** The URLs of the stations flagged `insecure` (broken certificates): only their probes skip the check. */
    fun insecureUrls(stations: List<Station>): Set<String> = stations.filter { it.insecure }.map { it.url }.toSet()

    /**
     * workers.probe_stream (desktop build 204): ask a station what the
     * player asks it, does sound start in time? Opens the stream and waits
     * for real bytes (a HEAD isn't enough: plenty of servers answer it while
     * the mount is dead). A .pls / .m3u is opened the way the player opens
     * it: up only when one of the streams it lists sends audio, not when the
     * little text file itself arrives. A failure is tried once more after
     * [RETRY_PAUSE_MS], except an HTTP 4xx (the server's own answer).
     * Returns (ok, reason); the reasons use the desktop's words.
     */
    fun probe(
        url: String,
        insecure: Boolean = false,
        http: OkHttpClient = if (insecure) insecureClient else client,
        retry: Boolean = true,
        log: (String) -> Unit = {},
    ): Pair<Boolean, String> {
        val (ok, reason) = probeOnce(url, http, 0)
        if (ok || !retry || reason.startsWith("HTTP 4")) return ok to reason
        Thread.sleep(RETRY_PAUSE_MS)
        val (again, why) = probeOnce(url, http, 0)
        if (again) {
            log("[health] ${url.take(80)}: answered on the second try (first: $reason)")
            return true to ""
        }
        return false to why.ifEmpty { reason }
    }

    /** A playlist file by what the server sent: its type or its first line (desktop workers._looks_like_playlist). */
    private fun saysPlaylist(contentType: String?, head: String): Boolean {
        if ("#EXT-X-" in head) return false   // an HLS manifest: the player plays it
        val kind = contentType.orEmpty().substringBefore(';').trim().lowercase()
        val start = head.trimStart().take(16).lowercase()
        return kind in PLAYLIST_TYPES || listOf("[playlist]", "#extm3u", "http://", "https://").any { start.startsWith(it) }
    }

    /** One try at one address. */
    private fun probeOnce(url: String, http: OkHttpClient, depth: Int): Pair<Boolean, String> {
        val request = try {
            Request.Builder().url(url)
                .header("User-Agent", Playlists.STREAM_AGENT)
                .header("Icy-MetaData", "1")
                .build()
        } catch (e: IllegalArgumentException) {
            return false to "InvalidURL"
        }
        // The streams a playlist lists, read with its own call closed before they are tried.
        val listed = try {
            http.newCall(request).execute().use { response ->
                if (response.code >= 400) return false to "HTTP ${response.code}"
                val source = response.body?.source() ?: return false to "no audio data"
                if (!source.request(1)) return false to "no audio data"
                // Enough to see a playlist's first line; audio is already flowing, so this is quick.
                runCatching { source.request(16) }
                val head = source.buffer.clone().readUtf8(minOf(source.buffer.size, 2048L))
                val said = saysPlaylist(response.header("Content-Type"), head)
                // Or only its name says so, which is how the player picks what to open as a playlist (Playlists.resolve).
                val named = "#EXT-X-" !in head && Playlists.looksLikePlaylist(url)
                if (depth >= NEST_MAX || !(said || named)) return true to ""
                // The whole (small) file, as Playlists.resolve reads it.
                source.request(PLAYLIST_BYTES)
                val text = source.buffer.clone().readUtf8(minOf(source.buffer.size, PLAYLIST_BYTES))
                if ("#EXT-X-" in text) return true to ""
                val listed = Playlists.streamsIn(text).take(LISTED_MAX)
                // Named a playlist but lists nothing: the player plays the address as it is (resolve fails open).
                if (listed.isEmpty()) return if (said) false to "playlist lists no streams" else true to ""
                listed
            }
        } catch (e: IOException) {
            return false to reasonFor(e)
        }
        var why = ""
        for (stream in listed) {
            val (ok, reason) = probeOnce(stream, http, depth + 1)
            if (ok) return true to ""
            why = reason
        }
        return false to "no listed stream answered ($why)"
    }

    /** requests' exception names, which is what the desktop writes in last_error. */
    fun reasonFor(e: IOException): String = when {
        e is SocketTimeoutException && (e.message.orEmpty().contains("connect", ignoreCase = true)) -> "ConnectTimeout"
        e is SocketTimeoutException -> "ReadTimeout"
        e is InterruptedIOException -> "ReadTimeout"
        e is SSLException -> "SSLError"
        e is UnknownHostException || e is ConnectException -> "ConnectionError"
        else -> "ConnectionError"
    }

    // -- the launch sweep ------------------------------------------------------

    data class Summary(val checked: Int, val answered: Int, val offline: Boolean, val stopped: Boolean)

    /**
     * workers.LaunchCheck: four probes at a time, results in list order.
     * Failures are held back until one station answers (none answering:
     * none reported); if the first [OFFLINE_AFTER] all fail the phone is
     * taken to be offline and the sweep ends there. [stopped] ends it early (the app or the job is going).
     */
    fun sweep(
        targets: List<Pair<String, String>>,
        probe: (String) -> Pair<Boolean, String> = { Health.probe(it) },
        log: (String) -> Unit = {},
        stopped: () -> Boolean = { false },
        onResult: (id: String, ok: Boolean, reason: String) -> Unit,
    ): Summary {
        log("[health] launch check: started, ${targets.size} saved stream(s) to check")
        val pool = Executors.newFixedThreadPool(PARALLEL) { r -> Thread(r, "health-probe").apply { isDaemon = true } }
        try {
            val futures: List<Future<Pair<Boolean, String>?>> = targets.map { (_, url) ->
                pool.submit(Callable {
                    if (stopped()) null else try {
                        probe(url)
                    } catch (e: Exception) {
                        // One odd answer must not end the whole sweep.
                        false to (e::class.simpleName ?: "error")
                    }
                })
            }
            val held = ArrayList<Triple<String, Boolean, String>>()
            var seenOk = false
            var fails = 0
            var answered = 0
            var checked = 0
            for ((i, future) in futures.withIndex()) {
                val result = try { future.get() } catch (e: Exception) { null }
                if (stopped() || result == null) {
                    log("[health] launch check: stopped")
                    return Summary(checked, answered, offline = false, stopped = true)
                }
                val id = targets[i].first
                val (ok, reason) = result
                checked++
                if (ok) answered++
                if (!seenOk) {
                    if (ok) {
                        seenOk = true
                        held.forEach { onResult(it.first, it.second, it.third) }
                        held.clear()
                        onResult(id, true, reason)
                        continue
                    }
                    fails++
                    held += Triple(id, false, reason)
                    if (fails >= OFFLINE_AFTER && held.size >= fails) {
                        log("[health] launch check: ended, the first $fails streams all failed - looks offline, nothing marked")
                        return Summary(checked, answered, offline = true, stopped = false)
                    }
                    continue
                }
                onResult(id, ok, reason)
            }
            // As on the desktop, failures still held (no station answered at all) are never reported.
            log("[health] launch check: ended, $checked checked, $answered answered, ${checked - answered} no answer")
            return Summary(checked, answered, offline = false, stopped = false)
        } finally {
            pool.shutdownNow()
        }
    }

    // -- next and previous ------------------------------------------------------

    /** Where Next or Previous lands, and the frequencies passed over on the way. */
    data class Step(val index: Int?, val passed: List<Int>)

    /**
     * bridge._step_past_down: the next index in [step]'s direction (+1 or
     * -1, wrapping) whose station isn't marked down. One lap at most: back
     * where it started (or all the way round when nothing is playing)
     * means every other one is down, and then nothing moves (index null).
     */
    fun stepPastDown(count: Int, here: Int?, step: Int, down: (Int) -> Boolean): Step {
        if (count <= 0) return Step(null, emptyList())
        val pos = here?.takeIf { it in 0 until count } ?: if (step > 0) -1 else count
        val passed = ArrayList<Int>()
        for (k in 1..count) {
            val candidate = Math.floorMod(pos + k * step, count)
            if (candidate == here) break
            if (down(candidate)) { passed += candidate; continue }
            return Step(candidate, passed)
        }
        return Step(null, passed)
    }

    /** bridge._skip_log's line. */
    fun skipLog(button: String, passed: List<String>, landed: String?): String {
        var shown = passed.take(8).joinToString(", ") { "'$it'" }
        if (passed.size > 8) shown += " and ${passed.size - 8} more"
        return if (landed == null) "[skip] $button: no station up - passed ${passed.size} marked down ($shown); stayed put"
        else "[skip] $button: passed ${passed.size} marked down ($shown) -> '$landed'"
    }

    /** What the user is told when nothing else is up (bridge.on_notice). */
    fun allDownNotice(button: String, nothingPlaying: Boolean): String =
        if (nothingPlaying) "Every station is marked down, so $button did nothing."
        else "Every other station is marked down, so $button stayed where it was."
}
