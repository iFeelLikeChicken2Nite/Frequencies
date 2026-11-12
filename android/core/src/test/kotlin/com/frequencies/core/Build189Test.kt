package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Build 189: one tag filter for the phone and the car. The shared filter
 * holds only the tag terms (`Search.withText(query, "")`); the phone puts its
 * own typed text back on top (`Search.withText(tags, text)`).
 */
class Build189Test {
    private val phone = "jazz night tag:\"80s\" -tag:\"talk\""

    @Test
    fun theSharedFilterKeepsOnlyTags() {
        val tags = Search.withText(phone, "")
        assertEquals("tag:\"80s\" -tag:\"talk\"", tags)
        assertEquals("", Search.text(tags))
        assertEquals(Search.tagStates(phone), Search.tagStates(tags))
        assertEquals("", Search.withText("", ""))
        assertEquals("", Search.withText("jazz", ""))
    }

    @Test
    fun thePhoneGetsItsTextBack() {
        val tags = Search.withText(phone, "")
        val again = Search.withText(tags, Search.text(phone))
        assertEquals(Search.text(phone), Search.text(again))
        assertEquals(Search.tagStates(phone), Search.tagStates(again))
    }

    @Test
    fun aCarTapShowsOnThePhone() {
        // The phone has "80s" on and "talk" hidden, and a typed search; the car walks "80s" and "rock" on.
        var shared = Search.withText(phone, "")
        shared = Search.setTag(shared, "80s", Browse.nextTagState(Search.tagState(shared, "80s")))
        shared = Search.setTag(shared, "Rock", Browse.nextTagState(Search.tagState(shared, "Rock")))
        val seen = Search.withText(shared, Search.text(phone))
        assertEquals(Search.EXCLUDED, Search.tagState(seen, "80s"))
        assertEquals(Search.INCLUDED, Search.tagState(seen, "rock"))
        assertEquals(Search.EXCLUDED, Search.tagState(seen, "talk"))
        assertEquals("jazz night", Search.text(seen))
    }

    @Test
    fun aPhoneTapShowsInTheCar() {
        // A tap on the phone's chip (cycleTag), then the car's Saved and Tags tab read the shared terms.
        val shared = Search.withText(Search.cycleTag(phone, "Rock"), "")
        assertEquals(Search.INCLUDED, Search.tagState(shared, "rock"))
        assertEquals(shared, Browse.filterOf(Browse.filterId(shared)))
    }
}
