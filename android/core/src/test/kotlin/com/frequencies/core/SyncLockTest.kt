package com.frequencies.core

import java.io.File
import java.nio.file.Files
import kotlin.concurrent.thread
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Desktop build 178 notes: the app's SyncKeeper asked waitingArt (the
 * store's lock) while holding its own lock, and takeState called the
 * app's taken() (which needs that lock) while holding the store's: both
 * threads waited for each other for good, and with ~800 stations a sync
 * froze every time. taken() now runs outside the store's lock.
 */
class SyncLockTest {
    private class Device(val me: String, stations: List<Station>, onTaken: (SyncPhone) -> Unit = {}) {
        val dir: File = Files.createTempDirectory("lock").toFile()
        @Volatile var list: List<Station> = stations
        @Volatile var settings: Settings = Settings.from(null)
        @Volatile var history: JsonArray = JsonArray(emptyList())
        private val lib = object : SyncLibrary {
            override fun <T> edit(block: (SyncEdit) -> T): T = synchronized(this) {
                val e = SyncEdit(list.map { it.copy() }.toMutableList(), Settings(LinkedHashMap(settings.data)),
                    SongHistory.entries(history).toMutableList())
                val r = block(e)
                if (e.stationsChanged) list = e.stations.toList()
                r
            }
        }
        lateinit var phone: SyncPhone
        init {
            phone = SyncPhone(me, SyncData.Store(), {}, File(dir, "artwork"), lib, taken = { _, _ -> onTaken(phone) })
        }
    }

    @Test fun takenRunsOutsideTheStoresLock() {
        val pic = Files.createTempFile("pic", ".png").toFile().apply { writeBytes(ByteArray(2048) { it.toByte() }) }
        val desk = Device("d".repeat(32), (1..50).map { i ->
            Station(linkedMapOf(
                "id" to JsonPrimitive("st-$i"),
                "url" to JsonPrimitive("http://example.com/$i"),
                "name" to JsonPrimitive("Station $i"),
                "custom_art_path" to JsonPrimitive(pic.absolutePath),
            ))
        })
        val state = desk.phone.stateNow("p".repeat(32))
        var answered = false
        val phone = Device("p".repeat(32), emptyList()) { p ->
            // What SyncKeeper.checkRun does from another thread, here while taken() runs.
            val other = thread { p.waitingArt() }
            other.join(5_000)
            answered = !other.isAlive
        }
        val need = phone.phone.takeState("d".repeat(32), state)
        assertTrue("asked for the picture", need.isNotEmpty())
        assertTrue("waitingArt answered while taken() ran", answered)
    }
}
