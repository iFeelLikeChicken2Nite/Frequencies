package com.frequencies.app.pebble

import com.frequencies.core.Pebble
import io.rebble.pebblekit2.client.BasePebbleListenerService
import io.rebble.pebblekit2.common.model.PebbleDictionary
import io.rebble.pebblekit2.common.model.ReceiveResult
import io.rebble.pebblekit2.common.model.WatchIdentifier
import java.util.UUID

/**
 * The Pebble app binds this while the Frequencies watchapp is open
 * (PebbleKit Android 2). Only the selected Pebble app gets through
 * (Settings › Pebble › "Only this Pebble app"); everything else is
 * [PebbleLink]'s.
 */
class PebbleListener : BasePebbleListenerService() {
    private val ours = UUID.fromString(Pebble.WATCHAPP_UUID)

    override fun onCreate() {
        super.onCreate()
        PebbleLink.applyOnlyApp(this)
    }

    override suspend fun onMessageReceived(watchappUUID: UUID, data: PebbleDictionary, watch: WatchIdentifier): ReceiveResult {
        if (watchappUUID != ours) return ReceiveResult.Nack
        PebbleLink.get(this).received(data.entries.associate { (k, v) -> k.toInt() to v.value })
        return ReceiveResult.Ack
    }

    override fun onAppOpened(watchappUUID: UUID, watch: WatchIdentifier) {
        if (watchappUUID == ours) PebbleLink.get(this).opened()
    }

    override fun onAppClosed(watchappUUID: UUID, watch: WatchIdentifier) {
        if (watchappUUID == ours) PebbleLink.get(this).closed()
    }
}
