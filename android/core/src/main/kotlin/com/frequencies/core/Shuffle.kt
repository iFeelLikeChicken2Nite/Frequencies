package com.frequencies.core

import kotlin.random.Random

/**
 * Shuffle, as the desktop does it (bridge.next, _shuffle_pool, previous):
 * Next picks a random frequency while it is on; Previous always goes back
 * to the one played before. The state is the desktop's own settings key
 * [SETTING], so a backup or a sync carries it both ways.
 *
 * The phone's indexes are positions in the player's list; the caller says
 * which of them can't play now ([unavailable]: offline and needing the
 * internet, a music folder not on this phone) and which are marked down.
 */
object Shuffle {
    /**
     * storage.DEFAULT_SETTINGS "shuffle": on since desktop build 181 / phone
     * build 182 (user, task 232), for new profiles only; an older settings
     * file without the key reads as off (Settings.from).
     */
    const val SETTING = "shuffle"

    fun on(settings: Settings?): Boolean = settings?.bool(SETTING, true) ?: true

    /**
     * The next frequency with shuffle on, or null when nothing else can
     * play. One of the others at random, passing over those marked down
     * (bridge._shuffle_pool) unless that is all there is; never one that
     * can't play at all. Every one is as likely as any other ("Custom
     * station frequency" is gone, user 2026-11-04).
     */
    fun next(
        count: Int,
        here: Int?,
        unavailable: (Int) -> Boolean,
        down: (Int) -> Boolean,
        random: Random = Random.Default,
    ): Int? {
        val others = (0 until count).filter { it != here && !unavailable(it) }
        if (others.isEmpty()) return null
        val pool = others.filterNot(down).ifEmpty { others }
        return pool.random(random)
    }

    /**
     * bridge._back_stack: the frequencies played before this one, newest
     * last. Previous takes them back off; the one left is not put back on.
     */
    class BackStack(private val limit: Int = 100) {
        private val ids = ArrayList<String>()

        val size: Int get() = ids.size

        /** [left] stopped playing for another frequency. */
        fun push(left: String) {
            if (ids.lastOrNull() == left) return
            ids += left
            while (ids.size > limit) ids.removeAt(0)
        }

        /**
         * bridge.previous: the newest one [usable] says yes to, dropping the
         * ones passed on the way; null when none is left. [passed] collects
         * the names of the down ones skipped, for the log line.
         */
        fun pop(usable: (String) -> Usable): Pair<String?, List<String>> {
            val passed = ArrayList<String>()
            while (ids.isNotEmpty()) {
                val id = ids.removeAt(ids.lastIndex)
                when (usable(id)) {
                    Usable.YES -> return id to passed
                    Usable.DOWN -> passed += id
                    Usable.NO -> Unit
                }
            }
            return null to passed
        }

        fun clear() = ids.clear()
    }

    enum class Usable { YES, DOWN, NO }

    /**
     * bridge.previous's test for one frequency: gone or unable to play now
     * is passed over quietly; marked down is passed over only with shuffle
     * off.
     */
    fun usableBack(exists: Boolean, unavailable: Boolean, down: Boolean, shuffle: Boolean): Usable = when {
        !exists || unavailable -> Usable.NO
        down && !shuffle -> Usable.DOWN
        else -> Usable.YES
    }
}
