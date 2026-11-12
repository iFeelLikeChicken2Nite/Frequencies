package com.frequencies.app.playback

import com.frequencies.app.TagFilter
import android.os.Bundle
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.session.MediaConstants
import com.frequencies.app.Library
import com.frequencies.app.R
import com.frequencies.core.Browse
import com.frequencies.core.IBroadcast
import com.frequencies.core.Twitch
import com.frequencies.core.YouTube
import com.frequencies.core.LocalStations
import com.frequencies.core.Playlists
import com.frequencies.core.Schedule
import java.time.LocalDateTime
import com.frequencies.core.Station

/** The browse tree's root; Android Auto shows its children as tabs. */
const val ROOT_ID = "root"

/**
 * Longest lists handed to Android Auto in one piece. A longer list of
 * frequencies shows its first [Browse.PAGE_MOST] and then A-Z parts that
 * hold every one (batch 24; it used to stop at 100).
 */
const val MOST_STATIONS = Browse.PAGE_MOST
const val MOST_TAGS = 100

/**
 * A station to play. [listId] is set for an item inside a browse list
 * (its id is then `<list>/<station id>`); the player's own items carry
 * the plain station id.
 */
fun Library.mediaItem(station: Station, listId: String? = null, subtitle: String? = null, group: String? = null): MediaItem {
    // In a browse list (Android Auto), a station that can't play now is shown but can't be started (task 214).
    val why = if (listId != null) unavailable(station) else null
    val metadata = MediaMetadata.Builder()
        .setTitle(station.displayName)
        .apply { (why ?: subtitle)?.let { setSubtitle(it) } }
        .apply { group?.let { setExtras(styled(group = it)) } }
        .setStation(station.displayName)
        .setArtworkUri(sharedArtUri(station))
        .setIsPlayable(why == null)
        .setIsBrowsable(false)
        .setMediaType(MediaMetadata.MEDIA_TYPE_RADIO_STATION)
        .build()
    return MediaItem.Builder()
        .setMediaId(listId?.let { Browse.itemId(it, station) } ?: station.id)
        // A local files station plays a song from its folder, picked when it starts (PlaybackService.resolve);
        // iBroadcast, YouTube and Twitch are looked up (and signed) when they play.
        .setUri(
            when {
                LocalStations.isLocal(station) -> localUri(station.id, 0)
                isService(station) -> serviceUri(station.id, 0)
                else -> station.url
            },
        )
        .setMimeType(
            when {
                Twitch.isTwitch(station) -> MimeTypes.APPLICATION_M3U8
                !LocalStations.isLocal(station) && !isService(station) && Playlists.isHls(station.url) -> MimeTypes.APPLICATION_M3U8
                else -> null
            },
        )
        .setMediaMetadata(metadata)
        .build()
}

/** A local files station's item: freqlocal://song/<station id>/<song number>; a new number makes the player load the next song. */
fun localUri(stationId: String, song: Int): String = "$LOCAL_SCHEME://song/${android.net.Uri.encode(stationId)}/$song"

/** The station id in a [localUri] (any id, even one from the desktop that isn't a valid host name). */
fun localStationId(uri: android.net.Uri): String = uri.pathSegments.firstOrNull().orEmpty()
const val LOCAL_SCHEME = "freqlocal"

/**
 * A streaming service's station (an iBroadcast library, a YouTube
 * playlist, a Twitch channel): its item carries freqsvc://play/<id>/<n>,
 * and the service turns that into a signed or resolved link each time it
 * plays. A new number makes the player load the next track.
 */
fun isService(station: Station?): Boolean =
    IBroadcast.isIBroadcast(station) || YouTube.isYouTube(station) || Twitch.isTwitch(station)

/** A service station that plays one track after another (not Twitch, which is live). */
fun playsTracks(station: Station?): Boolean = IBroadcast.isIBroadcast(station) || YouTube.isYouTube(station)

fun serviceUri(stationId: String, n: Int): String = "$SERVICE_SCHEME://play/${android.net.Uri.encode(stationId)}/$n"
const val SERVICE_SCHEME = "freqsvc"

private fun folder(id: String, title: String, type: Int, playable: Boolean = false, subtitle: String? = null, extras: Bundle? = null, art: android.net.Uri? = null): MediaItem = MediaItem.Builder()
    .setMediaId(id)
    .setMediaMetadata(
        MediaMetadata.Builder()
            .setTitle(title)
            .apply { subtitle?.let { setSubtitle(it) } }
            .apply { art?.let { setArtworkUri(it) } }
            .setIsBrowsable(true)
            .setIsPlayable(playable)
            .setMediaType(type)
            .apply { extras?.let { setExtras(it) } }
            .build(),
    )
    .build()

/**
 * An item's own look in Auto: a plain list row (tags, songs and A-Z parts
 * are never tiles, user, batch 24) and the heading of the group it sits
 * under (A-Z parts under "All, A to Z").
 */
private fun styled(list: Boolean = false, group: String? = null, category: Boolean = false): Bundle = Bundle().apply {
    if (list) putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_SINGLE_ITEM, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_LIST_ITEM)
    if (category) putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_SINGLE_ITEM, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_CATEGORY_GRID_ITEM)
    group?.let { putString(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_GROUP_TITLE, it) }
}

fun rootItem(): MediaItem = folder(ROOT_ID, "Frequencies", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)

/** Folders are lists, stations are a grid of their pictures. */
fun rootExtras(): Bundle = Bundle().apply {
    putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_BROWSABLE, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_LIST_ITEM)
    putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_PLAYABLE, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_GRID_ITEM)
}

/**
 * Auto's tabs: Saved (manual order, filtered by the Tags tab), Tags and
 * History (songs heard); the last two only once they have something in them.
 */
fun Library.tabs(): List<MediaItem> {
    val all = stations.value
    return buildList {
        // Saved, Tags, History (user, build 187).
        add(folder(Browse.SAVED, "Saved", MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS))
        if (Browse.tags(all).isNotEmpty()) add(folder(Browse.TAGS, "Tags", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED, extras = tilesStyle()))
        if (Browse.recentSongs(all, history.value).isNotEmpty()) add(folder(Browse.RECENT, "History", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED))
    }
}

/** A tag in the Tags tab: a list row (never a tile) that opens its frequencies. */
fun tagFolder(tag: String): MediaItem =
    folder(Browse.tagListId(tag), tag, MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS, extras = styled(list = true))

/** The Tags tab: tiles, not rows. */
private fun tilesStyle(): Bundle = Bundle().apply {
    putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_BROWSABLE, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_GRID_ITEM)
    putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_PLAYABLE, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_GRID_ITEM)
    putInt(MediaConstants.EXTRAS_KEY_CONTENT_STYLE_SINGLE_ITEM, MediaConstants.EXTRAS_VALUE_CONTENT_STYLE_GRID_ITEM)
}

/**
 * A tile in the Tags tab (user, build 187): the tag's pill in its own
 * color, faint gray (off), solid with a check (only these) or faint and
 * struck through with an x (hidden). It is a playable item that plays
 * nothing new: the tap walks the tag to its next state in place (build 188)
 * and is answered with the frequency already playing (build 189, so Auto
 * no longer waits on "Getting your selection…"); Saved, Next, Previous,
 * Shuffle and the phone's tag pills follow.
 */
private fun Library.tagTile(tag: String, query: String): MediaItem {
    val state = com.frequencies.core.Search.tagState(query, tag)
    val art = StripArt.pill(appContext, tag, settings.value.tagColor(tag), state)
    val word = when (state) {
        com.frequencies.core.Search.INCLUDED -> "Only these"
        com.frequencies.core.Search.EXCLUDED -> "Hidden"
        else -> "Off"
    }
    val title = when (state) {
        com.frequencies.core.Search.INCLUDED -> "✓ $tag"
        com.frequencies.core.Search.EXCLUDED -> "✕ $tag"
        else -> tag
    }
    return MediaItem.Builder()
        .setMediaId(Browse.tagFlipId(tag))
        .setMediaMetadata(
            MediaMetadata.Builder()
                .setTitle(title)
                .setSubtitle(word)
                .setArtworkUri(art)
                .setIsBrowsable(false)
                .setIsPlayable(true)
                .setMediaType(MediaMetadata.MEDIA_TYPE_GENRE)
                .setExtras(tilesStyle())
                .build(),
        )
        .build()
}

private fun Library.tagTiles(query: String): List<MediaItem> =
    Browse.tags(stations.value).take(MOST_TAGS).map { tagTile(it, query) }

/** One A-Z part of a long list, as a list row. */
private fun partItem(part: Browse.Part, group: String?): MediaItem =
    folder(part.id, part.label, MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS, subtitle = "${part.stations.size} frequencies", extras = styled(list = true, group = group))

/** A list of frequencies: all of them, or the first [MOST_STATIONS] and then A-Z parts holding every one. */
private fun Library.stationRows(list: List<Station>, listId: String, group: String?): List<MediaItem> {
    val parts = Browse.parts(list, listId)
    if (parts.isEmpty()) return list.map { mediaItem(it, listId, group = group) }
    return list.take(MOST_STATIONS).map { mediaItem(it, listId, group = group) } +
        parts.map { partItem(it, "All, A to Z") }
}

/** A song in Recent: plays the frequency it was heard on, or just lists when that one is gone. */
private fun Library.songItem(song: Browse.Song): MediaItem {
    val station = song.station
    val why = station?.let { unavailable(it) }
    val art = song.coverUrl.takeIf { it.startsWith("https://") }?.let { android.net.Uri.parse(it) } ?: station?.let { sharedArtUri(it) }
    val title = (if (song.favorite) "★ " else "") + song.title.ifEmpty { song.artist }
    val line = listOf(song.artist.takeIf { song.title.isNotEmpty() }, song.stationName).filter { !it.isNullOrBlank() }.joinToString(" · ")
    return MediaItem.Builder()
        .setMediaId(song.id)
        .setMediaMetadata(
            MediaMetadata.Builder()
                .setTitle(title)
                .setSubtitle(why ?: line)
                .setArtworkUri(art)
                .setIsPlayable(station != null && why == null)
                .setIsBrowsable(false)
                .setMediaType(MediaMetadata.MEDIA_TYPE_MUSIC)
                .setExtras(styled(list = true))
                .build(),
        )
        .build()
}

/** What a folder holds, or null for an id that isn't one. */
fun Library.children(parentId: String): List<MediaItem>? {
    val all = stations.value
    // A Tags page: the tiles as its filter leaves them.
    Browse.tagsPageOf(parentId)?.let { return tagTiles(it) }
    // Saved: the frequencies the Tags tab's filter leaves (Saved with a filter in its id: that one).
    Browse.filterOf(parentId)?.let { query ->
        val id = if (parentId == Browse.SAVED) Browse.filterId(TagFilter.query.value) else parentId
        return stationRows(Browse.list(all, id, order = Browse.carOrder(settings.value)).orEmpty(), id, null)
    }
    return when (parentId) {
        ROOT_ID -> tabs()
        Browse.TAGS -> tagTiles(TagFilter.query.value)
        Browse.RECENT -> Browse.recentSongs(all, history.value).map { songItem(it) }
        // Each with the show that's on; tapping one plays it (nothing here starts by itself).
        Browse.LIVE -> Schedule.liveNow(Browse.saved(all), LocalDateTime.now()).take(MOST_STATIONS)
            .map { mediaItem(it.station, Browse.LIVE, subtitle = it.name.ifEmpty { null }) }
        else -> {
            // An A-Z part holds at most PAGE_MOST, so it is listed whole.
            val order = Browse.carOrder(settings.value)
            if (Browse.partOf(parentId) != null) Browse.list(all, parentId, order = order)?.map { mediaItem(it, parentId) }
            else Browse.list(all, parentId, order = order)?.let { stationRows(it, parentId, null) }
        }
    }
}

/** A folder or song the car asks for by id: Saved with a tag filter, an A-Z part, or a song in Recent. */
fun Library.listItem(mediaId: String): MediaItem? {
    val all = stations.value
    if (Browse.tagsPageOf(mediaId) != null) return folder(mediaId, "Tags", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED, extras = tilesStyle())
    Browse.filterOf(mediaId)?.let { query ->
        val title = if (query.isEmpty()) "Saved" else "Saved · " + Browse.tags(all).filter { com.frequencies.core.Search.tagState(query, it) != com.frequencies.core.Search.NEUTRAL }.joinToString(", ")
        return folder(mediaId, title, MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS)
    }
    Browse.partOf(mediaId)?.let { (base, n) ->
        val part = Browse.list(all, base, history = history.value, order = Browse.carOrder(settings.value))?.let { Browse.parts(it, base).getOrNull(n) } ?: return null
        return partItem(part, null)
    }
    if (mediaId.startsWith(Browse.RECENT + "/")) return Browse.recentSongs(all, history.value).firstOrNull { it.id == mediaId }?.let { songItem(it) }
    return null
}

/** Search results: matching tags as folders, then frequencies (playing one queues Saved). */
fun Library.searchResults(query: String): List<MediaItem> {
    val found = Browse.search(stations.value, query)
    return found.tags.take(MOST_TAGS).map { tagFolder(it) } +
        found.stations.take(MOST_STATIONS).map { mediaItem(it, Browse.SAVED) }
}
