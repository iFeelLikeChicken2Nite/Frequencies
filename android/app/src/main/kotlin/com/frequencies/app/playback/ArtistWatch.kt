package com.frequencies.app.playback

import android.util.Log
import com.frequencies.app.ArtistKit
import com.frequencies.app.Library
import com.frequencies.core.ArtistPanel
import com.frequencies.core.Station
import com.frequencies.core.str
import com.frequencies.core.truthy
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** What the Artist tab shows now, shared by the playback service (which looks it up) and the screens. */
object ArtistNow {
    data class State(
        val stationId: String? = null,
        val panel: ArtistPanel? = null,
        /** The biography (or the station's article) is still being looked up. */
        val busy: Boolean = false,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** The thumbs: "", "up" or "down" (a new artist hasn't been judged yet). */
    val verdict = MutableStateFlow("")

    internal fun set(s: State) { _state.value = s }
}

/**
 * The desktop's feeds._maybe_fetch_artist and _show_station_bio: once per
 * artist, the name at once and the biography behind it, a photo search
 * when it brought none; a talk station (or a station correction) keeps its
 * own info the whole time it plays, songs or not. Late answers for an
 * artist no longer shown are dropped.
 */
class ArtistWatch(private val library: Library, private val kit: ArtistKit, private val scope: CoroutineScope) {
    private var stationId: String? = null
    private var shown = ""
    private var holdsStation = false
    private var talkPassed = ""
    private var job: Job? = null
    private var seq = 0

    fun tuned(station: Station?) {
        if (station?.id == stationId && station != null) return
        stationId = station?.id
        reset()
        if (station == null) { ArtistNow.set(ArtistNow.State()); return }
        showStation(station)
    }

    private fun reset() {
        job?.cancel()
        seq++
        shown = ""
        holdsStation = false
        talkPassed = ""
        ArtistNow.verdict.value = ""
    }

    private fun showStation(station: Station) {
        val wanted = kit.lookup.stationInfo(station)
        if (wanted.isEmpty()) { ArtistNow.set(ArtistNow.State(station.id)); return }
        holdsStation = true
        val talk = truthy(wanted["talk"])
        if (talk) Log.i(TAG, "[talk] '${station.displayName}' keeps the station's info in the Artist tab (talk radio, matched by ${wanted["_matched"].str() ?: "?"}, shipped list)")
        val first = ArtistPanel(station.displayName, wanted["name"].str()?.ifEmpty { null } ?: station.displayName, station = true, talk = talk)
        ArtistNow.set(ArtistNow.State(station.id, first, busy = true))
        val mine = ++seq
        job = scope.launch {
            val panel = withContext(Dispatchers.IO) { kit.lookup.station(station, wanted) }
            if (mine != seq) return@launch
            if (talk) Log.i(TAG, "[talk] '${station.displayName}': info shown (${if (wanted["bio"] != null) "typed-in bio" else if (panel.bio.isNotEmpty()) "Wikipedia" else "nothing found, name only"}${if (panel.photo != null) ", photo" else ", no photo"})")
            ArtistNow.set(ArtistNow.State(station.id, panel))
        }
    }

    /** The artist of the song showing now (Song ID's, or the stream's own after the MusicBrainz check). */
    fun artist(name: String?, cover: String?) {
        val id = stationId ?: return
        val artist = name.orEmpty().trim()
        if (holdsStation) {
            if (artist.isNotEmpty() && artist != talkPassed) {
                talkPassed = artist
                Log.i(TAG, "[talk] song by '$artist' left the station's info in place")
            }
            return
        }
        if (!kit.lookup.wanted(library.settings.value, library.station(id))) return
        if (artist.isEmpty() || artist == shown) return
        shown = artist
        job?.cancel()
        val mine = ++seq
        ArtistNow.verdict.value = ""
        ArtistNow.set(ArtistNow.State(id, kit.lookup.first(artist), busy = true))
        job = scope.launch {
            val panel = withContext(Dispatchers.IO) { kit.lookup.biography(artist) }
            if (mine != seq) return@launch
            Log.i(TAG, "[artist] '$artist': ${if (panel.bio.isEmpty()) "no biography found${kit.info.lastBioError.let { if (it.isEmpty()) "" else " ($it)" }}" else panel.bioSource.ifEmpty { "biography" }}${if (panel.photo != null) ", photo" else ""}")
            ArtistNow.set(ArtistNow.State(id, panel))
            if (panel.photo != null) return@launch
            val found = withContext(Dispatchers.IO) { kit.lookup.findPhoto(artist, setOfNotNull(cover?.ifEmpty { null })) } ?: return@launch
            if (mine != seq) {
                Log.i(TAG, "[artist] late photo for '$artist' not shown (the tab is on someone else now); kept for '$artist'")
                return@launch
            }
            val (file, credit) = found
            ArtistNow.set(ArtistNow.State(id, panel.copy(photo = file, credits = panel.credits + listOfNotNull(credit))))
        }
    }

    /** A new photo pack or bio fixes (or none): look again at what is showing. */
    fun refresh() {
        val station = library.station(stationId) ?: return
        val artist = shown
        reset()
        showStation(station)
        if (!holdsStation && artist.isNotEmpty()) artist(artist, null)
    }

    fun release() { job?.cancel() }

    companion object { private const val TAG = "Frequencies" }
}
