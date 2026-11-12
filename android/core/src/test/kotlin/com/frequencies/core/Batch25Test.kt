package com.frequencies.core

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Android batch 25: the ad cover on every station (desktop 181's storage.ad_cover_on / bridge._ad_cover_for). */
class Batch25Test {
    private fun station(id: String, url: String, kind: String? = null, vararg extra: Pair<String, Any?>): Station {
        val s = Station(LinkedHashMap(Defaults.station))
        s["id"] = JsonPrimitive(id)
        s["name"] = JsonPrimitive(id)
        s["url"] = JsonPrimitive(url)
        if (kind != null) s["kind"] = JsonPrimitive(kind)
        for ((k, v) in extra) s[k] = when (v) {
            is Boolean -> JsonPrimitive(v)
            is Number -> JsonPrimitive(v)
            else -> JsonPrimitive(v as String?)
        }
        return s
    }

    @Test fun theControlsShowForEveryStationThatCanHaveAnAd() {
        assertTrue(Twitch.adCoverOffered(station("plain", "https://a.example/stream")))
        assertTrue(Twitch.adCoverOffered(Twitch.stationFor("lofigirl", { "t" }, 1.0)))
        assertFalse(Twitch.adCoverOffered(station("folder", "local:///music", "local")))
        assertFalse(Twitch.adCoverOffered(YouTube.stationFor("https://www.youtube.com/playlist?list=PL1", "Mix", { "y" }, 1.0)))
        assertFalse(Twitch.adCoverOffered(station("ib", "ibroadcast://everything", "ibroadcast")))
    }

    @Test fun aPlainStreamIsCoveredOnlyWhenTheUserTurnedItOn() {
        // Never set: off for a plain stream, on (30 s) for Twitch, as desktop's defaults.
        val plain = station("plain", "https://a.example/stream")
        assertFalse(Twitch.adCoverOn(plain))
        assertFalse(Twitch.coversAd(plain))
        assertTrue(Twitch.coversAd(Twitch.stationFor("lofigirl", { "t" }, 1.0)))
        // Turned on with the desktop's keys: covered for ad_seconds.
        val on = station("on", "https://b.example/stream", null, "ad_override" to true, "ad_seconds" to 20.0)
        assertTrue(Twitch.coversAd(on))
        assertEquals(20.0, Twitch.adCoverSeconds(on), 0.0)
        // 0 s, or off, covers nothing.
        assertFalse(Twitch.coversAd(station("zero", "https://c.example/stream", null, "ad_override" to true, "ad_seconds" to 0.0)))
        assertFalse(Twitch.coversAd(station("off", "https://d.example/stream", null, "ad_override" to false)))
        // A music folder or a YouTube list never covers (bridge: custom stations have no ads), even turned on.
        assertFalse(Twitch.coversAd(station("folder", "local:///music", "local", "ad_override" to true)))
        assertFalse(Twitch.coversAd(station("yt", "youtube://x", "youtube", "ad_override" to true)))
        assertFalse(Twitch.coversAd(null))
    }

    @Test fun aPlainStreamIsCoveredByAnotherPlainStream() {
        val me = station("me", "https://a.example/stream", null, "ad_override" to true, "ad_seconds" to 15.0)
        val other = station("other", "https://b.example/stream")
        val sameUrl = station("twin", "https://a.example/stream")
        val hasAds = station("ads", "https://c.example/stream", null, "ad_override" to true)
        val got = Twitch.coverCandidates(me, listOf(me, other, sameUrl, hasAds), isDown = { false })
        assertEquals(listOf("other"), got.map { it.id })
        // Chosen first.
        val third = station("third", "https://e.example/stream")
        me["ad_filler_kind"] = JsonPrimitive("chosen")
        me["ad_filler_station_id"] = JsonPrimitive("third")
        assertEquals("third", Twitch.coverCandidates(me, listOf(me, other, third), isDown = { false }).first().id)
    }
}
