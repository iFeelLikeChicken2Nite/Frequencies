package com.frequencies.app.playback

import android.os.Bundle
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.CommandButton
import androidx.media3.session.MediaNotification
import androidx.media3.session.MediaSession
import com.google.common.collect.ImmutableList

/**
 * Media3's own playing notification, in the app's accent color (Android
 * tints the small icon and, on older versions, the buttons with it). The
 * color is the accent at the time the notification is drawn: the next
 * song, pause or play brings a new one.
 */
@UnstableApi
class AccentNotifications(
    private val inner: MediaNotification.Provider,
    private val accent: () -> Int,
) : MediaNotification.Provider {
    override fun createNotification(
        mediaSession: MediaSession,
        mediaButtonPreferences: ImmutableList<CommandButton>,
        actionFactory: MediaNotification.ActionFactory,
        onNotificationChangedCallback: MediaNotification.Provider.Callback,
    ): MediaNotification {
        val tinting = MediaNotification.Provider.Callback { onNotificationChangedCallback.onNotificationChanged(tinted(it)) }
        return tinted(inner.createNotification(mediaSession, mediaButtonPreferences, actionFactory, tinting))
    }

    override fun handleCustomCommand(session: MediaSession, action: String, extras: Bundle): Boolean =
        inner.handleCustomCommand(session, action, extras)

    private fun tinted(n: MediaNotification): MediaNotification {
        n.notification.color = (0xFF000000.toInt() or accent())
        return n
    }
}
