package com.frequencies.core

import java.io.File
import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.zip.ZipFile
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * The developer's addresses the build ships (photo_pack.json, written by
 * the desktop's tools/make_photo_pack.py): the photo pack, where
 * thumbs-down reports go, and the picture links. None is a secret.
 */
class PackAddresses(private val data: JsonObject) {
    val pack: String get() = data["pack"].str().orEmpty().trim()
    val reports: String get() = data["reports"].str().orEmpty().trim()
    val art: String get() = data["art"].str().orEmpty().trim()

    /**
     * photopack.report_service_address (build 151): the developer's report
     * Worker, https://<name>.workers.dev, or "" (reports open in the browser).
     */
    val reportService: String
        get() = data["report_service"].str().orEmpty().trim().trimEnd('/').takeIf { it.startsWith("https://") || it.startsWith("http://") }.orEmpty()

    /** photopack.bio_address: bio-fixes.json beside photo-pack.json. */
    val bioFixes: String get() = beside("bio-fixes.json")

    fun beside(name: String): String = if (pack.isEmpty()) "" else runCatching { pack.toHttpUrl().resolve(name).toString() }.getOrDefault("")

    /**
     * photopack.report_address: https://github.com/<owner>/<repo> the
     * thumbs-down issues go to: the report address, else the photo
     * pack's repository.
     */
    val reportRepo: String
        get() {
            for (address in listOf(reports, pack)) {
                REPO.find(address)?.let { return "https://github.com/${it.groupValues[1]}/${it.groupValues[2]}" }
            }
            return ""
        }

    companion object {
        private val REPO = Regex("^https://github\\.com/([^/\\s]+)/([^/\\s#?]+)")
        fun read(text: String?): PackAddresses =
            PackAddresses(runCatching { FreqJson.parseToJsonElement(text ?: "").jsonObject }.getOrDefault(JsonObject(emptyMap())))
    }
}

/**
 * The photo pack and the bio fixes on this phone (photopack.py):
 * `<dir>/photo_pack/` (pack.json, photos/, talk_photos/, installed.json)
 * and `<dir>/bio_fixes.json`, kept when the photos are switched off.
 */
class PhotoPackStore(val dir: File, private val folder: String = PACK_DIR) {
    val packDir get() = File(dir, folder)
    val bioFile get() = File(dir, BIO_FILE)

    private class Cached(var stamp: Long = -1, var artists: Map<String, JsonObject> = emptyMap(), var info: JsonObject = JsonObject(emptyMap()))
    private val packCache = Cached()
    private val bioCache = Cached()

    private fun read(file: File, cache: Cached): Map<String, JsonObject> = synchronized(cache) {
        val stamp = if (file.isFile) file.lastModified() + file.length() * 31 else 0L
        if (stamp != cache.stamp) {
            val data = if (stamp != 0L) readJson(file) as? JsonObject else null
            cache.artists = ArtistOverrides.lowerKeys(data?.get("artists") as? JsonObject ?: JsonObject(emptyMap()))
            cache.info = data ?: JsonObject(emptyMap())
            cache.stamp = stamp
        }
        cache.artists
    }

    /** photopack.pack_names: the pack's artist entries by lowercase name. */
    fun packNames(): Map<String, JsonObject> = read(File(packDir, PACK_INDEX), packCache)

    /** photopack.artist_entry: the pack's entry, its photo as a full path here (dropped if missing). */
    fun artistEntry(key: String): Map<String, JsonElement> {
        val entry = LinkedHashMap<String, JsonElement>(packNames()[key.trim().lowercase()] ?: return emptyMap())
        val photo = entry["photo"].str().orEmpty().trim()
        if (photo.isNotEmpty() && !photo.startsWith("http://") && !photo.startsWith("https://")) {
            val file = inside(photo)
            if (file != null && file.exists()) entry["photo"] = JsonPrimitive(file.path) else entry.remove("photo")
        }
        return entry
    }

    /** photopack.logo_entry: a logo pack's entry, its logo as a full path here; empty when its file isn't here. */
    fun logoEntry(key: String): Map<String, JsonElement> {
        val entry = LinkedHashMap<String, JsonElement>(packNames()[key.trim().lowercase()] ?: return emptyMap())
        val file = inside(entry["logo"].str().orEmpty().trim()) ?: return emptyMap()
        if (!file.isFile) return emptyMap()
        entry["logo"] = JsonPrimitive(file.path)
        return entry
    }

    /** photopack.talk_photo */
    fun talkPhoto(base: String): File? = inside("talk_photos/" + File(base).name)?.takeIf { it.exists() }

    private fun inside(relative: String, folder: File = packDir): File? {
        val root = folder.canonicalFile
        val file = File(root, relative).canonicalFile
        return file.takeIf { it.path.startsWith(root.path + File.separator) }
    }

    /** True when [file] is one of the pack's own files. */
    fun relativeToPack(file: File): String? {
        val root = packDir.canonicalPath + File.separator
        val path = file.canonicalPath
        return if (path.startsWith(root)) path.substring(root.length).replace(File.separatorChar, '/') else null
    }

    /** photopack.installed: {"sha256", "made", "photos", "artists", "installed"} or empty. */
    fun installed(): JsonObject = readJson(File(packDir, INSTALLED)) as? JsonObject ?: JsonObject(emptyMap())

    /** Opting out deletes the pack (not the bio fixes). True if there was one. */
    fun remove(): Boolean {
        if (!packDir.isDirectory) return false
        packDir.deleteRecursively()
        return true
    }

    /** photopack.bio_names / bio_entry: the developer's text fixes (strings only, never a photo). */
    fun bioNames(): Map<String, JsonObject> = read(bioFile, bioCache)

    fun bioEntry(key: String): Map<String, JsonElement> =
        (bioNames()[key.trim().lowercase()] ?: emptyMap()).filter { (k, v) -> k != "photo" && v is JsonPrimitive && v.isString }

    /** photopack.bio_info: {"sha256", "made", "artists"} of the fixes here, or null. */
    fun bioInfo(): Triple<String, String, Int>? {
        if (!bioFile.isFile) return null
        val names = bioNames()
        val info = synchronized(bioCache) { bioCache.info }
        return Triple(info["sha256"].str().orEmpty(), info["made"].str().orEmpty(), names.size)
    }

    companion object {
        const val PACK_DIR = "photo_pack"
        const val PACK_INDEX = "pack.json"
        const val INSTALLED = "installed.json"
        const val BIO_FILE = "bio_fixes.json"
        /** settings.json: the opt-in. Off unless switched on, as on the desktop. */
        const val SETTING = "photo_pack"
    }
}

/** What a pack or bio-fixes check came to: "current", "updated", "none" or "failed". */
data class PackResult(val state: String, val text: String)

/**
 * photopack.PackUpdate and BioUpdate: read photo-pack.json; download the
 * pack only when its checksum changed, check it, undo the base32 (twice),
 * unpack beside the old one and swap the two folders.
 */
class PackUpdater(
    private val http: OkHttpClient,
    private val store: PhotoPackStore,
    private val today: () -> Date = { Date() },
) {
    fun updatePack(address: String, cancel: () -> Boolean = { false }, progress: (Int) -> Unit = {}): PackResult =
        try {
            pack(address, cancel, progress)
        } catch (e: Exception) {
            PackResult("failed", why(e))
        }

    /**
     * photopack.Keeper.check_logos (build 141): the band logos, a pack of
     * their own beside the photo pack (logo-pack.json); "none" when the
     * developer hasn't published any. Same checks, same encoding.
     */
    fun updateLogos(address: String, cancel: () -> Boolean = { false }): PackResult = try {
        val missing = http.newCall(request(address)).execute().use { it.code == 404 }
        if (missing) PackResult("none", "none published yet") else pack(address, cancel) {}
    } catch (e: Exception) {
        PackResult("failed", why(e))
    }

    private fun pack(address: String, cancel: () -> Boolean, progress: (Int) -> Unit): PackResult {
        val manifest = getJson(address) ?: return PackResult("failed", "the pack address doesn't point at a pack")
        val sha = manifest["sha256"].str().orEmpty()
        if (sha.isEmpty()) return PackResult("failed", "the pack address doesn't point at a pack")
        val have = store.installed()
        if (have["sha256"].str() == sha) return PackResult("current", summary(have))

        val file = manifest["url"].str()?.ifEmpty { null } ?: manifest["file"].str()?.ifEmpty { null } ?: "frequencies-photo-pack.b32"
        val url = address.toHttpUrl().resolve(file) ?: return PackResult("failed", "the pack address doesn't point at a pack")
        store.dir.mkdirs()
        val folder = store.packDir
        var part = File(folder.path + ".download")
        val digest = MessageDigest.getInstance("SHA-256")
        progress(0)
        http.newCall(request(url.toString())).execute().use { r ->
            if (!r.isSuccessful) throw HttpFailure(r.code)
            val body = r.body!!
            val size = manifest["size"].num()?.toLong()?.takeIf { it > 0 } ?: body.contentLength()
            var got = 0L
            var last = -1
            part.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        if (cancel()) throw IOException("stopped")
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        digest.update(buf, 0, n)
                        got += n
                        val percent = if (size > 0) minOf(99, (got * 100 / size).toInt()) else 0
                        if (percent != last) { last = percent; progress(percent) }
                    }
                }
            }
        }
        if (hex(digest.digest()) != sha) {
            part.delete()
            return PackResult("failed", "the download didn't match the pack's checksum (an old or broken upload?)")
        }
        if (manifest["encoding"].str() == "base32x2") {
            val plain = File(folder.path + ".zip")
            part.inputStream().buffered().use { input ->
                Base32Input(Base32Input(input)).use { decoded -> plain.outputStream().use { decoded.copyTo(it) } }
            }
            part.delete()
            part = plain
        }
        val info = unpack(part, folder, manifest)
        part.delete()
        return PackResult("updated", summary(info))
    }

    /** photopack._unpack: never outside the folder; then the .new/.old swap. */
    private fun unpack(zip: File, folder: File, manifest: JsonObject): JsonObject {
        val fresh = File(folder.path + ".new")
        fresh.deleteRecursively()
        fresh.mkdirs()
        val root = fresh.canonicalPath + File.separator
        var photos = 0
        ZipFile(zip, Charsets.UTF_8).use { pack ->
            for (member in pack.entries()) {
                if (member.isDirectory) continue
                val target = File(fresh, member.name).canonicalFile
                if (!target.path.startsWith(root)) continue
                target.parentFile.mkdirs()
                pack.getInputStream(member).use { src -> target.outputStream().use { src.copyTo(it) } }
                if (!member.name.endsWith(".json")) photos++
            }
        }
        val index = readJson(File(fresh, PhotoPackStore.PACK_INDEX)) as? JsonObject
        val info = JsonObject(
            linkedMapOf(
                "sha256" to (manifest["sha256"] ?: JsonPrimitive("")),
                "made" to (manifest["made"] ?: JsonPrimitive("")),
                "photos" to JsonPrimitive(photos),
                "artists" to JsonPrimitive((index?.get("artists") as? JsonObject)?.size ?: 0),
                "installed" to JsonPrimitive(SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(today())),
            ),
        )
        writeJsonAtomic(File(fresh, PhotoPackStore.INSTALLED), info)
        val old = File(folder.path + ".old")
        old.deleteRecursively()
        if (folder.isDirectory && !folder.renameTo(old)) throw IOException("couldn't move the old pack aside")
        if (!fresh.renameTo(folder)) throw IOException("couldn't put the new pack in place")
        old.deleteRecursively()
        return info
    }

    /** photopack.BioUpdate: bio-fixes.json, kept if it changed. */
    fun updateBios(address: String): PackResult = try {
        http.newCall(request(address)).execute().use { r ->
            if (r.code == 404) return PackResult("none", "none published yet")
            if (!r.isSuccessful) throw HttpFailure(r.code)
            val text = r.body!!.string()
            val data = runCatching { FreqJson.parseToJsonElement(text) as? JsonObject }.getOrNull()
            val artists = data?.get("artists") as? JsonObject ?: return PackResult("failed", "the bio fixes file isn't one")
            val count = artists.size
            val sha = data["sha256"].str().orEmpty()
            if (sha.isNotEmpty() && sha == store.bioInfo()?.first) return PackResult("current", "$count artists")
            writeJsonAtomic(store.bioFile, data)
            PackResult("updated", "$count artists, made ${data["made"].str() ?: "?"}")
        }
    } catch (e: Exception) {
        PackResult("failed", why(e))
    }

    private fun request(url: String) = Request.Builder().url(url).header("User-Agent", AGENT).build()

    private fun getJson(url: String): JsonObject? = http.newCall(request(url)).execute().use { r ->
        if (!r.isSuccessful) throw HttpFailure(r.code)
        runCatching { FreqJson.parseToJsonElement(r.body!!.string()) as? JsonObject }.getOrNull()
    }

    private class HttpFailure(val code: Int) : IOException("HTTP $code")

    companion object {
        const val AGENT = "Frequencies (radio app)"

        fun summary(info: JsonObject): String =
            "${info["photos"].num()?.toInt() ?: 0} photos, ${info["artists"].num()?.toInt() ?: 0} artists, made ${info["made"].str()?.ifEmpty { null } ?: "?"}"

        /** photopack._why: a failure in a few plain words. */
        fun why(e: Exception): String = when (e) {
            is HttpFailure -> if (e.code == 404) "the pack isn't at its address (404)" else "the server answered ${e.code}"
            is java.net.UnknownHostException, is java.net.ConnectException, is java.net.SocketTimeoutException ->
                "couldn't reach the pack's address (offline?)"
            is IllegalArgumentException -> "the pack address doesn't point at a pack"
            else -> (e.message?.trim()?.ifEmpty { null } ?: e.javaClass.simpleName).let { if (it.length < 160) it else it.take(157) + "…" }
        }

        private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) }
    }
}

/**
 * Base32 (RFC 4648) undone as it streams: the pack is stored as the zip
 * in base32, twice (the user's choice: nothing on GitHub looks like a
 * photo), so two of these in a row give back the zip.
 */
class Base32Input(input: InputStream) : FilterInputStream(input) {
    private val out = ByteArray(5)
    private var outLen = 0
    private var outPos = 0
    private var done = false

    override fun read(): Int {
        if (outPos >= outLen && !fill()) return -1
        return out[outPos++].toInt() and 0xff
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (len == 0) return 0
        var n = 0
        while (n < len) {
            if (outPos >= outLen && !fill()) break
            val take = minOf(len - n, outLen - outPos)
            System.arraycopy(out, outPos, b, off + n, take)
            outPos += take
            n += take
        }
        return if (n == 0) -1 else n
    }

    override fun available(): Int = outLen - outPos
    override fun markSupported(): Boolean = false

    /** One group of eight characters into up to five bytes. */
    private fun fill(): Boolean {
        if (done) return false
        val group = IntArray(8)
        var count = 0
        var pad = 0
        while (count < 8) {
            val c = `in`.read()
            if (c < 0) {
                if (count == 0) { done = true; return false }
                throw IOException("the pack's encoding is cut short")
            }
            val ch = c.toChar()
            if (ch == ' ' || ch == '\r' || ch == '\n' || ch == '\t') continue
            if (ch == '=') { pad++; group[count++] = 0; continue }
            if (pad > 0) throw IOException("the pack's encoding has stray characters")
            group[count++] = when (ch) {
                in 'A'..'Z' -> ch - 'A'
                in '2'..'7' -> ch - '2' + 26
                else -> throw IOException("the pack's encoding has stray characters")
            }
        }
        var bits = 0L
        for (v in group) bits = (bits shl 5) or v.toLong()
        val bytes = when (pad) { 0 -> 5; 1 -> 4; 3 -> 3; 4 -> 2; 6 -> 1; else -> throw IOException("the pack's encoding has stray characters") }
        for (i in 0 until 5) out[i] = (bits shr (32 - 8 * i)).toByte()
        outLen = bytes
        outPos = 0
        if (pad > 0) done = true
        return true
    }
}
