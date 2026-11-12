package com.frequencies.core

/**
 * Per-station pre-buffering, the desktop's (storage "buffer_enabled" /
 * "buffer_seconds", learned "connect_worst"; bridge._buffer_for and
 * Player.play's 1–60 s clamp). Off by default. When on, the player reads
 * that far ahead before the station is heard, and waits at most that long
 * plus 12 s (player._handle_connected). On the phone it is a bigger Media3
 * buffer for the flagged stations only.
 */
object Prebuffer {
    const val TITLE = "Pre-buffering"
    const val SWITCH = "Pre-buffer this station"
    const val BLURB = "Read further ahead before starting. Slower to begin, but it rides out the drop-outs that make some streams unlistenable."
    const val AUTO = "auto (use the measured worst)"
    /** The longest the player waits past the target before it starts anyway. */
    const val GRACE_S = 12.0
    const val MAX_S = 60.0

    /** The Settings line under the blurb: the measured worst start, or what will be. */
    fun worstLine(station: Station): String {
        val worst = worst(station)
        return if (worst > 0) "The longest this station has taken to start is ${"%.1f".format(java.util.Locale.US, worst)}s."
        else "Once this station has played, its slowest start will be shown here as a guide."
    }

    fun worst(station: Station): Double = station["connect_worst"].num() ?: 0.0

    /**
     * bridge._buffer_for + the player's clamp: seconds to read ahead before
     * playing, or 0 to leave the player's normal start alone. An enabled
     * setting with no value of its own uses the worst start measured.
     */
    fun seconds(station: Station?): Double {
        if (station == null || !truthy(station["buffer_enabled"])) return 0.0
        var s = station["buffer_seconds"].num() ?: 0.0
        if (s <= 0) s = worst(station)
        return if (s > 0) s.coerceIn(1.0, MAX_S) else 0.0
    }

    /** Station id -> pre-buffer in ms, for the flagged stations only. */
    fun targets(stations: List<Station>): Map<String, Long> =
        stations.mapNotNull { s -> seconds(s).takeIf { it > 0 }?.let { s.id to (it * 1000).toLong() } }.toMap()

    /** The new connect_worst after a start that took [seconds], or null when it isn't a new worst. */
    fun newWorst(station: Station, seconds: Double): Double? =
        if (seconds > worst(station)) Math.round(seconds.coerceAtMost(300.0) * 10) / 10.0 else null
}
