package com.frequencies.core

/**
 * The Saved Frequencies search box and tag filter, as the desktop has
 * them (storage.matches_search and the tag filter helpers): one query
 * string holds both, so `jazz tag:"80s" -tag:"talk"` is a typed search
 * plus two tag pills.
 *
 * Included tags are ORed (a station is rarely two things at once);
 * free text and exclusions narrow on top. A quoted tag matches exactly,
 * an unquoted one is a substring (hand typing).
 */
object Search {
    const val INCLUDED = 1
    const val EXCLUDED = -1
    const val NEUTRAL = 0

    /** Split on whitespace, except inside double quotes. */
    fun tokens(query: String?): List<String> {
        val out = ArrayList<String>()
        val current = StringBuilder()
        var inQuotes = false
        for (ch in query.orEmpty()) {
            when {
                ch == '"' -> { inQuotes = !inQuotes; current.append(ch) }
                ch.isWhitespace() && !inQuotes -> if (current.isNotEmpty()) { out.add(current.toString()); current.clear() }
                else -> current.append(ch)
            }
        }
        if (current.isNotEmpty()) out.add(current.toString())
        return out
    }

    private fun unquote(text: String): Pair<String, Boolean> =
        if (text.length >= 2 && text.first() == '"' && text.last() == '"') text.substring(1, text.length - 1) to true
        else text to false

    private class Token(val negated: Boolean, val isTag: Boolean, val value: String, val quoted: Boolean)

    /** Takes both `tag:"two words"` and `"tag:two words"`. */
    private fun split(token: String): Token {
        var negated = false
        var text = token
        if (text.length > 1 && text.startsWith("-")) { negated = true; text = text.substring(1) }
        val (inner, outerQuoted) = unquote(text)
        if (inner.lowercase().startsWith("tag:")) {
            val (value, innerQuoted) = unquote(inner.substring(4))
            return Token(negated, true, value, outerQuoted || innerQuoted)
        }
        return Token(negated, false, inner, outerQuoted)
    }

    fun matches(station: Station, query: String?): Boolean {
        val tokens = tokens(query)
        if (tokens.isEmpty()) return true
        val tags = station.tags.map { it.lowercase() }
        val fields = listOf(station.customName.orEmpty(), station.name, station.url).map { it.lowercase() }
        val included = ArrayList<Boolean>()
        for (raw in tokens) {
            val t = split(raw)
            val value = t.value.lowercase()
            if (value.isEmpty()) continue
            val result = if (t.isTag) {
                val hit = if (t.quoted) value in tags else tags.any { value in it }
                if (!t.negated) { included.add(hit); continue }
                hit
            } else {
                fields.any { value in it } || tags.any { value in it }
            }
            if (result == t.negated) return false
        }
        return included.isEmpty() || included.any { it }
    }

    fun filter(stations: List<Station>, query: String?): List<Station> =
        if (tokens(query).isEmpty()) stations else stations.filter { matches(it, query) }

    /** tag (lowercase) -> INCLUDED / EXCLUDED. */
    fun tagStates(query: String?): Map<String, Int> {
        val states = LinkedHashMap<String, Int>()
        for (raw in tokens(query)) {
            val t = split(raw)
            if (t.isTag && t.value.isNotEmpty()) states[t.value.lowercase()] = if (t.negated) EXCLUDED else INCLUDED
        }
        return states
    }

    fun tagState(query: String?, tag: String): Int = tagStates(query)[tag.lowercase()] ?: NEUTRAL

    private fun quoteTag(tag: String) = "\"" + tag.replace("\"", "") + "\""

    /** The query with [tag] set to [state], rewritten rather than appended. */
    fun setTag(query: String?, tag: String, state: Int): String {
        val wanted = tag.lowercase()
        val kept = tokens(query).filterNot { val t = split(it); t.isTag && t.value.lowercase() == wanted }.toMutableList()
        when (state) {
            INCLUDED -> kept.add("tag:" + quoteTag(tag))
            EXCLUDED -> kept.add("-tag:" + quoteTag(tag))
        }
        return kept.joinToString(" ")
    }

    /** A tap walks neutral <-> included, a long press neutral <-> excluded; either clears the other state. */
    fun cycleTag(query: String?, tag: String, exclude: Boolean = false): String {
        val wanted = if (exclude) EXCLUDED else INCLUDED
        return setTag(query, tag, if (tagState(query, tag) == wanted) NEUTRAL else wanted)
    }

    /** Drop every tag term, leaving typed text alone. */
    fun clearTags(query: String?): String = tokens(query).filterNot { split(it).isTag }.joinToString(" ")

    /** The typed text without its tag terms. */
    fun text(query: String?): String = clearTags(query)

    /** [text] typed into the box, keeping the tag terms. */
    fun withText(query: String?, text: String): String {
        val tagTerms = tokens(query).filter { split(it).isTag }
        return (listOf(text.trim()).filter { it.isNotEmpty() } + tagTerms).joinToString(" ")
    }
}
