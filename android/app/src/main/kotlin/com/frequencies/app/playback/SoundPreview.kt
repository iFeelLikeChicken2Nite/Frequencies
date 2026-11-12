package com.frequencies.app.playback

import com.frequencies.core.Equalizer
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Station settings' Sound tab, heard while it is being set: the playback
 * service plays these values for that station until the dialog closes,
 * then goes back to what was saved.
 */
object SoundPreview {
    data class Preview(val stationId: String, val bands: List<Equalizer.Band>, val gainDb: Double)

    val current = MutableStateFlow<Preview?>(null)
}
