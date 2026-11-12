package com.frequencies.core

import java.net.URLDecoder
import java.net.URLEncoder
import java.text.Normalizer
import java.time.LocalDateTime
import kotlinx.serialization.json.JsonArray

/**
 * The lists Android Auto browses and the station a spoken "play …" picks.
 * The app turns these into media items; everything here is plain lists of
 * stations so it can be checked without a phone.
 *
 * A station inside a list has the id `<list>/<station id>`, so playing it
 * queues that list and next/previous move along it.
 */
object Browse {
    // Compiled once: building a Regex is slow, and A-Z parts sort hundreds of names (build 185: the car froze the app).
    private val MARKS = Regex("\\p{M}+")
    private val APOSTROPHES = Regex("['’`]")
    private val OTHER = Regex("[^\\p{L}\\p{N}.]+")
    private val DOTS = Regex("(?<!\\d)\\.|\\.(?!\\d)")
    private val SPACES = Regex("\\s+")
    private val RECENT_NUMBER = Regex("^\\d+:")

    const val SAVED = "saved"
    const val RECENT = "recent"
    const val TAGS = "tags"
    /** Frequencies with a scheduled show on the air now (the Schedule). */
    const val LIVE = "live"
    private const val TAG = "tag:"

    /** How many recently played frequencies the Recent tab lists. */
    const val RECENT_MOST = 20

    /** Saved with a tag filter: `savedf:` + the url-encoded Search query (`tag:"80s" -tag:"talk"`). */
    private const val FILTER = "savedf:"
    /** `<list>@<n>`: the n-th A-Z part of a list too long to hand over in one piece. */
    private const val PART = '@'
    /** `savedf:<query>~<n>`: Saved whose tag strip starts at the n-th tag. Url-encoding turns a '~' in a tag into %7E. */
    private const val STRIP = '~'
    /** Tags the strip shows at once, between its arrows: two rows of four (user, build 185). */
    const val STRIP_TAGS = 8

    /**
     * The most frequencies one list hands to Android Auto in one piece
     * (batch 24). Media3 cuts any list it sends Auto at 256 KB without a
     * word (MediaUtils.TRANSACTION_SIZE_LIMIT_IN_BYTES), about 300 of our
     * stations; 150 stays well clear. A longer list shows its first 150
     * and then A-Z parts that hold every one.
     */
    const val PAGE_MOST = 150

    /** Songs the Recent tab lists (song history, newest first). */
    const val RECENT_SONGS = 60

    /** The Saved tab: every frequency in its manual order. */
    fun saved(stations: List<Station>, order: String = "manual"): List<Station> = sortStations(stations, order)

    /** Saved's order in the car (Settings > Appearance > Android Auto, user build 197): its own, most played first unless chosen. */
    const val CAR_ORDER_DEFAULT = "play_count"

    fun carOrder(settings: Settings): String =
        settings.string("car_sort_mode", CAR_ORDER_DEFAULT).takeIf { it in SORT_LABELS } ?: CAR_ORDER_DEFAULT

    /** The Recent tab: what was played last first, never-played ones left out. */
    fun recent(stations: List<Station>): List<Station> =
        stations.filter { it.lastPlayed > 0 }.sortedByDescending { it.lastPlayed }.take(RECENT_MOST)

    /** The Live now tab: frequencies with a show on the air at [now], in manual order. */
    fun live(stations: List<Station>, now: LocalDateTime = LocalDateTime.now()): List<Station> =
        Schedule.liveNow(saved(stations), now).map { it.station }

    /** Every tag in use, each once (first spelling wins), A to Z. */
    fun tags(stations: List<Station>): List<String> {
        val seen = LinkedHashMap<String, String>()
        for (s in stations) for (t in s.tags) seen.putIfAbsent(t.lowercase(), t)
        return seen.values.sortedBy { it.lowercase() }
    }

    /** The frequencies with [tag], in manual order. */
    fun tagged(stations: List<Station>, tag: String): List<Station> =
        saved(stations).filter { s -> s.tags.any { it.equals(tag, ignoreCase = true) } }

    fun tagListId(tag: String): String = TAG + URLEncoder.encode(tag, "UTF-8")

    /** The tag a [tagListId] names, or null. */
    fun tagOf(listId: String): String? =
        if (listId.startsWith(TAG)) URLDecoder.decode(listId.removePrefix(TAG), "UTF-8") else null

    /**
     * A list's frequencies by its id (saved, a tag filter on Saved, recent,
     * live, tag:…, or an A-Z part of any of them); null for an unknown id.
     * Recent is the frequencies the recent songs were heard on.
     */
    fun list(stations: List<Station>, listId: String, now: LocalDateTime = LocalDateTime.now(), history: JsonArray = JsonArray(emptyList()), order: String = "manual"): List<Station>? {
        partOf(listId)?.let { (base, n) -> return list(stations, base, now, history, order)?.let { parts(it, base).getOrNull(n)?.stations } }
        // Saved (with or without tags) in [order]: the car's own order there.
        filterOf(listId)?.let { return Search.filter(saved(stations, order), it) }
        return when (listId) {
            RECENT -> recentSongs(stations, history).mapNotNull { it.station }.distinctBy { it.id }
            LIVE -> live(stations, now)
            else -> tagOf(listId)?.let { tagged(stations, it) }
        }
    }

    fun itemId(listId: String, station: Station): String = "$listId/${station.id}"

    // -- Saved's tag pills (batch 24) ------------------------------------------

    /** The id of Saved with [query] as its tag filter (plain Saved when it has none). */
    fun filterId(query: String, at: Int = 0): String {
        val q = if (Search.tokens(query).isEmpty()) "" else query
        if (q.isEmpty() && at <= 0) return SAVED
        return FILTER + URLEncoder.encode(q, "UTF-8") + if (at > 0) "$STRIP$at" else ""
    }

    /** The tag filter a Saved id carries ("" for plain Saved), or null for any other id. */
    fun filterOf(listId: String): String? = when {
        listId == SAVED -> ""
        // Url-encoded, so a '/' means a station inside the list (`<list>/<station id>`), not the list.
        listId.startsWith(FILTER) && '/' !in listId && PART !in listId ->
            URLDecoder.decode(listId.removePrefix(FILTER).substringBefore(STRIP), "UTF-8")
        else -> null
    }

    private const val TAGS_PAGE = "tagsf:"

    /**
     * A page of Auto's Tags tab (user, build 187: the tag tiles live there,
     * Saved is a plain list that follows them): opening it sets the car's tag
     * filter to [query] and shows the tiles again in their new states.
     */
    fun tagsPageId(query: String): String = TAGS_PAGE + URLEncoder.encode(query, "UTF-8")

    /** The tag filter a Tags page id sets, or null for any other id. */
    fun tagsPageOf(listId: String): String? =
        if (listId.startsWith(TAGS_PAGE) && '/' !in listId) URLDecoder.decode(listId.removePrefix(TAGS_PAGE), "UTF-8") else null

    private const val TAG_FLIP = "tagflip:"

    /**
     * A tag tile in Auto's Tags tab (build 188): a playable item, so only a
     * real tap reaches the app (Auto opens and prefetches folders by itself,
     * which flipped tags on its own and stacked pages on Back in 187). The
     * tap walks the tag to its next state; nothing starts playing.
     */
    fun tagFlipId(tag: String): String = TAG_FLIP + URLEncoder.encode(tag, "UTF-8")

    /** The tag a tag tile's id names, or null for any other id. */
    fun tagFlipOf(id: String): String? =
        if (id.startsWith(TAG_FLIP) && '/' !in id) URLDecoder.decode(id.removePrefix(TAG_FLIP), "UTF-8").ifEmpty { null } else null

    /** Where the tag strip of a Saved id starts (its first tag's place, A to Z). */
    fun stripAt(listId: String): Int =
        if (filterOf(listId) == null) 0 else listId.substringAfter(STRIP, "").toIntOrNull()?.coerceAtLeast(0) ?: 0

    /**
     * What a tap on a tag pill does in the car. Auto's lists have no long
     * press, so one tap walks the desktop's two gestures in turn: off ->
     * only these (tap) -> hide these (hold) -> off.
     */
    fun nextTagState(state: Int): Int = when (state) {
        Search.NEUTRAL -> Search.INCLUDED
        Search.INCLUDED -> Search.EXCLUDED
        else -> Search.NEUTRAL
    }

    /** One tag pill: its state in [query] and the Saved filter a tap opens. */
    data class Pill(val tag: String, val state: Int, val target: String)

    fun pills(stations: List<Station>, query: String, at: Int = 0): List<Pill> = tags(stations).map { tag ->
        val state = Search.tagState(query, tag)
        Pill(tag, state, filterId(Search.setTag(query, tag, nextTagState(state)), at))
    }

    /**
     * Saved's tag strip in the car (user, build 185: "< tag tag tag tag >"
     * over a second row of four, arrows on the sides): [STRIP_TAGS] pills
     * from [stripAt], and the Saved ids the arrows open (null: no arrows, 8 tags or fewer).
     * A pill tap keeps the strip where it is.
     */
    data class Strip(val back: String?, val pills: List<Pill>, val next: String?)

    fun strip(stations: List<Station>, listId: String): Strip? {
        val query = filterOf(listId) ?: return null
        val all = tags(stations)
        if (all.isEmpty()) return null
        val last = ((all.size - 1) / STRIP_TAGS) * STRIP_TAGS
        val at = (stripAt(listId) / STRIP_TAGS * STRIP_TAGS).coerceIn(0, last)
        val pills = pills(stations, query, at).subList(at, minOf(all.size, at + STRIP_TAGS))
        // Arrows only when there are more tags than one page holds (user); then both, wrapping around.
        if (all.size <= STRIP_TAGS) return Strip(null, pills, null)
        return Strip(
            back = filterId(query, if (at > 0) at - STRIP_TAGS else last),
            pills = pills,
            next = filterId(query, if (at < last) at + STRIP_TAGS else 0),
        )
    }

    // -- long lists: A-Z parts ---------------------------------------------------

    /** One A-Z part of a long list: "A – C" and the frequencies in it. */
    data class Part(val id: String, val label: String, val stations: List<Station>)

    fun partId(listId: String, n: Int): String = "$listId$PART$n"

    /** (list id, part number) for a part id, else null. Tag and filter ids are url-encoded, so they never hold a bare '@'. */
    fun partOf(listId: String): Pair<String, Int>? {
        val at = listId.lastIndexOf(PART)
        if (at <= 0 || '/' in listId) return null
        val n = listId.substring(at + 1).toIntOrNull() ?: return null
        return listId.substring(0, at) to n
    }

    /**
     * A list longer than [PAGE_MOST] in A-Z parts of at most that many
     * (even sizes), each labeled by the start of its first and last names;
     * a shorter list has none.
     */
    fun parts(list: List<Station>, listId: String): List<Part> {
        if (list.size <= PAGE_MOST) return emptyList()
        // Each name made loose once, not once per comparison.
        val keys = list.associate { it.id to loose(it.displayName) }
        val sorted = list.sortedWith(compareBy({ keys[it.id].orEmpty().ifEmpty { it.displayName.lowercase() } }, { it.id }))
        val count = (sorted.size + PAGE_MOST - 1) / PAGE_MOST
        val size = (sorted.size + count - 1) / count
        val chunks = sorted.chunked(size)
        fun key(s: Station, k: Int) = keys[s.id].orEmpty().ifEmpty { "#" }.take(k).trimEnd().replaceFirstChar { it.uppercase() }
        // One letter each, longer only where two parts would meet on the same start ("Radio A" / "Radio B").
        var k = 1
        while (k < 16 && chunks.zipWithNext().any { (a, b) -> key(a.last(), k).equals(key(b.first(), k), ignoreCase = true) }) k++
        return chunks.mapIndexed { n, part ->
            val from = key(part.first(), k)
            val to = key(part.last(), k)
            Part(partId(listId, n), if (from == to) from else "$from – $to", part)
        }
    }

    // -- Recent: song history ---------------------------------------------------

    /** One song in the Recent tab and the saved frequency it was heard on (null when that one is gone). */
    data class Song(val id: String, val title: String, val artist: String, val stationName: String, val station: Station?, val favorite: Boolean, val coverUrl: String)

    /**
     * The Recent tab: the songs in the history (identified or named by the
     * station), newest first, each with the frequency it was heard on. The
     * history keeps the station's name, so it is matched by name.
     */
    fun recentSongs(stations: List<Station>, history: JsonArray): List<Song> {
        val byName = HashMap<String, Station>()
        val all = saved(stations)
        for (s in all) byName.putIfAbsent(s.displayName.trim().lowercase(), s)
        for (s in all) byName.putIfAbsent(s.name.trim().lowercase(), s)
        return SongHistory.entries(history).sortedByDescending(SongHistory::at).take(RECENT_SONGS).mapIndexed { n, e ->
            val name = SongHistory.station(e)
            val station = byName[name.trim().lowercase()]
            Song(
                "$RECENT/$n:${station?.id.orEmpty()}",
                SongHistory.title(e),
                SongHistory.artist(e),
                name,
                station,
                SongHistory.isFavorite(e),
                SongHistory.coverUrl(e),
            )
        }
    }

    /** `<list>/<station id>` → (list id, station id); null for a plain station id. */
    fun splitItemId(itemId: String): Pair<String, String>? {
        val slash = itemId.indexOf('/')
        if (slash <= 0 || slash == itemId.length - 1) return null
        return itemId.substring(0, slash) to itemId.substring(slash + 1)
    }

    /** What a tap on [itemId] plays: its list, starting at it. A song in Recent plays the frequency it was heard on. */
    fun queueFor(stations: List<Station>, itemId: String, history: JsonArray = JsonArray(emptyList()), order: String = "manual"): Pick? {
        val (listId, raw) = splitItemId(itemId) ?: return null
        val stationId = if (listId == RECENT) raw.replaceFirst(RECENT_NUMBER, "") else raw
        val list = list(stations, listId, history = history, order = order)?.takeIf { it.isNotEmpty() } ?: return null
        val index = list.indexOfFirst { it.id == stationId }
        return if (index < 0) null else Pick(list, index, listId)
    }

    /** A list to play and where in it to start. [what] says how it was found, for the log. */
    data class Pick(val stations: List<Station>, val index: Int, val what: String)

    /**
     * A spoken "play [query] on Frequencies": a tag's name plays that tag's
     * list, else the best-matching name or custom name plays inside Saved.
     * Null when nothing matches (an empty query is the caller's: it plays
     * the last frequency).
     */
    fun playFromSearch(stations: List<Station>, query: String): Pick? {
        for (q in queries(query)) {
            val tag = tags(stations).firstOrNull { loose(it) == q || squash(it) == q.replace(" ", "") }
            if (tag != null) return Pick(tagged(stations, tag), 0, "tag '$tag'")
            val best = ranked(stations, q).firstOrNull() ?: continue
            val list = saved(stations)
            return Pick(list, list.indexOfFirst { it.id == best.id }.coerceAtLeast(0), "name '${best.displayName}'")
        }
        return null
    }

    /** Search results: tags whose name matches, and frequencies by name, then by tag. */
    fun search(stations: List<Station>, query: String): Results {
        for (q in queries(query)) {
            val tags = tags(stations).filter { loose(it).contains(q) || squash(it).contains(q.replace(" ", "")) }
            val byName = ranked(stations, q)
            val wanted = tags.map { it.lowercase() }.toSet()
            val byTag = saved(stations).filter { s -> s !in byName && s.tags.any { it.lowercase() in wanted } }
            if (tags.isNotEmpty() || byName.isNotEmpty()) return Results(tags, byName + byTag)
        }
        return Results(emptyList(), emptyList())
    }

    data class Results(val tags: List<String>, val stations: List<Station>)

    /** Names, best match first: exact, then starts with, then every word in it, then run together. */
    private fun ranked(stations: List<Station>, q: String): List<Station> {
        val words = q.split(' ')
        val qs = q.replace(" ", "")
        fun score(name: String): Int {
            val n = loose(name)
            if (n.isEmpty()) return 0
            val ns = n.replace(" ", "")
            val nameWords = n.split(' ')
            return when {
                n == q || ns == qs -> 4
                n.startsWith("$q ") || nameWords.windowed(words.size).any { it == words } -> 3
                words.all { w -> nameWords.any { it.startsWith(w) } } -> 2
                ns.contains(qs) -> 1
                else -> 0
            }
        }
        return saved(stations)
            .map { s -> s to maxOf(score(s.displayName), score(s.name)) }
            .filter { it.second > 0 }
            .sortedByDescending { it.second }
            .map { it.first }
    }

    /** The query as said, then without filler words ("the 80s station" → "80s"). */
    private fun queries(query: String): List<String> {
        val q = loose(query)
        if (q.isEmpty()) return emptyList()
        val trimmed = q.split(' ').filter { it !in FILLER }.joinToString(" ")
        return if (trimmed.isEmpty() || trimmed == q) listOf(q) else listOf(q, trimmed)
    }

    private val FILLER = setOf("the", "radio", "station", "stations", "music", "frequency", "frequencies", "play", "some")

    /** Lowercase, no accents or apostrophes, "&" as "and", other marks as spaces. */
    fun loose(text: String): String {
        val plain = Normalizer.normalize(text, Normalizer.Form.NFD).replace(MARKS, "")
        return plain.lowercase()
            .replace("&", " and ")
            .replace(APOSTROPHES, "")
            .replace(OTHER, " ")
            .replace(DOTS, " ")
            .trim()
            .replace(SPACES, " ")
    }

    private fun squash(text: String) = loose(text).replace(" ", "")
}
