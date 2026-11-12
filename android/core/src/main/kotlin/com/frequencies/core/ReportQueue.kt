package com.frequencies.core

import java.io.File
import java.util.Date
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * What the thumbs down was pressed on (desktop build 152, task 215): taken
 * at the press, so whatever plays next can't change or cancel it.
 */
data class ThumbsSnapshot(
    val artist: String,
    val source: String,
    val bio: String,
    /** The photo showing (a file or an address), as ArtistPanel.photo. */
    val photo: String,
    val station: String,
) {
    companion object {
        fun of(panel: ArtistPanel, station: String): ThumbsSnapshot =
            ThumbsSnapshot(panel.name, panel.bioSource, panel.bio, panel.photo.orEmpty(), station)
    }
}

/**
 * photopack.ReportQueue (desktop build 152): thumbs-down reports waiting to
 * go out, in a file, so a station change, a slow network, the app being
 * closed or the phone restarting can't lose one. A report leaves the queue
 * only when the report Worker took it (the artist is marked as reported
 * then, not before) or refused it for good (the browser issue is the way
 * then). The phone adds one thing: a report waits out the fifteen-second
 * change of mind in the file ([HOLD_SECONDS], held until `hold_until`), so
 * a thumbs up on the same artist in that time takes it back and nothing
 * is lost if the app goes meanwhile.
 *
 * Plain file work, no Android: the app sends from a WorkManager job.
 * Not thread-safe by itself; the app calls it under one lock.
 */
class ReportQueue(private val file: File, private val now: () -> Long = { System.currentTimeMillis() / 1000 }) {

    sealed interface Added {
        /** In the file; it goes out once its hold is over. */
        data object Queued : Added
        /** That artist was reported today, or is waiting already. */
        data object Already : Added
        /** This build has no report address at all. */
        data object Nowhere : Added
        /** No report Worker in this build: the pre-filled issue opens in the browser (the artist is marked). */
        data class Browser(val address: String) : Added
    }

    sealed interface Sent {
        /** Nothing waiting. */
        data object Empty : Sent
        /** The first report is still in its hold; try again at [atSeconds]. */
        data class Held(val atSeconds: Long) : Sent
        /** The Worker filed it (or it was filed today already); the artist is marked. */
        data class Filed(val artist: String, val text: String) : Sent
        /** The Worker said no for good: open [address] (the pre-filled issue; empty with no repository). The artist is marked. */
        data class GaveUp(val artist: String, val why: String, val address: String) : Sent
        /** Not sent this time (offline, a server error): it stays, try again later. */
        data class Retry(val artist: String, val why: String) : Sent
        /** This build can't send (no Worker address); the reports stay. */
        data object NoService : Sent
    }

    fun items(): List<JsonObject> = runCatching {
        (FreqJson.parseToJsonElement(file.readText()) as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
    }.getOrDefault(emptyList())

    private fun write(items: List<JsonObject>) {
        file.parentFile?.mkdirs()
        writeJsonAtomic(file, JsonArray(items))
    }

    fun waiting(artist: String): Boolean = items().any { it["artist"].str().orEmpty().equals(artist, ignoreCase = true) }

    /**
     * photopack.ReportQueue.add: the report, from the snapshot. [what] is
     * "bio", "photo" or "both"; [photoLink] is ThumbsDown.photoLink's words
     * for the photo shown ("" when the photo isn't part of it).
     */
    fun add(
        settings: Settings, snap: ThumbsSnapshot, what: String, photoLink: String,
        service: String, repo: String, build: String,
        hold: Long = HOLD_SECONDS, date: Date = Date(now() * 1000),
    ): Added {
        if (repo.isEmpty() && service.isEmpty()) return Added.Nowhere
        if (!ThumbsDown.due(settings, snap.artist, date) || waiting(snap.artist)) return Added.Already
        val (title, body) = ThumbsDown.issueText(snap.artist, snap.source, snap.bio, what, photoLink, date)
        if (service.isEmpty()) {
            ThumbsDown.markSent(settings, snap.artist, date)
            return Added.Browser(if (repo.isEmpty()) "" else ThumbsDown.issueAddress(repo, title, body))
        }
        val at = now()
        val item = JsonObject(linkedMapOf(
            "artist" to JsonPrimitive(snap.artist),
            "title" to JsonPrimitive(title),
            "body" to JsonPrimitive(body),
            "fields" to ReportPoster.fields(snap.artist, snap.source, snap.bio, what, photoLink, snap.station, build),
            "what" to JsonPrimitive(what),
            "source" to JsonPrimitive(snap.source),
            "queued" to JsonPrimitive(at),
            "hold_until" to JsonPrimitive(at + hold),
        ))
        write(items() + item)
        return Added.Queued
    }

    /**
     * A thumbs up (or the thumb taken back) on [artist] while its report is
     * still in its hold: the report goes. Returns true if one was taken back.
     */
    fun takeBack(artist: String): Boolean {
        val all = items()
        val t = now()
        val rest = all.filterNot {
            it["artist"].str().orEmpty().equals(artist, ignoreCase = true) && (it["hold_until"].num()?.toLong() ?: 0) > t
        }
        if (rest.size == all.size) return false
        write(rest)
        return true
    }

    /** A thumbs down pressed on another artist: the earlier one's hold ends now (mainwindow._artist_verdict). */
    fun releaseHolds() {
        val t = now()
        val all = items()
        if (all.none { (it["hold_until"].num()?.toLong() ?: 0) > t }) return
        write(all.map { if ((it["hold_until"].num()?.toLong() ?: 0) > t) JsonObject(it + ("hold_until" to JsonPrimitive(t))) else it })
    }

    /** When the first report's hold is over, or null with nothing waiting. */
    fun nextDue(): Long? = items().minOfOrNull { it["hold_until"].num()?.toLong() ?: 0 }

    /**
     * photopack.ReportQueue.kick + _answered: send the first report whose
     * hold is over through [send] (ReportPoster.send to the Worker). The
     * caller sends again while the answer is Filed or GaveUp, and retries
     * later on Retry (WorkManager's backoff, the desktop's 30 s to 30 min).
     */
    fun sendNext(settings: Settings, service: String, repo: String, send: (JsonObject) -> ReportPoster.Result): Sent {
        val t = now()
        val all = items()
        val kept = all.filter { (it["queued"].num()?.toLong() ?: 0) >= t - KEEP_DAYS * 86_400 }
        if (kept.size != all.size) write(kept)
        if (kept.isEmpty()) return Sent.Empty
        if (service.isEmpty()) return Sent.NoService
        val item = kept.filter { (it["hold_until"].num()?.toLong() ?: 0) <= t }.firstOrNull()
            ?: return Sent.Held(kept.minOf { it["hold_until"].num()?.toLong() ?: 0 })
        val artist = item["artist"].str().orEmpty()
        val fields = item["fields"] as? JsonObject ?: JsonObject(emptyMap())
        fun drop() = write(items().filter { it != item })
        return when (val r = send(fields)) {
            is ReportPoster.Result.Filed -> { drop(); ThumbsDown.markSent(settings, artist, Date(t * 1000)); Sent.Filed(artist, r.url) }
            ReportPoster.Result.Already -> { drop(); ThumbsDown.markSent(settings, artist, Date(t * 1000)); Sent.Filed(artist, "already reported today") }
            is ReportPoster.Result.Failed -> if (r.permanent) {
                drop()
                ThumbsDown.markSent(settings, artist, Date(t * 1000))
                val address = if (repo.isEmpty()) "" else ThumbsDown.issueAddress(repo, item["title"].str().orEmpty(), item["body"].str().orEmpty())
                Sent.GaveUp(artist, r.why, address)
            } else Sent.Retry(artist, r.why)
        }
    }

    companion object {
        /** photopack.QUEUE_FILE */
        const val FILE = "report-queue.json"
        /** photopack.QUEUE_KEEP_DAYS */
        const val KEEP_DAYS = 7L
        /** mainwindow._artist_verdict: the first press is often a slip. */
        const val HOLD_SECONDS = 15L
    }
}
