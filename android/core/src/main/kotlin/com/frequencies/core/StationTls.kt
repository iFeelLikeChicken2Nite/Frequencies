package com.frequencies.core

import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * The desktop's per-station `insecure` flag (Goodvibes' <insecure>,
 * player.py's tls-verify): a station whose server has a broken security
 * certificate (self-signed, expired, or made out for another name) still
 * plays. Only that station's own requests skip the check: its stream,
 * the playlist behind it, the health check and the stream titles that
 * come inside the stream. Everything else in the app, and every other
 * station, keeps checking certificates as before. Nothing here is global.
 */
object StationTls {
    /**
     * Every station plays even with a broken certificate, and there is no
     * switch (user, build 170: "this should be enabled for every station
     * with no option visible"). Only station audio is meant: its stream,
     * its playlist, its health check. Everything else in the app (sync,
     * reports, lyrics, artist info, pictures) still checks certificates.
     * The `insecure` flag stays in station data so the desktop and shared
     * files keep theirs.
     */
    const val EVERY_STATION = true

    /** Whether [station]'s own requests skip the check: all of them now ([EVERY_STATION]). */
    fun skipsCheck(station: Station?): Boolean = EVERY_STATION || station?.insecure == true
    /** Marks a request as one of a flagged station's; [Router] takes it off before sending. */
    const val MARK = "X-Frequencies-Insecure"

    /** Accepts any certificate. Only ever reached through [insecureCopy]. */
    private object TrustAll : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    /**
     * A copy of [base] (same timeouts, pool and dispatcher) that doesn't
     * check certificates or host names. For flagged stations only.
     */
    fun insecureCopy(base: OkHttpClient): OkHttpClient {
        val context = SSLContext.getInstance("TLS").apply { init(null, arrayOf(TrustAll), SecureRandom()) }
        return base.newBuilder()
            .sslSocketFactory(context.socketFactory, TrustAll)
            .hostnameVerifier { _, _ -> true }
            .build()
    }

    /** The two clients: [secure] for everything, [insecure] made on first use. */
    class Clients(val secure: OkHttpClient) {
        val insecure: OkHttpClient by lazy { insecureCopy(secure) }
        fun forStation(station: Station?): OkHttpClient = if (skipsCheck(station)) insecure else secure
        fun forFlag(insecure: Boolean): OkHttpClient = if (insecure) this.insecure else secure
    }

    /**
     * The playback service's HTTP for streams. A request carrying [MARK]
     * (a flagged station's stream or playlist, marked by the service) goes
     * through the insecure client, and its server's name is remembered for
     * the rest of that station's play (an HLS stream fetches its pieces
     * without the mark); [tunedTo] forgets the names when another station
     * starts. Everything else is checked as usual.
     */
    class Router(private val clients: Clients) : Call.Factory {
        private val hosts = ConcurrentHashMap.newKeySet<String>()

        /**
         * A station starts. An unflagged one: nothing is trusted any more.
         * A flagged one: its server is trusted from the first byte (and the
         * servers its playlist sent it to, marked already, stay).
         */
        fun tunedTo(insecure: Boolean, url: String) {
            if (!insecure) hosts.clear() else hostOf(url)?.let { hosts.add(it) }
        }

        fun trusts(host: String): Boolean = host.lowercase() in hosts

        override fun newCall(request: Request): Call {
            val host = request.url.host.lowercase()
            if (request.header(MARK) != null) {
                hosts.add(host)
                return clients.insecure.newCall(request.newBuilder().removeHeader(MARK).build())
            }
            return (if (host in hosts) clients.insecure else clients.secure).newCall(request)
        }

        private fun hostOf(url: String): String? = runCatching { java.net.URI(url.trim()).host?.lowercase() }.getOrNull()
    }

    /** Settings > station: the switch and what it says (Android's own words; the desktop has no switch). */
    const val SWITCH_TITLE = "Allow a broken security certificate"
    const val SWITCH_SUMMARY = "Only for this station, when it won't play because its server's certificate is out of date or self-signed. " +
        "Its connection is then not checked, so someone on the same network could change what it sends. Leave this off unless you need it."
}
