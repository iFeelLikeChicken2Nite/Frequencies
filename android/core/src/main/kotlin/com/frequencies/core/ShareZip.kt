package com.frequencies.core

import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Several saved frequencies as one zip (sharezip.py): frequencies-share.json
 * lists each as a featured entry, picture embedded. A local files station
 * can bring its music along under `audio/<n>/` (the desktop's layout): on
 * the way in it is unpacked into a new folder of the app's own and the
 * station points at it; one that came without its music arrives with no
 * folder, as on the desktop.
 */
object ShareZip {
    const val MANIFEST = "frequencies-share.json"
    const val FORMAT = "frequencies-share"

    class ShareZipError(message: String) : Exception(message)

    /** iBroadcast links are signed for one account, so those can't be shared. */
    fun shareable(station: Station): Boolean =
        station["ibroadcast_urls"].let { !truthy(it) } && station.kind != "ibroadcast"

    /** sharezip._safe: a name for the zip. */
    fun safeName(name: String): String {
        var text = name.filter { it.isLetterOrDigit() || it in " -_" }.trim().take(80).trim()
        if (text.uppercase() in setOf("CON", "PRN", "AUX", "NUL") + (1..9).flatMap { listOf("COM$it", "LPT$it") }) text = "_$text"
        return text.ifEmpty { "frequency" }
    }

    fun fileName(stations: List<Station>): String =
        (if (stations.size == 1) safeName(stations[0].displayName) else "Frequencies - ${stations.size} frequencies") + ".zip"

    /** One music file of a local files station: its path inside the folder ("Disc 1/01 Song.mp3") and how to read it. */
    class Track(val relativePath: String, val open: () -> InputStream)

    /** sharezip._STORED: already compressed, so deflating them again only costs time. */
    private val STORED = listOf(".mp3", ".ogg", ".opus", ".m4a", ".aac", ".flac", ".wma", ".webm", ".mka", ".mp4")

    /**
     * sharezip.write. [audio] gives a local files station's music (null or
     * empty: that station goes without it). Returns how many music files went in.
     */
    fun write(stations: List<Station>, out: OutputStream, audio: ((Station) -> List<Track>?)? = null): Int {
        var count = 0
        ZipOutputStream(out).use { z ->
            val entries = stations.mapIndexed { i, s ->
                val entry = LinkedHashMap(Featured.fromStation(s))
                if (s.kind == "local") {
                    // The folder's path means nothing on another machine.
                    entry["url"] = JsonPrimitive("local://" + safeName(s.displayName))
                    val tracks = audio?.invoke(s).orEmpty()
                    if (tracks.isNotEmpty()) {
                        val prefix = "audio/${i + 1}"
                        for (t in tracks) {
                            val rel = t.relativePath.split('/', '\\').filter { it.isNotEmpty() && it != "." && it != ".." }.joinToString("/")
                            if (rel.isEmpty()) continue
                            z.setLevel(if (STORED.any { rel.lowercase().endsWith(it) }) java.util.zip.Deflater.NO_COMPRESSION else java.util.zip.Deflater.DEFAULT_COMPRESSION)
                            z.putNextEntry(ZipEntry("$prefix/$rel"))
                            t.open().use { it.copyTo(z) }
                            z.closeEntry()
                            count++
                        }
                        entry["audio_folder"] = JsonPrimitive(prefix)
                    }
                }
                JsonObject(entry)
            }
            val manifest = JsonObject(
                linkedMapOf("format" to JsonPrimitive(FORMAT), "version" to JsonPrimitive(1), "stations" to JsonArray(entries)),
            )
            z.setLevel(java.util.zip.Deflater.DEFAULT_COMPRESSION)
            z.putNextEntry(ZipEntry(MANIFEST))
            z.write(encodeJson(manifest).toByteArray(Charsets.UTF_8))
            z.closeEntry()
        }
        return count
    }

    /**
     * The stations a share zip carries (pictures written to [artDir]). With
     * [audioRoot], a local files station that came with its music is
     * unpacked into a new folder there (sharezip.read / _unpack) and points
     * at it. The desktop writes the music before the list, so the music is
     * first set down in a staging folder while the zip streams by.
     */
    fun read(input: InputStream, artDir: File, newId: () -> String, audioRoot: File? = null): List<Station> {
        var manifest: String? = null
        val staging = audioRoot?.let { File(it, ".incoming-${System.nanoTime()}") }
        try {
            ZipInputStream(input, Charsets.UTF_8).use { z ->
                while (true) {
                    val e = z.nextEntry ?: break
                    if (e.name == MANIFEST) { manifest = z.readBytes().toString(Charsets.UTF_8); if (staging == null) break else continue }
                    if (staging == null || e.isDirectory || !e.name.startsWith("audio/")) continue
                    val parts = e.name.split('/').filter { it.isNotEmpty() }
                    if (parts.any { it == ".." || it == "." } || !LocalStations.isAudio(parts.last())) continue
                    val target = File(staging, parts.joinToString(File.separator))
                    target.parentFile?.mkdirs()
                    target.outputStream().use { z.copyTo(it) }
                }
            }
            return parse(manifest, artDir, newId, audioRoot, staging)
        } finally {
            staging?.deleteRecursively()
        }
    }

    private fun parse(manifest: String?, artDir: File, newId: () -> String, audioRoot: File?, staging: File?): List<Station> {
        val text = manifest ?: throw ShareZipError("That zip isn't a Frequencies share.")
        val data = runCatching { FreqJson.parseToJsonElement(text) as? JsonObject }.getOrNull()
        if (data == null || data["format"].str() != FORMAT) throw ShareZipError("That zip isn't a Frequencies share.")
        return (data["stations"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }
            .filter { truthy(it["url"]) }
            .map { raw ->
                val prefix = raw["audio_folder"].str().orEmpty().trim('/')
                val entry = JsonObject(raw - "audio_folder")
                Featured.toStation(entry, artDir, newId).also { s ->
                    if (s.kind == "local") {
                        s["local_path"] = JsonPrimitive("")
                        val unpacked = if (prefix.isEmpty() || staging == null || audioRoot == null) null
                        else unpack(File(staging, prefix.replace('/', File.separatorChar)), audioRoot, s.displayName)
                        if (unpacked != null) {
                            s["local_path"] = JsonPrimitive(unpacked.absolutePath)
                            s["url"] = JsonPrimitive("local://" + unpacked.absolutePath)
                        }
                    }
                }
            }
    }

    /** sharezip._unpack: the station's music into a fresh folder under [root], never over another. */
    private fun unpack(from: File, root: File, name: String): File? {
        if (!from.isDirectory || from.walkTopDown().none { it.isFile }) return null
        val base = File(root, safeName(name))
        var target = base
        var n = 2
        while (target.exists()) target = File(root, "${base.name} (${n++})")
        target.parentFile?.mkdirs()
        return if (from.renameTo(target) || from.copyRecursively(target)) target else null
    }

    /** Whether [head] (the first bytes of a file) is a zip. */
    fun looksLikeZip(head: ByteArray): Boolean =
        head.size >= 4 && head[0] == 'P'.code.toByte() && head[1] == 'K'.code.toByte() && head[2].toInt() == 3 && head[3].toInt() == 4
}
