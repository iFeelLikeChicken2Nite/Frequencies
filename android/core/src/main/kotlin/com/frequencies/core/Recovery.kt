package com.frequencies.core

/**
 * What the phone does when a station stops by itself (tasks 239 and 240, Android build 198): it never
 * sits on a failed station. A station that was playing and dropped is reconnected a few times, waiting
 * a little longer each time, while the car and the phone show it loading; then the next station that
 * can play takes over ("Skip broken stations"), or the music folder when the signal looks weak. A
 * station that never played at all moves on at once, as before.
 *
 * Loop guards: a station's reconnects ([Tries]) are forgotten only after it has played steadily for
 * [STEADY_MS] since its last drop, so a stream that keeps dropping can't reconnect forever; and
 * moving on stops after a full lap of stations failing one after another with nothing heard. Then the
 * phone stops cleanly and says so (Play tries again): never an error screen in the car.
 */
object Recovery {
    /** The waits before reconnect 1, 2 and 3. */
    val WAITS_MS: List<Long> = listOf(2_000L, 5_000L, 10_000L)

    /** Playing this long without a drop: the station's reconnects start over. */
    const val STEADY_MS = 60_000L

    /** A refusal that asking again won't change (a page that isn't there, a forbidden stream); a timeout, rate limit or server trouble may. */
    fun worthRetrying(httpCode: Int?): Boolean = httpCode == null || httpCode == 408 || httpCode == 429 || httpCode >= 500

    /**
     * The wait before the next reconnect after [tries] of them, or null when it is time to move on. A
     * station that never played moves on at once, unless the network itself failed ([networkTrouble]:
     * no connection, a timeout): that is most likely the reception, so it is tried again (and the weak
     * signal rule may switch to the music folder) rather than every station failing in turn.
     */
    fun nextWait(tries: Int, httpCode: Int?, wasPlaying: Boolean, networkTrouble: Boolean): Long? = when {
        !worthRetrying(httpCode) -> null
        tries == 0 && !wasPlaying && !networkTrouble -> null
        else -> WAITS_MS.getOrNull(tries)
    }

    /**
     * One station's reconnects since it last played steadily (build 206, task 249). Every kind of drop
     * counts here, whoever reconnects: a stream that errors or stalls (the app waits and reconnects), one
     * the server ends (reconnected at once), and one whose connection breaks while it plays, which Media3
     * reconnects by itself without telling the app. Steady means [STEADY_MS] since the sound started AND
     * since the last drop: build 198 counted only from the start, so a station that came back by itself
     * after a drop, or that Media3 kept reconnecting, had its count wiped a minute after it first played
     * and was tried again for ever (task 249).
     */
    class Tries {
        /** The station being counted, and how many reconnects it has had. */
        var stationId: String? = null
            private set
        var count = 0
            private set
        private var lastDrop = 0L

        /** The reconnects [id] has had so far (a different station starts at 0). */
        fun of(id: String): Int {
            if (stationId != id) {
                stationId = id
                count = 0
                lastDrop = 0L
            }
            return count
        }

        /** [id] dropped at [now] and is being reconnected: one more try used. */
        fun use(id: String, now: Long) {
            of(id)
            count++
            lastDrop = now
        }

        /** Whether [id] may be reconnected once more (Media3's own reconnect, or the stream's end). */
        fun left(id: String): Boolean = of(id) < WAITS_MS.size

        /** A new tune-in or moving on: nothing counted. */
        fun forget() {
            stationId = null
            count = 0
            lastDrop = 0L
        }

        /** [id] plays at [now], its sound having started at [playingFrom]: steady long enough, its tries start over. */
        fun playing(id: String, now: Long, playingFrom: Long) {
            if (count > 0 && stationId == id && steady(now, playingFrom, lastDrop)) forget()
        }
    }

    /** Playing steadily: [STEADY_MS] since the sound started and since the last drop. */
    fun steady(now: Long, playingFrom: Long, lastDrop: Long): Boolean = now - maxOf(playingFrom, lastDrop) >= STEADY_MS

    /** Moving on again after [failedInARow] stations failed one after another with nothing heard, out of [stations]. */
    fun keepMovingOn(failedInARow: Int, stations: Int): Boolean = failedInARow < stations

    /** What the user is told when it stops ([lap]: every station failed in a row). */
    fun stoppedNotice(name: String, lap: Boolean): String =
        if (lap) "No station could be reached. Press Play to try again."
        else "$name couldn't be reached. Press Play to try again."
}
