package com.frequencies.songid

/**
 * Song ID: a Kotlin port of the Shazam signature the desktop makes with
 * shazamio_core (SongRec's code), and the lookup shazam.py does with it.
 */
object SongId {
    /** The signature is made from mono 16-bit PCM at this rate. */
    const val SAMPLE_RATE = 16_000

    /** The desktop records this much for each check (workers.ShazamWorker). */
    const val SECONDS = 6

    /** A check every this many seconds while a station plays (feeds.station_changed). */
    const val INTERVAL_SECONDS = 30

    fun samplesNeeded(): Int = SAMPLE_RATE * SECONDS
}
