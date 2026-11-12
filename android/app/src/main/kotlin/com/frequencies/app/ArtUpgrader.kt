package com.frequencies.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import com.frequencies.core.ArtFinder
import com.frequencies.core.ArtImage
import com.frequencies.core.ArtMatch
import com.frequencies.core.Featured
import com.frequencies.core.Station
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.JsonPrimitive

/**
 * The desktop's art upgrade (artupgrade.ArtUpgrade, build 126): a station
 * that came by QR code, share code or zip wears the code's small copy of
 * its picture; this finds the same picture full size online and swaps it
 * in, so the app, the notification and Android Auto stop showing a blurry
 * thumbnail. A picture the user chose is never replaced. Runs a few
 * seconds after start and whenever stations arrive, two at a time.
 */
class ArtUpgrader(private val library: Library) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = Semaphore(MAX_RUNNING)
    private val tried = HashSet<String>()
    val finder = ArtFinder(::decode, library.directory)

    fun start() {
        scope.launch {
            delay(4000)
            library.stations.collect { list -> list.forEach { queue(it) } }
        }
    }

    fun queue(station: Station, force: Boolean = false) {
        if (!ArtMatch.carriesThumbnail(station, library.files.artDir)) return
        synchronized(tried) {
            if (station.id in tried) return
            if (!ArtMatch.due(station, library.now(), force)) return
            tried.add(station.id)
        }
        scope.launch { running.withPermit { upgrade(station) } }
    }

    private fun upgrade(station: Station) {
        val thumbPath = station.customArtPath
        val thumb = runCatching { File(thumbPath).readBytes() }.getOrNull() ?: return
        val result = runCatching { finder.search(station, thumb) }.getOrElse {
            ArtMatch.Result("error", notes = listOf("${it.javaClass.simpleName}: ${it.message}"))
        }
        val now = library.station(station.id) ?: return
        val name = now.displayName
        val notes = if (result.notes.isEmpty()) "" else " (${result.notes.joinToString("; ")})"
        if (now.customArtPath != thumbPath || !com.frequencies.core.truthy(now[ArtMatch.FLAG])) {
            library.update(station.id) { it.data.remove(ArtMatch.FLAG) }
            log("'$name': kept the picture you chose")
            return
        }
        when (result.status) {
            "found" -> {
                val file = save(result, station.id) ?: run { log("'$name': found full-size art but couldn't save it"); return }
                library.update(station.id) {
                    it["custom_art_path"] = JsonPrimitive(file.absolutePath)
                    it.data.remove(ArtMatch.FLAG)
                    it.data.remove(ArtMatch.CHECKED)
                    it["art_url"] = JsonPrimitive(result.url)
                    it["art_url_sha"] = JsonPrimitive(Featured.sha1(file))
                }
                if (File(thumbPath).name.startsWith(station.id)) File(thumbPath).delete()
                val host = runCatching { java.net.URI(result.url).host }.getOrNull().orEmpty()
                val from = if (result.via.isNotEmpty()) "${result.via} ($host)" else host
                log("'$name': matched and applied full-size art ${result.image?.width}x${result.image?.height} from $from (the code's copy was ${result.thumb}; ${result.match})")
            }
            "none" -> {
                library.update(station.id) { it[ArtMatch.CHECKED] = com.frequencies.core.floatJson(library.now()) }
                log("'$name': kept the code's ${result.thumb} copy, no online picture matched (${result.looked} looked at)$notes")
            }
            "offline" -> log("'$name': kept the code's copy, couldn't search (no connection?)$notes")
            "flat" -> {
                library.update(station.id) { it[ArtMatch.CHECKED] = com.frequencies.core.floatJson(library.now() + 10 * 365 * 86400.0) }
                log("'$name': kept the code's ${result.thumb} copy, it is too plain to match against")
            }
            else -> log("'$name': kept the code's copy (${result.status})$notes")
        }
    }

    /** artupgrade.save_picture: at most 1200 px, PNG when it has see-through parts, else JPEG. */
    private fun save(result: ArtMatch.Result, stationId: String): File? {
        val bytes = result.bytes ?: return null
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
        val scaled = fit(bitmap, ArtMatch.SAVE_MAX_PX)
        val png = scaled.hasAlpha()
        val out = ByteArrayOutputStream()
        if (!scaled.compress(if (png) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG, if (png) 100 else 92, out)) return null
        val data = out.toByteArray()
        val digest = MessageDigest.getInstance("SHA-1").digest(data).joinToString("") { "%02x".format(it) }.take(10)
        val dir = library.files.artDir.apply { mkdirs() }
        return File(dir, "$stationId-$digest.${if (png) "png" else "jpg"}").takeIf { runCatching { it.writeBytes(data) }.isSuccess }
    }

    /** A picture the user picked in "Find a picture", saved like an upgrade's. */
    fun saveChosen(stationId: String, url: String): Boolean {
        val bytes = runCatching { finder.get(url) }.getOrNull() ?: return false
        val file = save(ArtMatch.Result("found", bytes = bytes, url = url), stationId) ?: return false
        library.update(stationId) {
            it["custom_art_path"] = JsonPrimitive(file.absolutePath)
            it.data.remove(ArtMatch.FLAG)
            it.data.remove(ArtMatch.CHECKED)
            it["art_url"] = JsonPrimitive(url)
            it["art_url_sha"] = JsonPrimitive(Featured.sha1(file))
        }
        log("'${library.station(stationId)?.displayName}': picture chosen from $url")
        return true
    }

    private fun log(text: String) = Log.i("Frequencies", "[art] $text")

    companion object {
        private const val MAX_RUNNING = 2

        private fun fit(bitmap: Bitmap, most: Int): Bitmap {
            val side = maxOf(bitmap.width, bitmap.height)
            if (side <= most) return bitmap
            val k = most.toFloat() / side
            return Bitmap.createScaledBitmap(bitmap, (bitmap.width * k).toInt().coerceAtLeast(1), (bitmap.height * k).toInt().coerceAtLeast(1), true)
        }

        /** Picture bytes as pixels for the comparison; very large pictures are read at a fraction. */
        fun decode(bytes: ByteArray): ArtImage? {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 2400) sample *= 2
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply {
                inSampleSize = sample
                inPreferredConfig = Bitmap.Config.ARGB_8888
            }) ?: return null
            val pixels = IntArray(bitmap.width * bitmap.height)
            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
            return ArtImage(bitmap.width, bitmap.height, pixels, bitmap.hasAlpha()).also { bitmap.recycle() }
        }
    }
}
