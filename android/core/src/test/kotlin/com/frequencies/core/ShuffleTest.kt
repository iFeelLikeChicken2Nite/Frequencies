package com.frequencies.core

import java.nio.file.Files
import kotlin.random.Random
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Shuffle (comfort spec 7): the desktop's bridge.next / _shuffle_pool / previous rules. */
class ShuffleTest {
    private val none: (Int) -> Boolean = { false }

    @Test
    fun neverTheCurrentOneNeverOneThatCantPlay() {
        val unavailable = setOf(1, 4)          // offline and needing the internet, a folder not here
        val down = setOf(2, 6)
        val rng = Random(7)
        repeat(2000) {
            val pick = Shuffle.next(8, 3, { it in unavailable }, { it in down }, random = rng)!!
            assertTrue("landed on $pick", pick != 3 && pick !in unavailable && pick !in down)
        }
    }

    @Test
    fun downOnesOnlyWhenThatIsAllThereIs() {
        // bridge._shuffle_pool: "Everything marked down is still better than refusing to move."
        val rng = Random(1)
        val seen = HashSet<Int>()
        repeat(200) { seen += Shuffle.next(4, 0, none, { it != 0 }, random = rng)!! }
        assertEquals(setOf(1, 2, 3), seen)
        // But never one that can't play at all: nothing else left is null (the caller says why).
        assertNull(Shuffle.next(3, 0, { it != 0 }, none, random = rng))
        assertNull(Shuffle.next(1, 0, none, none, random = rng))
        assertNull(Shuffle.next(0, null, none, none, random = rng))
        // Nothing playing yet: any of them, the first included.
        assertEquals(0, Shuffle.next(1, null, none, none, random = rng))
    }

    @Test
    fun uniformOverAThousandDraws() {
        val rng = Random(42)
        val counts = IntArray(6)
        repeat(1000) { counts[Shuffle.next(6, 0, none, none, random = rng)!!]++ }
        assertEquals(0, counts[0])
        // Five others at about 200 each; well inside what chance allows (sd ~ 12.6).
        for (i in 1..5) assertTrue("frequency $i came up ${counts[i]} times", counts[i] in 150..250)
    }

    @Test
    fun shuffleOffKeepsTheListOrder() {
        // Shuffle off is Health.stepPastDown, unchanged: in order, wrapping, passing over the down ones.
        assertEquals(1, Health.stepPastDown(4, 0, +1) { false }.index)
        assertEquals(0, Health.stepPastDown(4, 3, +1) { false }.index)
        assertEquals(3, Health.stepPastDown(4, 0, -1) { false }.index)
        assertEquals(2, Health.stepPastDown(4, 0, +1) { it == 1 }.index)
    }

    @Test
    fun previousIsTheOnePlayedBefore() {
        val back = Shuffle.BackStack()
        listOf("a", "b", "b", "c").forEach(back::push)
        assertEquals(3, back.size)                  // the same one twice in a row is one entry
        val exists = setOf("a", "b")
        val down = setOf("b")
        fun usable(shuffle: Boolean): (String) -> Shuffle.Usable = { id ->
            Shuffle.usableBack(id in exists, false, id in down, shuffle)
        }
        // Shuffle off: "c" is gone (deleted), "b" is down and passed over, "a" it is.
        val (id, passed) = back.pop(usable(false))
        assertEquals("a", id)
        assertEquals(listOf("b"), passed)
        assertEquals(0, back.size)
        assertNull(back.pop(usable(false)).first)
        // Shuffle on: a down one is still where you were.
        listOf("a", "b").forEach(back::push)
        assertEquals("b", back.pop(usable(true)).first)
        // Can't play now (offline): passed over quietly.
        assertEquals(Shuffle.Usable.NO, Shuffle.usableBack(true, true, false, true))
        // A long session keeps the newest ones.
        val small = Shuffle.BackStack(limit = 3)
        listOf("1", "2", "3", "4", "5").forEach(small::push)
        assertEquals(3, small.size)
        assertEquals("5", small.pop { Shuffle.Usable.YES }.first)
    }

    @Test
    fun theSettingIsTheDesktopsAndSurvivesABackup() {
        val fresh = Settings.from(null)
        assertTrue(Shuffle.on(fresh))               // storage.DEFAULT_SETTINGS, on since build 182 (desktop 181)
        assertEquals(JsonPrimitive(true), fresh["shuffle"])
        assertTrue(Shuffle.on(null))
        assertTrue(Shuffle.on(Settings.from(JsonObject(emptyMap()))))
        // A profile from before 182 that never saved it keeps the old default, off; a saved choice stays.
        assertFalse(Shuffle.on(Settings.from(JsonObject(mapOf("theme" to JsonPrimitive("paper"))))))
        assertFalse(Shuffle.on(Settings.from(JsonObject(mapOf("shuffle" to JsonPrimitive(false))))))
        val settings = Settings.from(JsonObject(mapOf("shuffle" to JsonPrimitive(true))))
        val contents = Backup.Contents(emptyList(), settings, JsonObject(emptyMap()), JsonArray(emptyList()), JsonObject(emptyMap()), JsonObject(emptyMap()))
        val text = FreqJson.encodeToString(JsonObject.serializer(), Backup.export(contents, exportedAt = 1.0))
        val back = Backup.read(text, Files.createTempDirectory("art").toFile()).settings
        assertTrue(Shuffle.on(back))
    }

    @Test
    fun customStationFrequencyIsGone() {
        // User, 2026-11-04 (desktop storage.REMOVED_SETTINGS): not a default,
        // dropped from an old settings.json and from a restored backup,
        // while keys the phone doesn't know still ride along untouched.
        assertFalse("local_station_ratio" in Settings.from(null).data)
        val old = Settings.from(JsonObject(mapOf(
            "local_station_ratio" to JsonPrimitive(30),
            "winamp_skin" to JsonPrimitive("base.wsz"),
        )))
        assertFalse("local_station_ratio" in old.data)
        assertEquals(JsonPrimitive("base.wsz"), old["winamp_skin"])
        // A desktop backup written before build 165 still has the key.
        val text = javaClass.getResource("/desktop/desktop-backup.json")!!.readText()
        assertTrue(text.contains("\"local_station_ratio\""))
        val back = Backup.read(text, Files.createTempDirectory("art").toFile()).settings
        assertFalse("local_station_ratio" in back.data)
        assertFalse("local_station_ratio" in SyncData.SHARED_SETTINGS)
    }
}
