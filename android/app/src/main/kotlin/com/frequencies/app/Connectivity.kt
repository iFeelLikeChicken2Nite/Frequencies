package com.frequencies.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import android.util.Log
import com.frequencies.core.OfflineDecider
import java.net.InetSocketAddress
import java.net.Socket
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Is the internet there? (desktop offline.py, build 151, task 214). One
 * answer for the whole app, [offline]. Android's own opinion comes first:
 * no network with internet at all is offline, and a network Android has
 * checked ("validated") is online. A network Android hasn't checked
 * (GrapheneOS lets people turn its connectivity checks off, and some Wi-Fi
 * blocks them) is decided by the desktop's quick look: can a connection be
 * opened to a few well-known addresses. The answer only flips after it held
 * for a while both ways (6 s to go offline, 4 s to come back; core
 * OfflineDecider), so a dropped Wi-Fi for two seconds changes nothing.
 */
object Connectivity {
    private val _offline = MutableStateFlow(false)
    val offline: StateFlow<Boolean> = _offline.asStateFlow()

    private val decider = OfflineDecider()
    private var manager: ConnectivityManager? = null
    @Volatile private var kick = 0L

    /** offline.PROBES: anycast DNS resolvers, up nearly everywhere; a TCP handshake to them sends nothing anyone has to answer. */
    private val PROBES = listOf("1.1.1.1" to 443, "8.8.8.8" to 443, "9.9.9.9" to 443, "1.0.0.1" to 53, "8.8.4.4" to 53)

    fun start(context: Context, scope: CoroutineScope) {
        if (manager != null) return
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return
        manager = cm
        cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { kick = SystemClock.elapsedRealtime() }
            override fun onLost(network: Network) { kick = SystemClock.elapsedRealtime() }
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) { kick = SystemClock.elapsedRealtime() }
        })
        scope.launch(Dispatchers.IO) {
            var lastLook = 0L
            while (isActive) {
                val now = SystemClock.elapsedRealtime()
                val settled = !decider.offline && androidSays() == true
                // All well: a look now and then (Android's callbacks bring the next one sooner). Unsure or offline: every 2 s.
                val every = if (settled) 20_000L else 2_000L
                if (now - lastLook >= every || kick > lastLook) {
                    lastLook = now
                    val online = look()
                    if (decider.look(online, now)) {
                        Log.i(TAG, "[offline] ${if (decider.offline) "the internet is gone: only local files stations play" else "the internet is back"}")
                        _offline.value = decider.offline
                    }
                }
                delay(1_000)
            }
        }
    }

    /** Android's opinion: false = no network with internet, true = a checked one, null = not checked. */
    private fun androidSays(): Boolean? {
        val cm = manager ?: return null
        val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) } ?: return false
        if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return false
        return if (caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) true else null
    }

    private fun look(): Boolean = androidSays() ?: reachable()

    /** offline.reachable: can anything on the internet be reached? Network; never on the main thread. */
    private fun reachable(): Boolean = PROBES.any { (host, port) ->
        runCatching { Socket().use { it.connect(InetSocketAddress(host, port), 2_000) } }.isSuccess
    }

    private const val TAG = "Frequencies"
}
