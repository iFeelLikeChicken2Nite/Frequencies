package com.frequencies.core

import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull

class Build187Test {
    @Test
    fun aTagsPageCarriesItsFilter() {
        val q = Search.setTag("", "80s / synth", Search.INCLUDED)
        val id = Browse.tagsPageId(q)
        assertEquals(q, Browse.tagsPageOf(id))
        assertEquals("", Browse.tagsPageOf(Browse.tagsPageId("")))
        // Not a Saved list, and nothing else is a Tags page.
        assertNull(Browse.filterOf(id))
        assertNull(Browse.tagsPageOf(Browse.SAVED))
        assertNull(Browse.tagsPageOf(Browse.TAGS))
        assertNull(Browse.tagsPageOf("$id/station"))
    }
}
