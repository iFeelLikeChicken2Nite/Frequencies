package com.frequencies.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/** Build 188: the car's tag tiles are tapped in place, and an on filter chip is solid. */
class Build188Test {
    @Test
    fun aTagTileNamesItsTag() {
        assertEquals("80s / synth", Browse.tagFlipOf(Browse.tagFlipId("80s / synth")))
        assertNull(Browse.tagFlipOf(Browse.tagFlipId("")))
        assertNull(Browse.tagFlipOf(Browse.SAVED))
        assertNull(Browse.tagFlipOf(Browse.tagListId("jazz")))
        assertNull(Browse.filterOf(Browse.tagFlipId("jazz")))
        assertNull(Browse.splitItemId(Browse.tagFlipId("jazz"))?.let { Browse.filterOf(it.first) })
    }

    @Test
    fun onChipsAreSolid() {
        assertEquals(TagLook.OFF, TagLook.filterStyle(Search.NEUTRAL))
        assertEquals(TagLook.EXCLUDE, TagLook.filterStyle(Search.EXCLUDED))
        val on = TagLook.filterStyle(Search.INCLUDED)
        assertEquals(230, on.fillAlpha)
        assertEquals(TagLook.Text.DARK, on.text)
        assertNotEquals(TagLook.pressedAlpha(TagLook.OFF), TagLook.pressedAlpha(on))
    }

    @Test
    fun fingerprintsAreUnchanged() {
        // The same 16 hex digits the String.format version gave (the desktop and old stores compare them).
        for (v in listOf(JsonPrimitive("jazz"), JsonPrimitive(42), buildJsonObject { put("b", JsonPrimitive(1)); put("a", JsonPrimitive("x")) }, null)) {
            val sb = StringBuilder()
            val canonical = SyncData.fingerprint(v)
            assertEquals(16, canonical.length)
            assertEquals(canonical, canonical.lowercase())
        }
        val digest = java.security.MessageDigest.getInstance("SHA-1").digest("\"jazz\"".toByteArray())
        assertEquals(digest.joinToString("") { "%02x".format(it) }.take(16), SyncData.fingerprint(JsonPrimitive("jazz")))
    }
}
