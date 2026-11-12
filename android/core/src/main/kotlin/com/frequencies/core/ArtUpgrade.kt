package com.frequencies.core

import java.net.URI
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** A decoded picture: ARGB pixels, row by row. */
class ArtImage(val width: Int, val height: Int, val pixels: IntArray, val hasAlpha: Boolean) {
    fun pixel(x: Int, y: Int) = pixels[y * width + x]
}

/**
 * artupgrade.py (desktop build 126): a station that came in by QR code,
 * share code or zip carries only the code's small copy of its picture.
 * This looks for the same picture full size online (the code's own link,
 * the station's directory entry, its website, TuneIn) and takes one only
 * when it is clearly the same image, compared on a small grid: color,
 * shape and edges, and the same shape of frame.
 */
object ArtMatch {
    const val FLAG = "art_from_code"
    const val CHECKED = "art_checked"
    const val RETRY_DAYS = 30
    const val FLOOR_PX = 120
    const val BIGGER_BY = 2
    const val ENOUGH_PX = 400
    const val SAVE_MAX_PX = 1200
    const val COLOR_LIMIT = 20.0
    const val SHAPE_LIMIT = 0.92
    const val EDGE_LIMIT = 0.85
    const val ASPECT_LIMIT = 0.12
    const val DETAIL_MIN = 4.0
    const val MAX_CANDIDATES = 24
    const val MAX_BYTES = 6 * 1024 * 1024
    const val AGENT = "Frequencies/1.5"
    const val BROWSER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    const val TUNEIN_SEARCH = "https://opml.radiotime.com/Search.ashx?render=json&query="

    /** color difference, gray correlation, edge correlation, aspect difference */
    data class Match(val color: Double, val shape: Double, val edges: Double, val aspect: Double) {
        val same: Boolean get() = color <= COLOR_LIMIT && shape >= SHAPE_LIMIT && edges >= EDGE_LIMIT && aspect <= ASPECT_LIMIT
        override fun toString() = "color %.0f, shape %.2f, edges %.2f".format(color, shape, edges)
    }

    private class Cells(val rgb: IntArray, val gray: IntArray, val edges: IntArray)

    // artupgrade._cells: the picture at the thumbnail's size, then at grid x grid.
    private fun cells(image: ArtImage, grid: Int, width: Int, height: Int, background: Int?): Cells {
        val small = scale(scale(image, width, height, background), grid, grid, null)
        val rgb = IntArray(grid * grid * 3)
        val gray = IntArray(grid * grid)
        for (i in 0 until grid * grid) {
            val p = small.pixels[i]
            val r = (p shr 16) and 0xff
            val g = (p shr 8) and 0xff
            val b = p and 0xff
            rgb[3 * i] = r; rgb[3 * i + 1] = g; rgb[3 * i + 2] = b
            gray[i] = (r * 11 + g * 16 + b * 5) / 32               // Qt's qGray
        }
        val edges = IntArray(grid * (grid - 1) * 2)
        var k = 0
        for (y in 0 until grid) for (x in 0 until grid - 1) edges[k++] = gray[y * grid + x + 1] - gray[y * grid + x]
        for (y in 0 until grid - 1) for (x in 0 until grid) edges[k++] = gray[(y + 1) * grid + x] - gray[y * grid + x]
        return Cells(rgb, gray, edges)
    }

    /**
     * An area-averaged resize (a box filter going down, bilinear going up),
     * near enough Qt's smooth scaling for a 12-32 cell grid. Alpha is drawn
     * over [background] when one is given, as artupgrade._flattened does;
     * without one, color is taken as it is (RGB32 ignores alpha).
     */
    private fun scale(src: ArtImage, w: Int, h: Int, background: Int?): ArtImage {
        val out = IntArray(w * h)
        val sx = src.width.toDouble() / w
        val sy = src.height.toDouble() / h
        for (y in 0 until h) {
            val y0 = y * sy
            val y1 = (y + 1) * sy
            for (x in 0 until w) {
                val x0 = x * sx
                val x1 = (x + 1) * sx
                var r = 0.0; var g = 0.0; var b = 0.0; var total = 0.0
                if (sx <= 1.0 && sy <= 1.0) {
                    // Upscaling: bilinear at the cell's center.
                    val cx = ((x + 0.5) * sx - 0.5).coerceIn(0.0, src.width - 1.0)
                    val cy = ((y + 0.5) * sy - 0.5).coerceIn(0.0, src.height - 1.0)
                    val ix = cx.toInt(); val iy = cy.toInt()
                    val fx = cx - ix; val fy = cy - iy
                    for ((px, py, wt) in listOf(
                        Triple(ix, iy, (1 - fx) * (1 - fy)), Triple(min(ix + 1, src.width - 1), iy, fx * (1 - fy)),
                        Triple(ix, min(iy + 1, src.height - 1), (1 - fx) * fy), Triple(min(ix + 1, src.width - 1), min(iy + 1, src.height - 1), fx * fy),
                    )) {
                        val p = flatten(src.pixel(px, py), background)
                        r += ((p shr 16) and 0xff) * wt; g += ((p shr 8) and 0xff) * wt; b += (p and 0xff) * wt; total += wt
                    }
                } else {
                    var py = y0.toInt()
                    while (py < y1 && py < src.height) {
                        val wy = min(y1, py + 1.0) - max(y0, py.toDouble())
                        var px = x0.toInt()
                        while (px < x1 && px < src.width) {
                            val wt = (min(x1, px + 1.0) - max(x0, px.toDouble())) * wy
                            if (wt > 0) {
                                val p = flatten(src.pixel(px, py), background)
                                r += ((p shr 16) and 0xff) * wt; g += ((p shr 8) and 0xff) * wt; b += (p and 0xff) * wt; total += wt
                            }
                            px++
                        }
                        py++
                    }
                }
                if (total <= 0) total = 1.0
                out[y * w + x] = (0xff shl 24) or ((r / total + 0.5).toInt().coerceIn(0, 255) shl 16) or
                    ((g / total + 0.5).toInt().coerceIn(0, 255) shl 8) or (b / total + 0.5).toInt().coerceIn(0, 255)
            }
        }
        return ArtImage(w, h, out, false)
    }

    private fun flatten(p: Int, background: Int?): Int {
        if (background == null) return p
        val a = (p ushr 24) and 0xff
        if (a == 255) return p
        fun mix(shift: Int) = (((p shr shift) and 0xff) * a + ((background shr shift) and 0xff) * (255 - a)) / 255
        return (0xff shl 24) or (mix(16) shl 16) or (mix(8) shl 8) or mix(0)
    }

    private fun correlation(a: IntArray, b: IntArray): Double {
        val ma = a.average()
        val mb = b.average()
        var sa = 0.0; var sb = 0.0; var sab = 0.0
        for (i in a.indices) {
            val da = a[i] - ma
            val db = b[i] - mb
            sa += da * da; sb += db * db; sab += da * db
        }
        if (sqrt(sa) < 1e-6 || sqrt(sb) < 1e-6) return 0.0
        return sab / (sqrt(sa) * sqrt(sb))
    }

    /** artupgrade.detail: how much there is to match on (a plain color is ~0). */
    fun detail(thumb: ArtImage): Double {
        val gray = cells(thumb, 16, thumb.width, thumb.height, null).gray
        val mean = gray.average()
        return sqrt(gray.sumOf { (it - mean) * (it - mean) } / gray.size)
    }

    /** artupgrade.compare: [image] against the code's [thumb]. */
    fun compare(thumb: ArtImage, image: ArtImage): Match {
        val width = thumb.width
        val height = thumb.height
        val grid = max(12, min(32, min(width, height) / 2))
        val mine = cells(thumb, grid, width, height, null)
        val backgrounds = if (image.hasAlpha) listOf(null, 0xff000000.toInt(), 0xffffffff.toInt()) else listOf(null)
        var best: Triple<Double, Double, Double>? = null
        for (bg in backgrounds) {
            val c = cells(image, grid, width, height, bg)
            var diff = 0.0
            for (i in c.rgb.indices) diff += abs(mine.rgb[i] - c.rgb[i])
            val d = Triple(diff / c.rgb.size, correlation(mine.gray, c.gray), correlation(mine.edges, c.edges))
            if (best == null || d.second + d.third - d.first / 50 > best.second + best.third - best.first / 50) best = d
        }
        val a = width.toDouble() / max(1, height)
        val b = image.width.toDouble() / max(1, image.height)
        return Match(best!!.first, best.second, best.third, abs(a - b) / max(a, b))
    }

    /** artupgrade.page_images: og:image, twitter:image and icon links in a page. */
    fun pageImages(page: String, base: String): List<String> {
        val out = ArrayList<String>()
        val patterns = listOf(
            Regex("""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)(?::src)?["'][^>]*>""", RegexOption.IGNORE_CASE),
            Regex("""<link[^>]+rel=["'][^"']*(?:apple-touch-icon|icon)[^"']*["'][^>]*>""", RegexOption.IGNORE_CASE),
        )
        val value = Regex("""(?:content|href)=["']([^"']+)["']""", RegexOption.IGNORE_CASE)
        for (p in patterns) for (tag in p.findAll(page)) {
            val m = value.find(tag.value) ?: continue
            runCatching { URI(base).resolve(unescape(m.groupValues[1].trim())).toString() }.getOrNull()?.let(out::add)
        }
        return out
    }

    private fun unescape(s: String) = s.replace("&amp;", "&").replace("&quot;", "\"").replace("&#39;", "'")
        .replace("&lt;", "<").replace("&gt;", ">")

    /** artupgrade.tunein_logos: the best few matches' logos, largest first. */
    fun tuneInLogos(data: JsonObject?, name: String, limit: Int = 3): List<String> {
        val rows = (data?.get("body") as? JsonArray)?.filterIsInstance<JsonObject>()
            ?.filter { !it["image"].str().isNullOrEmpty() && (it["type"].str() ?: "audio") == "audio" }.orEmpty()
        val wanted = name.lowercase()
        val sorted = rows.sortedBy { wanted !in it["text"].str().orEmpty().lowercase() }
        val out = ArrayList<String>()
        for (row in sorted.take(limit)) {
            val image = row["image"].str()!!
            if ("/logoq." in image) listOf("g", "d").forEach { out.add(image.replace("/logoq.", "/logo$it.")) }
            out.add(image)
        }
        return out
    }

    /** feeds._site_icons: icons from the station's website (or its stream's host). */
    fun siteIcons(station: Station): List<String> {
        val homepage = station["homepage"].str().orEmpty().trim().ifEmpty { station.url }
        val uri = runCatching { URI(homepage) }.getOrNull() ?: return emptyList()
        if (uri.scheme !in listOf("http", "https") || uri.rawAuthority.isNullOrEmpty()) return emptyList()
        val root = "${uri.scheme}://${uri.rawAuthority}"
        return listOf("$root/apple-touch-icon.png", "$root/apple-touch-icon-precomposed.png", "$root/favicon.png", "$root/favicon.ico")
    }

    /** artupgrade.carries_thumbnail: still wearing the code's small copy, in the art folder. */
    fun carriesThumbnail(station: Station, artDir: java.io.File): Boolean {
        val path = station.customArtPath
        if (!truthy(station[FLAG]) || path.isEmpty()) return false
        val file = java.io.File(path)
        return file.isFile && file.absoluteFile.parentFile == artDir.absoluteFile && file.name.startsWith(station.id.ifEmpty { "?" })
    }

    /** Due a look: never checked, checked over 30 days ago, or asked for. */
    fun due(station: Station, nowSeconds: Double, force: Boolean = false): Boolean {
        val checked = station[CHECKED].num() ?: 0.0
        return force || checked <= 0 || nowSeconds - checked >= RETRY_DAYS * 86400.0
    }

    data class Result(
        /** found, none, offline, flat, unreadable or error */
        val status: String,
        val image: ArtImage? = null,
        val bytes: ByteArray? = null,
        val url: String = "",
        val via: String = "",
        val thumb: String = "",
        val match: Match? = null,
        val looked: Int = 0,
        val notes: List<String> = emptyList(),
    )
}

/**
 * The online part of artupgrade: where to look, and the search itself.
 * [decode] reads picture bytes (BitmapFactory on the phone, ImageIO in tests).
 */
class ArtFinder(
    private val decode: (ByteArray) -> ArtImage?,
    private val directory: RadioBrowser = RadioBrowser(),
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build(),
    private val tuneInSearch: String = ArtMatch.TUNEIN_SEARCH,
) {
    fun get(url: String, agent: String = ArtMatch.AGENT, limit: Int = ArtMatch.MAX_BYTES): ByteArray {
        http.newCall(Request.Builder().url(url).header("User-Agent", agent).build()).execute().use { r ->
            if (!r.isSuccessful) throw java.io.IOException("HTTP ${r.code}")
            val body = r.body ?: throw java.io.IOException("empty")
            val length = body.contentLength()
            if (length > limit) throw java.io.IOException("too big")
            val stream = body.byteStream()
            val out = java.io.ByteArrayOutputStream()
            val buf = ByteArray(65536)
            while (true) {
                val n = stream.read(buf)
                if (n < 0) break
                out.write(buf, 0, n)
                if (out.size() > limit) throw java.io.IOException("too big")
            }
            return out.toByteArray()
        }
    }

    /**
     * artupgrade.candidates: picture links worth trying, best sources first,
     * and whether any search got an answer (false = probably offline).
     */
    fun candidates(station: Station, name: String, notes: MutableList<String>): Pair<List<String>, Boolean> {
        val urls = ArrayList<String>()
        var asked = 0
        station.favicon.trim().takeIf { it.startsWith("http") }?.let(urls::add)
        var homepage = station["homepage"].str().orEmpty().trim()
        try {
            var rows = emptyList<RadioBrowser.Result>()
            val stream = station.url.trim()
            if (stream.startsWith("http")) {
                rows = try { directory.byUrl(stream).also { asked++ } } catch (e: Exception) { emptyList() }
            }
            val same = rows.filter { it.favicon.isNotEmpty() }
            if (same.isEmpty() && name.isNotEmpty()) {
                rows = directory.search(name, limit = 8)
                asked++
            }
            for (row in same.ifEmpty { rows }) {
                row.favicon.trim().takeIf { it.isNotEmpty() }?.let(urls::add)
                if (homepage.isEmpty() && row.homepage.isNotEmpty()) homepage = row.homepage
            }
        } catch (e: Exception) {
            notes.add("directory: ${e.javaClass.simpleName}")
        }
        if (homepage.startsWith("http")) {
            try {
                val page = get(homepage, ArtMatch.BROWSER_AGENT, 1024 * 1024)
                asked++
                urls.addAll(ArtMatch.pageImages(String(page, Charsets.UTF_8), homepage))
            } catch (e: Exception) {
                notes.add("homepage: ${e.javaClass.simpleName}")
            }
        }
        val withHome = station.copy().also { if (homepage.isNotEmpty()) it["homepage"] = kotlinx.serialization.json.JsonPrimitive(homepage) }
        urls.addAll(ArtMatch.siteIcons(withHome).take(2))
        if (name.isNotEmpty()) {
            try {
                val data = FreqJson.parseToJsonElement(String(get(tuneInSearch + SongHistory.quote(name), ArtMatch.AGENT, 1 shl 20), Charsets.UTF_8))
                asked++
                urls.addAll(ArtMatch.tuneInLogos(data as? JsonObject, name))
            } catch (e: Exception) {
                notes.add("TuneIn: ${e.javaClass.simpleName}")
            }
        }
        return urls.filter { it.startsWith("http") }.distinct().take(ArtMatch.MAX_CANDIDATES) to (asked > 0)
    }

    /** artupgrade._UpgradeWorker._search: the full-size copy of [thumbBytes], if one can be found. */
    fun search(station: Station, thumbBytes: ByteArray): ArtMatch.Result {
        val notes = ArrayList<String>()
        val thumb = decode(thumbBytes) ?: return ArtMatch.Result("unreadable")
        val tw = thumb.width
        val th = thumb.height
        val size = "${tw}x$th"
        val flat = ArtMatch.detail(thumb) < ArtMatch.DETAIL_MIN
        val link = station.artUrl.trim()
        if (link.startsWith("http")) {
            try {
                val bytes = get(link)
                val image = decode(bytes)
                if (image != null) {
                    val d = ArtMatch.compare(thumb, image)
                    val bigger = min(image.width, image.height) > max(tw, th)
                    if (bigger && (d.same || flat && d.aspect <= ArtMatch.ASPECT_LIMIT)) {
                        return ArtMatch.Result("found", image, bytes, link, "the code's link", size, d, 0, notes)
                    }
                    notes.add("the code's link showed another picture")
                } else {
                    notes.add("the code's link gave no picture")
                }
            } catch (e: Exception) {
                notes.add("the code's link: ${e.javaClass.simpleName}")
            }
        }
        if (flat) return ArtMatch.Result("flat", thumb = size, notes = notes)

        val need = max(ArtMatch.FLOOR_PX, ArtMatch.BIGGER_BY * min(tw, th))
        val name = station.name.trim().ifEmpty { station.displayName }
        val (urls, searched) = candidates(station, name, notes)
        var best: ArtMatch.Result? = null
        var bestSize = 0
        var looked = 0
        for (url in urls) {
            val bytes = try { get(url) } catch (e: Exception) { continue }
            val image = decode(bytes) ?: continue
            looked++
            val side = min(image.width, image.height)
            if (side < need) continue
            val d = ArtMatch.compare(thumb, image)
            if (!d.same) continue
            if (best == null || side > bestSize) {
                best = ArtMatch.Result("found", image, bytes, url, "", size, d, 0, notes)
                bestSize = side
            }
            if (side >= ArtMatch.ENOUGH_PX) break
        }
        return best?.copy(looked = looked) ?: ArtMatch.Result(if (searched) "none" else "offline", thumb = size, looked = looked, notes = notes)
    }

    /** "Find a picture": every place a picture of this station might be, for the user to choose from. */
    fun pictureLinks(station: Station): List<String> {
        val name = station.name.trim().ifEmpty { station.displayName }
        val (urls, _) = candidates(station, name, ArrayList())
        return (listOf(station.artUrl.trim()).filter { it.startsWith("http") } + urls).distinct()
    }
}
