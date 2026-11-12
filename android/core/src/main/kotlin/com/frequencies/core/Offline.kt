package com.frequencies.core

import kotlin.random.Random
import kotlinx.serialization.json.JsonPrimitive

/**
 * Local files stations (the desktop's kind "local"): a folder of the
 * user's own music played like a station, a random song joined partway
 * through. On the desktop `local_path` is the folder; a station added on
 * the phone also keeps the folder Android let the app read in
 * [ANDROID_FOLDER] (a content:// link), and `local_path` holds the same
 * folder as a path, so a backup that visits the desktop says which folder
 * it was. A station from the desktop whose folder isn't on this phone
 * stays exactly as it was ("not on this phone") until a folder is chosen.
 */
object LocalStations {
    /** The phone's own key: the folder Android gave the app (DocumentsContract tree link). */
    const val ANDROID_FOLDER = "android_folder"

    /** storage.AUDIO_EXTENSIONS */
    val AUDIO_EXTENSIONS = listOf(
        ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac",
        ".wav", ".wma", ".mp4", ".alac", ".aiff", ".aif",
    )

    fun isAudio(name: String): Boolean = AUDIO_EXTENSIONS.any { name.lowercase().endsWith(it) }

    /** storage.is_local_station */
    fun isLocal(s: Station?): Boolean = s?.kind == "local"

    fun localPath(s: Station): String = s["local_path"].str().orEmpty()
    fun androidFolder(s: Station): String = s[ANDROID_FOLDER].str().orEmpty()

    /**
     * A new local files station, the desktop's way (mainwindow
     * _finish_custom_station): kind "local", the folder, and
     * "local://<folder>" as its address (so it has one, and two stations
     * of the same folder are one).
     */
    fun create(name: String, path: String, androidFolder: String, now: Double, newId: () -> String): Station {
        val s = Station(linkedMapOf<String, kotlinx.serialization.json.JsonElement>(
            "name" to JsonPrimitive(name.ifBlank { "Local station" }),
            "kind" to JsonPrimitive("local"),
            "local_path" to JsonPrimitive(path),
            ANDROID_FOLDER to JsonPrimitive(androidFolder),
            "url" to JsonPrimitive("local://$path"),
            "art_source" to JsonPrimitive("auto"),
        ))
        return Station.normalize(s, now, newId)
    }

    /**
     * Where an Android folder link points, as a path: the tree document id
     * "primary:Music/Lo-fi" is /storage/emulated/0/Music/Lo-fi; another
     * volume ("1A2B-3C4D:Albums") is /storage/1A2B-3C4D/Albums.
     */
    fun pathOfTreeId(docId: String): String {
        val volume = docId.substringBefore(':', "")
        val rest = docId.substringAfter(':', docId).trim('/')
        val root = if (volume.isEmpty() || volume == "primary") "/storage/emulated/0" else "/storage/$volume"
        return if (rest.isEmpty()) root else "$root/$rest"
    }

    /** The folder's own name, for a new station's name. */
    fun folderName(path: String): String = path.trimEnd('/').substringAfterLast('/').ifEmpty { "Local station" }

    /** storage.pick_local_track: a random track, avoiding an immediate repeat where possible. */
    fun <T> pick(tracks: List<T>, exclude: T?, rng: Random = Random.Default): T? {
        if (tracks.isEmpty()) return null
        val pool = if (exclude != null && tracks.size > 1) tracks.filter { it != exclude }.ifEmpty { tracks } else tracks
        return pool[rng.nextInt(pool.size)]
    }

    /** bridge._source_for: tuning in joins the song 5-60% of the way through, so it sounds already on air. */
    fun joinFraction(rng: Random = Random.Default): Double = 0.05 + rng.nextDouble() * 0.55

    /** bridge.subtitle: "Local · <folder>". */
    fun subtitle(s: Station): String {
        val path = localPath(s).trimEnd('/')
        return "Local · " + (path.substringAfterLast('/').ifEmpty { path })
    }
}

/**
 * Offline mode (desktop build 151, task 214): the rules and the wording.
 * While the internet is gone, only local files stations play: every
 * station that needs the internet is grayed out, can't be started, and is
 * left out of next / previous until the connection is back.
 */
object OfflineRules {
    /** storage.needs_internet: everything but a local files station. */
    fun needsInternet(s: Station?): Boolean = s != null && !(s.kind == "local" || LocalStations.localPath(s).isNotEmpty())

    // The desktop's words (mainwindow._offline_changed, bridge.play, bridge._online_only, stationlist.OFFLINE_TIP).
    const val BANNER = "You're offline. Frequencies that need the internet are grayed out until it's back."
    const val ROW = "Needs the internet, and you're offline right now."
    const val BACK = "Back online: every frequency is available again."
    const val NONE_LOCAL = "You're offline, and no local files station is in this list. Everything comes back when the internet does."
    fun cantPlay(name: String) = "You're offline. $name needs the internet; local files still play."
    fun switched(name: String) = "You're offline, so Frequencies switched to $name (local files)."
    fun stopped(name: String) = "You're offline, so $name stopped. It starts again when the internet is back."

    // The phone's own: a desktop station whose music folder is on the computer.
    const val NOT_HERE = "Not on this phone"
    fun notHere(name: String) = "$name plays a music folder that isn't on this phone. Choose a folder in its station settings."
}

/**
 * offline.Watch's rule: the answer only changes after it has held for a
 * while both ways (offline after [offAfterMs] of bad looks in a row,
 * online after [onAfterMs] of good ones), so a Wi-Fi blip changes nothing.
 */
class OfflineDecider(private val offAfterMs: Long = 6_000, private val onAfterMs: Long = 4_000) {
    var offline = false
        private set
    private var disagreeSince: Long? = null

    /** One look at [nowMs]: does the internet answer? Returns true when [offline] just flipped. */
    fun look(online: Boolean, nowMs: Long): Boolean {
        if (online != offline) {          // agrees with the current answer
            disagreeSince = null
            return false
        }
        val since = disagreeSince ?: nowMs.also { disagreeSince = it }
        val needed = if (offline) onAfterMs else offAfterMs
        if (nowMs - since < needed) return false
        offline = !offline
        disagreeSince = null
        return true
    }
}
