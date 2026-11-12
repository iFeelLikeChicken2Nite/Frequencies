package com.frequencies.app.playback

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Why a streaming service's station stopped, in the service's own plain
 * words (build 176: an iBroadcast refusal showed only the player's "The
 * station's server said no."). The playback service writes it; the
 * screens show it in place of the player's own words while that station's
 * error stands. Gone when the station is tuned again.
 */
object ServiceProblems {
    private val problems = ConcurrentHashMap<String, String>()
    private val watchers = CopyOnWriteArrayList<() -> Unit>()

    fun of(id: String?): String? = id?.let { problems[it] }

    fun set(id: String, text: String) {
        problems[id] = text
        watchers.forEach { it() }
    }

    fun clear(id: String) {
        if (problems.remove(id) != null) watchers.forEach { it() }
    }

    /**
     * The player's own error while it stands (build 198, task 239). The session
     * never shows it (the car and the notification get a paused station instead
     * of an error screen), so the app's own screens read it from here.
     */
    @Volatile var playerError: androidx.media3.common.PlaybackException? = null
        set(value) {
            if (field === value) return
            field = value
            watchers.forEach { it() }
        }

    /** [onChange] runs on the thread that changed it. */
    fun watch(onChange: () -> Unit) { watchers += onChange }
    fun unwatch(onChange: () -> Unit) { watchers -= onChange }
}
