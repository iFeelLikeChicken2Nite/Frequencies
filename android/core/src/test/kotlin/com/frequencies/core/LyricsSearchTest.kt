package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Test

/** Byte for byte with the desktop's lyrics.search_url (urllib.parse.quote_plus). */
class LyricsSearchTest {
    @Test
    fun sameAddressAsTheDesktop() {
        // python3 -c "from urllib.parse import quote_plus; print('https://duckduckgo.com/?q=' + quote_plus('Sugar Ray Fly lyrics'))"
        assertEquals("https://duckduckgo.com/?q=Sugar+Ray+Fly+lyrics", LyricsSearch.url("Sugar Ray", "Fly"))
        assertEquals("https://duckduckgo.com/?q=AC%2FDC+T.N.T.+lyrics", LyricsSearch.url("AC/DC", "T.N.T."))
        assertEquals("https://duckduckgo.com/?q=Bj%C3%B6rk+J%C3%B3ga+%26+more+lyrics", LyricsSearch.url("Björk", "Jóga & more"))
        assertEquals("https://duckduckgo.com/?q=Untitled+lyrics", LyricsSearch.url("", "Untitled"))
        assertEquals("", LyricsSearch.url("Someone", "  "))
    }
}
