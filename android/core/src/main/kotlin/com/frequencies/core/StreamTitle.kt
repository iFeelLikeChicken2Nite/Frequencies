package com.frequencies.core

/** What a stream says is playing, made readable (player.clean_stream_title). */
object StreamTitle {
    private val TAG_PAIR = Regex("\\s*[\\w{}\\[\\]]+\\s*=\\s*\"[^\"]*\"")
    private val BARE_KEY = Regex("\\s*[\\w{}\\[\\]]+\\s*=\\s*\\S*")
    private val FIELD = Regex("(\\w+)\\s*=\\s*\"([^\"]*)\"")
    private val FIRST_TAG = Regex("\\s*[\\w{}\\[\\]]+\\s*=\\s*\"")

    /**
     * Some encoders bolt their whole tag block onto the title
     * (`Dracula" artist="TAME IMPALA" {="0" itunesTrackId=...`). Keep the
     * title and the artist; drop the plumbing. Nothing is invented.
     */
    fun clean(raw: String?): String {
        if (raw.isNullOrBlank()) return ""
        val text = raw.trim()
        val fields = FIELD.findAll(text).associate { it.groupValues[1] to it.groupValues[2] }
        val artist = fields["artist"].orEmpty().trim()
        val head = FIRST_TAG.split(text, 2)[0]
        val title = (fields["title"] ?: head).trim().trim('"').trim()
        if (artist.isNotEmpty() && title.isNotEmpty()) return "$artist - $title"
        if (title.isNotEmpty() && title.length < text.length) return title
        val cleaned = BARE_KEY.replace(TAG_PAIR.replace(text, ""), "").trim().trim('"').trim()
        return cleaned.ifEmpty { text }
    }

    /**
     * "Artist - Title" split on a spaced dash (a bare hyphen is part of
     * names too often), left half first. Which half is the artist is
     * ArtistInfo.splitArtistTitle's call (MusicBrainz).
     * Returns null when the line has no spaced dash.
     */
    fun split(line: String): Pair<String, String>? {
        for (sep in listOf(" - ", " \u2013 ", " \u2014 ")) {
            val i = line.indexOf(sep)
            if (i >= 0) {
                val left = line.substring(0, i).trim()
                val right = line.substring(i + sep.length).trim()
                return if (left.isNotEmpty() && right.isNotEmpty()) left to right else null
            }
        }
        return null
    }
}
