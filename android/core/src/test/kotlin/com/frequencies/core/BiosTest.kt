package com.frequencies.core

import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class BiosTest {
    @Test
    fun footnoteMarksGo() {
        assertEquals("Formed in 1986. Originally a cover band.", Bios.clean("Formed in 1986.[2] Originally a cover band.[12][13]"))
        assertEquals("A band from Ohio.", Bios.clean("A band from Ohio.[a][note 1]"))
        assertEquals("They split in 2001.", Bios.clean("They split in 2001 [citation needed]."))
        assertEquals("Its best album.", Bios.clean("Its best album[according to whom?]."))
        assertEquals("Released as [Untitled] in [1999].", Bios.clean("Released as [Untitled] in [1999].[3]"))
        assertEquals("No marks here.", Bios.clean("No marks here."))
    }

    @Test
    fun shippedSugarRayBioHasNoMark() {
        val file = File("../app/src/main/assets/shipped/artist_overrides.json")
        val shipped = FreqJson.parseToJsonElement(file.readText()) as JsonObject
        val raw = ((shipped["sugar ray"] as JsonObject)["bio"]).str().orEmpty()
        assertTrue("the shipped bio still has its mark", "[2]" in raw)
        val shown = Bios.clean(raw)
        assertFalse(shown, Regex("\\[[0-9]+\\]").containsMatchIn(shown))
        assertTrue(shown, "Kristian Attard. Originally" in shown)
    }
}
