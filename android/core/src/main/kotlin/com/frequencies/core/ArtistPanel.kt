package com.frequencies.core

import java.io.File
import kotlinx.serialization.json.JsonElement

/** A credit line part: "Biography from [Wikipedia]", the name as the link. */
data class Credit(val prefix: String, val name: String, val url: String = "")

/**
 * What the Artist tab shows (the desktop's artist panel): the name, genres,
 * biography and photo, and whose words and picture they are. [photo] is a
 * file on the phone or an address; null shows the empty frame.
 */
data class ArtistPanel(
    val key: String,
    val name: String,
    val genres: String = "",
    val bio: String = "",
    val photo: String? = null,
    val credits: List<Credit> = emptyList(),
    /** Where the words came from, for a thumbs-down report ("Biography from Wikipedia"). */
    val bioSource: String = "",
    /** A talk station's (or a station correction's) own info: songs don't replace it. */
    val station: Boolean = false,
    val talk: Boolean = false,
) {
    /** The thumbs belong to an artist's biography, not a talk station's own info. */
    val canJudge: Boolean get() = bio.isNotEmpty() && !talk
}

/**
 * The desktop's feeds._maybe_fetch_artist / _on_artist_bio /
 * _find_artist_photo and _show_station_bio / _on_station_article, without
 * the screen: each call blocks on the network.
 */
class ArtistLookup(
    private val info: ArtistInfo,
    private val photos: ArtistPhotos,
    private val overrides: ArtistOverrides,
) {
    /** The name straight away, before anything is looked up. */
    fun first(artist: String): ArtistPanel {
        val written = overrides.artistOverride(artist)
        return ArtistPanel(artist, written["name"].str()?.trim()?.ifEmpty { null } ?: artist)
    }

    /** The biography and any photo that came with it or with a correction ([photo] null: search for one). */
    fun biography(artist: String): ArtistPanel {
        val written = overrides.artistOverride(artist)
        val found = runCatching { info.biography(artist, written) }.getOrNull()
        val photoPath = found?.photo?.takeIf { it.isNotEmpty() }?.let { photos.cacheImage(it)?.path }
        val tags = written["genres"].str()?.ifEmpty { null } ?: found?.tags?.take(6)?.joinToString(" · ").orEmpty()
        val chosen = written["photo"].str().orEmpty().trim()
        val photo = if (chosen.isNotEmpty() && File(chosen).exists()) chosen else photoPath
        val credits = mutableListOf<Credit>()
        val where = found?.source.orEmpty().trim()
        if (where.isNotEmpty()) credits += Credit("Biography from", ArtistPhotos.serviceName(where), found?.url.orEmpty())
        val photoSource = written["photo_source"].str().orEmpty().trim()
        val finalCredits = if (photoSource.isNotEmpty()) listOf(Credit("", photoSource, written["photo_url"].str().orEmpty().trim())) else credits
        return ArtistPanel(
            key = artist,
            name = (written["name"].str()?.ifEmpty { null } ?: artist).trim(),
            genres = tags,
            bio = Bios.clean(written["bio"].str()?.ifEmpty { null } ?: found?.summary.orEmpty()),
            photo = photo,
            credits = finalCredits,
            bioSource = if (where.isNotEmpty()) "Biography from ${ArtistPhotos.serviceName(where)}" else "",
        )
    }

    /**
     * A photo when the biography had none: (file, credit). [skip] holds
     * addresses not to use (the cover already showing).
     */
    fun findPhoto(artist: String, skip: Set<String>): Pair<String, Credit?>? {
        photos.cachedFor(artist)?.let { return it.path to null }
        val url = photos.find(artist, skip) ?: return null
        val file = photos.download(artist, url) ?: return null
        return file.path to Credit("photo from", ArtistPhotos.serviceName(url), url)
    }

    /** storage.station_info for this station; empty means songs fill the tab. */
    fun stationInfo(station: Station): Map<String, JsonElement> = overrides.stationInfo(station)

    /** feeds._on_station_article: a talk station's (or station correction's) own info. */
    fun station(station: Station, wanted: Map<String, JsonElement>): ArtistPanel {
        val name = station.displayName
        val article = runCatching { info.stationArticle(name, wanted) }.getOrNull()
        val photoRef = wanted["photo"].str()?.ifEmpty { null } ?: article?.photo.orEmpty()
        val photo = when {
            photoRef.startsWith("http://") || photoRef.startsWith("https://") -> photos.cacheImage(photoRef)?.path
            photoRef.isNotEmpty() -> overrides.talkPhotoPath(photoRef)?.path
            else -> null
        }
        val credits = when {
            wanted["photo_source"].str().orEmpty().isNotEmpty() ->
                listOf(Credit("", wanted["photo_source"].str()!!, wanted["photo_url"].str().orEmpty()))
            article?.url.orEmpty().isNotEmpty() && wanted["bio"].str().isNullOrEmpty() ->
                listOf(Credit("About the station from", "Wikipedia", article!!.url))
            else -> emptyList()
        }
        return ArtistPanel(
            key = name,
            name = (wanted["name"].str()?.ifEmpty { null } ?: name).trim(),
            genres = wanted["genres"].str().orEmpty(),
            bio = article?.summary.orEmpty(),
            photo = photo,
            credits = credits,
            station = true,
            talk = truthy(wanted["talk"]),
        )
    }

    /**
     * mainwindow.artist_lookup_on: biographies for this station? The
     * global switch (always on since build 100), a talk station always,
     * then the station's own "artist_lookup".
     */
    fun wanted(settings: Settings, station: Station?): Boolean {
        if (!settings.bool("artist_on", true)) return false
        if (station == null) return true
        if (overrides.isTalkStation(station)) return true
        station["artist_lookup"]?.let { return truthy(it) }
        return true
    }
}
