package com.frequencies.core

import java.nio.file.Files
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android batch 22: Comfort View's automatic entry follows Song ID, with the talk station exception. */
class Batch22Test {
    private fun station(vararg pairs: Pair<String, Any>) = Station(linkedMapOf<String, kotlinx.serialization.json.JsonElement>().apply {
        put("id", JsonPrimitive("s1"))
        put("url", JsonPrimitive("http://example.com/live"))
        put("name", JsonPrimitive("Some Station"))
        for ((k, v) in pairs) put(k, when (v) { is Boolean -> JsonPrimitive(v); else -> JsonPrimitive(v.toString()) })
    })

    @Test fun songIdIsOnUnlessTurnedOff() {
        assertTrue(Comfort.songIdOn(station()))
        assertTrue(Comfort.songIdOn(station("shazam_auto" to true)))
        assertFalse(Comfort.songIdOn(station("shazam_auto" to false)))
        assertTrue("nothing playing: no change", Comfort.songIdOn(null))
    }

    @Test fun autoPageFollowsSongId() {
        assertEquals(Comfort.AutoPage.LYRICS, Comfort.autoPage(songId = true, talkBio = false))
        assertEquals("Song ID on keeps the lyrics, talk or not", Comfort.AutoPage.LYRICS, Comfort.autoPage(songId = true, talkBio = true))
        assertEquals(Comfort.AutoPage.NONE, Comfort.autoPage(songId = false, talkBio = false))
        assertEquals(Comfort.AutoPage.ARTIST, Comfort.autoPage(songId = false, talkBio = true))
    }

    @Test fun talkWithBioNeedsTheDevsMarkAndATypedBio() {
        val dir = Files.createTempDirectory("b22").toFile()
        val shipped = JsonObject(mapOf(
            "nye underground" to JsonObject(mapOf(
                "bio" to JsonPrimitive("Broadcasting the classics from the high desert."),
                "stream" to JsonPrimitive("http://65.109.81.84:8116/stream"),
                "talk" to JsonPrimitive(true),
            )),
            "quiet talk" to JsonObject(mapOf("talk" to JsonPrimitive(true), "stream" to JsonPrimitive("http://example.com/quiet"))),
        ))
        val own = JsonObject(mapOf("my show" to JsonObject(mapOf("bio" to JsonPrimitive("My own words about my show.")))))
        val o = ArtistOverrides({ JsonObject(emptyMap()) }, { shipped }, { JsonObject(emptyMap()) }, { own }, PhotoPackStore(dir))
        // The shipped talk station with its bio, matched by stream address or by name.
        assertTrue(o.talkWithBio(station("url" to "http://65.109.81.84:8116/stream", "name" to "Renamed")))
        assertTrue(o.talkWithBio(station("name" to "NYE Underground")))
        // A shipped talk station without a typed bio: no.
        assertFalse(o.talkWithBio(station("url" to "http://example.com/quiet")))
        // The dev's talk radio switch plus the station's own bio: yes; the switch alone or the bio alone: no.
        assertTrue(o.talkWithBio(station("name" to "My Show", "talk_radio" to true)))
        assertFalse(o.talkWithBio(station("name" to "My Show")))
        assertFalse(o.talkWithBio(station("name" to "Other", "talk_radio" to true)))
    }
}
