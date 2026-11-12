package com.frequencies.core

import java.io.StringReader
import javax.xml.parsers.DocumentBuilderFactory
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.w3c.dom.Element
import org.w3c.dom.Node
import org.xml.sax.InputSource

/**
 * Other apps' station lists in and out (playlists.py): M3U, PLS, XSPF,
 * OPML, Goodvibes, Rhythmbox and RadioDroid / radio-browser JSON. Found
 * by content, not by file name. Frequencies backups and share zips have
 * their own readers (Backup, ShareZip).
 */
object Imports {
    class ImportError(message: String) : Exception(message)

    /** What a reader found: one station before it becomes a saved frequency. */
    data class Entry(
        val name: String,
        val url: String,
        val favicon: String = "",
        val tags: List<String> = emptyList(),
        val rbUuid: String = "",
        val homepage: String = "",
        val insecure: Boolean = false,
    )

    val FORMAT_NAMES = linkedMapOf(
        "m3u" to "M3U / M3U8",
        "pls" to "PLS",
        "xspf" to "XSPF",
        "opml" to "OPML",
        "goodvibes" to "Goodvibes",
        "rhythmbox" to "Rhythmbox",
        "radiodroid" to "JSON (RadioDroid, radio-browser)",
    )

    /** What the phone can write: playlists.EXPORT_FORMATS without the backup (Settings has that). */
    val EXPORT_FORMATS = listOf("m3u", "pls", "xspf", "opml", "goodvibes", "radiodroid")

    fun extension(format: String) = when (format) {
        "goodvibes" -> ".xml"; "m3u" -> ".m3u"; "pls" -> ".pls"; "xspf" -> ".xspf"; "opml" -> ".opml"
        else -> ".json"
    }

    fun mimeType(format: String) = when (format) {
        "m3u" -> "audio/x-mpegurl"; "pls" -> "audio/x-scpls"; "xspf" -> "application/xspf+xml"
        "opml" -> "text/x-opml"; "goodvibes" -> "application/xml"; else -> "application/json"
    }

    private val UUID_RE = Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", RegexOption.IGNORE_CASE)
    private val PAGE_HOSTS = listOf("opml.radiotime.com", "tunein.com", "radiotime.com")

    private fun isUrl(text: String?) = Playlists.isUrl(text)

    fun sniff(text: String): String {
        val head = text.trimStart().take(400).lowercase()
        return when {
            "[playlist]" in head -> "pls"
            head.startsWith("{") || head.startsWith("[") -> "radiodroid"
            "<stations" in head -> "goodvibes"
            "<opml" in head -> "opml"
            "<rhythmdb" in head -> "rhythmbox"
            "<playlist" in head || "xspf" in head -> "xspf"
            else -> "m3u"
        }
    }

    /** (format, entries) for a file's text; [ImportError] with a plain reason when nothing is in it. */
    fun read(text: String): Pair<String, List<Entry>> {
        if (text.isBlank()) throw ImportError("That file is empty.")
        val format = sniff(text)
        var skipped = 0
        val entries = when (format) {
            "pls" -> readPls(text)
            "radiodroid" -> readJson(text)
            "goodvibes" -> readGoodvibes(text)
            "opml" -> readOpml(text).also { skipped = it.second }.first
            "rhythmbox" -> readRhythmbox(text)
            "xspf" -> readXspf(text)
            else -> readM3u(text)
        }
        if (entries.isEmpty() && skipped > 0) {
            throw ImportError(
                "That file only has links to web pages (like TuneIn), not streams. Open the station's page, " +
                    "copy its stream address and add it by its address instead.",
            )
        }
        if (entries.isEmpty()) throw ImportError("No stream addresses found in that file.")
        return format to entries
    }

    /** playlists._clean_tags: a string ("jazz, smooth"), or a list, as tidy lowercase tags. */
    fun cleanTags(value: Any?): List<String> {
        val parts = when (value) {
            null -> return emptyList()
            is String -> value.split(Regex("[,;|]"))
            is List<*> -> value.map { it.toString() }
            else -> return emptyList()
        }
        val out = ArrayList<String>()
        for (p in parts) {
            val tag = p.trim().trim('#').lowercase()
            if (tag.isNotEmpty() && tag !in out) out.add(tag)
        }
        return out.take(12)
    }

    fun readM3u(text: String): List<Entry> {
        data class Row(var name: String, var url: String, val favicon: String, val tags: String)
        val rows = ArrayList<Row>()
        var pendingName = ""
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.isEmpty()) continue
            if (line.uppercase().startsWith("#EXTINF")) {
                val masked = Regex("\"[^\"]*\"").replace(line) { " ".repeat(it.value.length) }
                val comma = masked.indexOf(',')
                pendingName = if (comma >= 0) line.substring(comma + 1).trim() else ""
                val logo = Regex("tvg-logo=\"([^\"]+)\"").find(line)?.groupValues?.get(1).orEmpty()
                val group = Regex("group-title=\"([^\"]+)\"").find(line)?.groupValues?.get(1).orEmpty()
                rows.add(Row(pendingName, "", logo, group))
                continue
            }
            if (line.startsWith("#")) continue
            if (rows.isNotEmpty() && rows.last().url.isEmpty()) rows.last().url = line
            else rows.add(Row(pendingName.ifEmpty { line }, line, "", ""))
            pendingName = ""
        }
        return rows.filter { isUrl(it.url) }.map { Entry(it.name.ifEmpty { it.url }, it.url, it.favicon, cleanTags(it.tags)) }
    }

    fun readPls(text: String): List<Entry> {
        val rows = sortedMapOf<Int, MutableMap<String, String>>()
        var inSection = false
        var sawSection = false
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.startsWith("[")) {
                inSection = line.equals("[playlist]", ignoreCase = true)
                sawSection = sawSection || inSection
                continue
            }
            if (!inSection) continue
            val m = Regex("^(file|title)(\\d+)\\s*[=:]\\s*(.*)$", RegexOption.IGNORE_CASE).find(line) ?: continue
            rows.getOrPut(m.groupValues[2].toInt()) { HashMap() }[m.groupValues[1].lowercase()] = m.groupValues[3].trim()
        }
        if (!sawSection) throw ImportError("That PLS file has no [playlist] section.")
        return rows.values.mapNotNull { r ->
            val url = r["file"].orEmpty()
            if (isUrl(url)) Entry(r["title"]?.takeIf { it.isNotEmpty() } ?: url, url) else null
        }
    }

    private fun xml(text: String): Element = try {
        val factory = DocumentBuilderFactory.newInstance()
        factory.isNamespaceAware = true
        runCatching { factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true) }
        factory.newDocumentBuilder().parse(InputSource(StringReader(text))).documentElement
    } catch (e: Exception) {
        throw ImportError("That file couldn't be read (${e.message ?: "not valid XML"}).")
    }

    private fun Node.tagName(): String = (localName ?: nodeName).substringAfter(':').lowercase()

    private fun Element.children(): List<Element> =
        (0 until childNodes.length).map { childNodes.item(it) }.filterIsInstance<Element>()

    private fun Element.descendants(name: String): List<Element> {
        val out = ArrayList<Element>()
        fun walk(e: Element) { for (c in e.children()) { if (c.tagName() == name) out.add(c); walk(c) } }
        if (tagName() == name) out.add(this)
        walk(this)
        return out
    }

    private fun Element.attrs(): Map<String, String> =
        (0 until attributes.length).associate { attributes.item(it).let { a -> a.nodeName.lowercase() to a.nodeValue.trim() } }

    fun readXspf(text: String): List<Entry> = xml(text).descendants("track").mapNotNull { track ->
        var name = ""; var url = ""; var image = ""; var tags = ""
        for (c in track.children()) {
            val value = c.textContent.trim()
            when (c.tagName()) {
                "title" -> name = value
                "location" -> url = value
                "image" -> image = value
                "meta" -> if (c.getAttribute("rel").trimEnd('/').endsWith("tags")) tags = value
            }
        }
        if (isUrl(url)) Entry(name.ifEmpty { url }, url, image, cleanTags(tags)) else null
    }

    fun readGoodvibes(text: String): List<Entry> = xml(text).descendants("station").mapNotNull { node ->
        val f = node.children().associate { it.tagName() to it.textContent.trim() }
        val url = f["uri"].orEmpty()
        if (isUrl(url)) Entry(f["name"]?.takeIf { it.isNotEmpty() } ?: url, url, insecure = f["insecure"]?.lowercase() in setOf("true", "1", "yes")) else null
    }

    private fun isPageUrl(url: String): Boolean {
        val host = url.lowercase().replace(Regex("^[a-z][a-z0-9+.\\-]*://"), "").substringBefore('/').substringBefore(':')
        return PAGE_HOSTS.any { host == it || host.endsWith(".$it") }
    }

    /** OPML outlines; a folder's name becomes a tag. Second: how many TuneIn page links were left out. */
    fun readOpml(text: String): Pair<List<Entry>, Int> {
        val out = ArrayList<Entry>()
        var skipped = 0
        fun walk(node: Element, folders: List<String>) {
            for (child in node.children()) {
                if (child.tagName() != "outline") continue
                val a = child.attrs()
                val url = a["url"] ?: a["xmlurl"] ?: a["htmlurl"] ?: ""
                val label = a["text"] ?: a["title"] ?: a["name"] ?: ""
                if (isUrl(url)) {
                    if (isPageUrl(url)) skipped++
                    else {
                        var tags = a["category"] ?: a["tags"] ?: a["genre"] ?: ""
                        if (tags.isEmpty() && folders.isNotEmpty()) tags = folders.joinToString(", ")
                        out.add(Entry(label.ifEmpty { url }, url, a["image"] ?: a["logo"] ?: a["imageurl"] ?: "", cleanTags(tags)))
                    }
                }
                walk(child, if (label.isNotEmpty() && !isUrl(url)) folders + label else folders)
            }
        }
        xml(text).descendants("body").firstOrNull()?.let { walk(it, emptyList()) }
        return out to skipped
    }

    fun readRhythmbox(text: String): List<Entry> = xml(text).descendants("entry").mapNotNull { node ->
        if (node.getAttribute("type") != "iradio") return@mapNotNull null
        val f = node.children().associate { it.tagName() to it.textContent.trim() }
        val url = f["location"].orEmpty()
        val genre = f["genre"].orEmpty()
        if (isUrl(url)) Entry(f["title"]?.takeIf { it.isNotEmpty() } ?: url, url, tags = cleanTags(if (genre.lowercase() == "unknown") "" else genre)) else null
    }

    /** RadioDroid / radio-browser exports (a Frequencies backup goes to Backup instead). */
    fun readJson(text: String): List<Entry> {
        val data = try { FreqJson.parseToJsonElement(text) } catch (e: Exception) { throw ImportError("That file isn't valid JSON.") }
        val rows: List<JsonElement> = when (data) {
            is JsonObject -> (data["stations"] as? JsonArray) ?: (data["items"] as? JsonArray) ?: JsonArray(emptyList())
            is JsonArray -> data
            else -> throw ImportError("That JSON isn't a station list.")
        }
        return rows.mapNotNull { row ->
            val r = row as? JsonObject ?: return@mapNotNull null
            fun s(vararg keys: String) = keys.firstNotNullOfOrNull { r[it].str()?.takeIf(String::isNotEmpty) }.orEmpty()
            val url = s("url_resolved", "url", "streamUrl", "stream_url", "urlResolved", "url-resolved")
            val name = s("custom_name", "name", "title").ifEmpty { url }
            val tagsValue: Any? = (r["tags"] as? JsonArray)?.mapNotNull { it.str() }
                ?: s("tags", "genre", "genres", "category").ifEmpty { null }
            val rb = s("stationuuid", "id").takeIf { UUID_RE.matches(it) }.orEmpty()
            if (isUrl(url)) Entry(name, url, s("favicon", "imageUrl", "favicon-url"), cleanTags(tagsValue), rb, s("homepage")) else null
        }
    }

    /** playlists.to_station: a new saved frequency (not yet placed in the list). */
    fun toStation(entry: Entry, newId: () -> String, now: Double): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(newId())
        s["name"] = JsonPrimitive(entry.name.ifEmpty { entry.url })
        s["url"] = JsonPrimitive(entry.url)
        s["favicon"] = JsonPrimitive(entry.favicon)
        s["insecure"] = JsonPrimitive(entry.insecure)
        s["rb_uuid"] = JsonPrimitive(entry.rbUuid)
        s["homepage"] = JsonPrimitive(entry.homepage)
        s["tags"] = stringArray(entry.tags)
        return Station.normalize(s, now, newId)
    }

    // -- writers ----------------------------------------------------------

    private fun nameOf(s: Station) = s.displayName

    private fun esc(text: String) = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

    fun write(stations: List<Station>, format: String): String = when (format) {
        "m3u" -> buildString {
            append("#EXTM3U\n")
            for (s in stations) {
                val logo = if (s.favicon.isNotEmpty()) " tvg-logo=\"${s.favicon}\"" else ""
                val tags = s.tags.joinToString(", ")
                val group = if (tags.isNotEmpty()) " group-title=\"$tags\"" else ""
                append("#EXTINF:-1$logo$group,${nameOf(s)}\n${s.url}\n")
            }
        }
        "pls" -> buildString {
            append("[playlist]\n")
            stations.forEachIndexed { i, s -> append("File${i + 1}=${s.url}\nTitle${i + 1}=${nameOf(s)}\nLength${i + 1}=-1\n") }
            append("NumberOfEntries=${stations.size}\nVersion=2\n")
        }
        "xspf" -> buildString {
            append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<playlist version=\"1\" xmlns=\"http://xspf.org/ns/0/\">\n")
            append("  <title>Frequencies</title>\n  <trackList>\n")
            for (s in stations) {
                append("    <track>\n      <title>${esc(nameOf(s))}</title>\n      <location>${esc(s.url)}</location>\n")
                if (s.favicon.isNotEmpty()) append("      <image>${esc(s.favicon)}</image>\n")
                val tags = s.tags.joinToString(", ")
                if (tags.isNotEmpty()) append("      <meta rel=\"http://frequencies.invalid/ns/tags\">${esc(tags)}</meta>\n")
                append("    </track>\n")
            }
            append("  </trackList>\n</playlist>\n")
        }
        "opml" -> buildString {
            append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<opml version=\"2.0\">\n  <head><title>Frequencies</title></head>\n  <body>\n")
            for (s in stations) {
                val attrs = mutableListOf("type" to "audio", "text" to nameOf(s), "URL" to s.url)
                val tags = s.tags.joinToString(", ")
                if (tags.isNotEmpty()) attrs.add("category" to tags)
                if (s.favicon.isNotEmpty()) attrs.add("image" to s.favicon)
                append("    <outline ${attrs.joinToString(" ") { (k, v) -> "$k=\"${esc(v)}\"" }}/>\n")
            }
            append("  </body>\n</opml>\n")
        }
        "goodvibes" -> buildString {
            append("<Stations>\n")
            for (s in stations) {
                append("  <Station>\n    <uri>${esc(s.url)}</uri>\n    <name>${esc(nameOf(s))}</name>\n")
                if (s.insecure) append("    <insecure>true</insecure>\n")
                append("  </Station>\n")
            }
            append("</Stations>\n")
        }
        "radiodroid" -> encodeJson(
            JsonArray(
                stations.map { s ->
                    JsonObject(
                        linkedMapOf(
                            "name" to JsonPrimitive(nameOf(s)),
                            "url" to JsonPrimitive(s.url),
                            "url_resolved" to JsonPrimitive(s.url),
                            "favicon" to JsonPrimitive(s.favicon),
                            "tags" to JsonPrimitive(s.tags.joinToString(",")),
                            "stationuuid" to JsonPrimitive(s["rb_uuid"].str().orEmpty()),
                        ),
                    )
                },
            ),
        )
        else -> throw ImportError("Don't know how to write '$format' files.")
    }
}
