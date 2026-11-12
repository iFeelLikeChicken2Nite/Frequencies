package com.frequencies.core

/**
 * Bio text as it is shown: Wikipedia's footnote marks taken out. Articles
 * (and bios copied from them, like the shipped Sugar Ray one) carry "[2]",
 * "[a]", "[note 1]" and "[citation needed]", which mean nothing without the
 * article's notes. Bracketed words that are part of the text ("[Untitled]")
 * and years ("[1999]") stay. Plain character classes only: Android's
 * regular expressions are ICU, not Java's.
 */
object Bios {
    private val MARK = Regex(
        "[ \\t]?\\[(?:[0-9]{1,3}|[a-z]|(?:note|nb|n|lower-alpha) ?[0-9]{1,3}|[^\\[\\]\\n]{0,40}(?:needed|\\?))\\]",
        RegexOption.IGNORE_CASE,
    )
    private val SPACES = Regex("[ \\t]{2,}")
    private val BEFORE_STOP = Regex("[ \\t]+([.,;:!?])")

    fun clean(text: String): String {
        if ('[' !in text) return text
        return text.replace(MARK, "").replace(SPACES, " ").replace(BEFORE_STOP, "$1").trim()
    }
}
