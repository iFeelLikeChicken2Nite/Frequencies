package com.frequencies.app.ui

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import androidx.core.content.ContextCompat
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.frequencies.app.playback.PlaybackService
import com.frequencies.core.Station
import com.google.common.util.concurrent.ListenableFuture
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** What the screens show about playback. */
data class NowPlaying(
    val stationId: String? = null,
    val stationName: String = "",
    val title: String = "",
    val artist: String = "",
    val artworkUri: Uri? = null,
    val isPlaying: Boolean = false,
    val playWhenReady: Boolean = false,
    val buffering: Boolean = false,
    val error: String? = null,
) {
    val hasStation get() = stationId != null
    /** A song is known (from the stream's titles), not just the station. */
    val hasSong get() = title.isNotEmpty() && title != stationName
}

/**
 * The screens' remote control for the playback service, the same way the
 * notification, Bluetooth and Android Auto reach it.
 */
class PlayerConnection(private val context: Context) {
    private val _state = MutableStateFlow(NowPlaying())
    val state: StateFlow<NowPlaying> = _state.asStateFlow()

    private var future: ListenableFuture<MediaController>? = null
    private var controller: MediaController? = null

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) = update()
    }

    /** A streaming service said why its station stopped (iBroadcast's own reason): show it now. */
    private val problemWatch: () -> Unit = { ContextCompat.getMainExecutor(context).execute { update() } }

    fun connect() {
        if (future != null) return
        com.frequencies.app.playback.ServiceProblems.watch(problemWatch)
        val token = SessionToken(context, ComponentName(context, PlaybackService::class.java))
        val f = MediaController.Builder(context, token).buildAsync()
        future = f
        f.addListener({
            // Assigned before update(), which reads it: else the bar stays empty until the next player event.
            controller = runCatching { f.get() }.getOrNull()?.also { it.addListener(listener) }
            update()
        }, ContextCompat.getMainExecutor(context))
    }

    fun release() {
        com.frequencies.app.playback.ServiceProblems.unwatch(problemWatch)
        controller?.removeListener(listener)
        future?.let { MediaController.releaseFuture(it) }
        future = null
        controller = null
    }

    /** The queue is Saved Frequencies as shown (filtered by tags or search): it follows the filter ([follow]). */
    private var savedQueue = false

    /**
     * Play [station], with the whole list queued so next and previous move
     * along it. [fromSaved]: the list is Saved Frequencies as shown, and
     * the queue keeps following its tag filter and search.
     */
    fun play(station: Station, list: List<Station>, fromSaved: Boolean = false) {
        val c = controller ?: return
        savedQueue = fromSaved
        if (c.currentMediaItem?.mediaId == station.id && c.playbackState != Player.STATE_IDLE) {
            if (fromSaved) follow(list)
            c.play()
            return
        }
        val index = list.indexOfFirst { it.id == station.id }.coerceAtLeast(0)
        c.setMediaItems(list.map { MediaItem.Builder().setMediaId(it.id).build() }, index, C.TIME_UNSET)
        c.prepare()
        c.play()
    }

    /**
     * Play a directory result without saving it (Add Frequency, like the
     * desktop's Play in Discover). The station is handed to the service
     * through [com.frequencies.app.Library.preview].
     */
    fun preview(station: Station) {
        val c = controller ?: return
        savedQueue = false
        c.setMediaItems(listOf(MediaItem.Builder().setMediaId(station.id).build()), 0, C.TIME_UNSET)
        c.prepare()
        c.play()
    }

    /**
     * Saved's filter changed (a tag tapped, a search typed): Next, Previous
     * and Shuffle go by what's shown now, as on the desktop, without
     * interrupting the frequency playing. One that the filter hides keeps
     * playing; Next then goes to the first one shown (user, build 186).
     */
    fun follow(list: List<Station>) {
        val c = controller ?: return
        if (!savedQueue || c.mediaItemCount == 0 || list.isEmpty()) return
        val current = c.currentMediaItem?.mediaId ?: return
        val ids = list.map { it.id }
        val want = if (current in ids) ids else listOf(current) + ids
        val now = (0 until c.mediaItemCount).map { c.getMediaItemAt(it).mediaId }
        if (want == now) return
        val at = want.indexOf(current)
        val index = c.currentMediaItemIndex
        if (index + 1 < c.mediaItemCount) c.removeMediaItems(index + 1, c.mediaItemCount)
        if (index > 0) c.removeMediaItems(0, index)
        fun items(part: List<String>) = part.map { MediaItem.Builder().setMediaId(it).build() }
        if (at > 0) c.addMediaItems(0, items(want.subList(0, at)))
        if (at + 1 < want.size) c.addMediaItems(items(want.subList(at + 1, want.size)))
    }

    fun togglePlay() {
        val c = controller ?: return
        if (c.playWhenReady && c.playbackState != Player.STATE_IDLE) {
            c.pause()
        } else {
            if (c.playbackState == Player.STATE_IDLE) c.prepare()
            c.play()
        }
    }

    fun next() = controller?.let { if (it.hasNextMediaItem()) it.seekToNextMediaItem() else it.seekToDefaultPosition(0) }
    fun previous() = controller?.let {
        if (it.hasPreviousMediaItem()) it.seekToPreviousMediaItem() else it.seekToDefaultPosition(it.mediaItemCount - 1)
    }

    private fun update() {
        val c = controller ?: return
        val item = c.currentMediaItem
        val meta = c.mediaMetadata
        val stationName = (meta.station ?: meta.albumTitle ?: item?.mediaMetadata?.title)?.toString().orEmpty()
        _state.value = NowPlaying(
            stationId = item?.mediaId,
            stationName = stationName,
            title = meta.title?.toString().orEmpty(),
            artist = meta.artist?.toString().orEmpty(),
            artworkUri = meta.artworkUri ?: item?.mediaMetadata?.artworkUri,
            isPlaying = c.isPlaying,
            playWhenReady = c.playWhenReady,
            buffering = c.playbackState == Player.STATE_BUFFERING && c.playWhenReady,
            // A service's own reason first (build 176: iBroadcast's refusal, not "The station's server said no.").
            // The session never carries the player's error (build 198): the service hands it over itself.
            error = (c.playerError ?: com.frequencies.app.playback.ServiceProblems.playerError)
                ?.let { com.frequencies.app.playback.ServiceProblems.of(item?.mediaId) ?: describe(it) },
        )
    }

    private fun describe(e: PlaybackException): String = when (e.errorCode) {
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT -> "Couldn't reach the station."
        PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS -> "The station's server said no."
        PlaybackException.ERROR_CODE_IO_FILE_NOT_FOUND -> "The station's address isn't there anymore."
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
        PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED -> "This stream's format can't be played."
        else -> "The station stopped playing (${e.errorCodeName})."
    }
}
