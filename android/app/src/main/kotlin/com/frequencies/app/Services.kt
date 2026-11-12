package com.frequencies.app

import android.content.Context
import android.net.Uri
import android.os.SystemClock
import android.util.Log
import androidx.browser.customtabs.CustomTabsIntent
import com.frequencies.core.IBroadcast
import com.frequencies.core.IBroadcastAccount
import com.frequencies.core.RedirectCatcher
import com.frequencies.core.Twitch
import com.frequencies.core.YouTube
import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.RequestBody.Companion.toRequestBody
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.Page
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.channel.ChannelInfo
import org.schabi.newpipe.extractor.channel.tabs.ChannelTabInfo
import org.schabi.newpipe.extractor.channel.tabs.ChannelTabs
import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException
import org.schabi.newpipe.extractor.playlist.PlaylistInfo
import org.schabi.newpipe.extractor.services.youtube.YoutubeParsingHelper
import org.schabi.newpipe.extractor.stream.DeliveryMethod
import org.schabi.newpipe.extractor.stream.StreamInfo
import org.schabi.newpipe.extractor.stream.StreamInfoItem

/** Opens a sign-in page in a Custom Tab (the phone's own browser, never a web view in the app). */
fun Context.openInCustomTab(url: String) {
    val intent = CustomTabsIntent.Builder().setShowTitle(true).build()
    // From a screen it opens over the app (back returns here); from elsewhere it needs a task of its own.
    if (this !is android.app.Activity) intent.intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
    intent.launchUrl(this, Uri.parse(url))
}

/**
 * iBroadcast on this phone (ibroadcast.py + ibtoken.TokenKeeper): the
 * sign-in in a Custom Tab with the desktop's loopback redirect, the token
 * renewed ahead of time on a background thread (once a minute it looks,
 * with ten minutes left it renews, five minutes between automatic
 * renewals), and renewed at once when a track was refused.
 */
class IBroadcastKeeper(context: Context, private val scope: CoroutineScope) {
    private val app = context.applicationContext
    val account = IBroadcastAccount(File(File(app.filesDir, "frequencies"), "ibroadcast.json"))
    private val _signedIn = MutableStateFlow(account.signedIn)
    val signedIn: StateFlow<Boolean> = _signedIn.asStateFlow()
    private val renewing = Mutex()
    @Volatile private var last = 0L
    @Volatile private var catcher: RedirectCatcher? = null
    @Volatile private var signing: kotlinx.coroutines.Job? = null
    private val _signingIn = MutableStateFlow(false)
    /** A sign-in is waiting for the iBroadcast page. */
    val signingIn: StateFlow<Boolean> = _signingIn.asStateFlow()
    private val _problem = MutableStateFlow<String?>(null)
    /** What went wrong with the last sign-in, in plain words (null when it worked or none ran). */
    val signInProblem: StateFlow<String?> = _problem.asStateFlow()

    /**
     * Start a sign-in from a screen ([from], an Activity, opens the page).
     * It belongs to the app, not the screen: turning the phone, leaving
     * Settings or the "Back to Frequencies" link don't end it. While it
     * waits a short foreground service ([SignInService]) keeps the app
     * awake and on the network, as Android allows only an app in the
     * foreground or with a foreground service to answer the browser and
     * reach iBroadcast; it goes when the sign-in ends (two minutes at most).
     */
    fun startSignIn(from: Context) {
        if (signing?.isActive == true) return
        _problem.value = null
        _signingIn.value = true
        SignInService.start(app)
        signing = scope.launch {
            val problem = try {
                signIn { url -> from.openInCustomTab(url) }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "[ibroadcast] sign-in: failed: ${e.javaClass.simpleName}: ${e.message}")
                "Something went wrong with the sign-in. Try again."
            } finally {
                SignInService.stop(app)
                _signingIn.value = false
            }
            _problem.value = problem
            Notices.say(problem ?: "Signed in to iBroadcast.")
        }
    }

    fun start() {
        scope.launch(Dispatchers.IO) {
            // Soon after launch too: a phone that slept overnight wakes with a token that has run out.
            delay(3000)
            while (isActive) {
                val cool = SystemClock.elapsedRealtime() - last < IBroadcast.COOLDOWN_SECONDS * 1000
                if (!cool && runCatching { account.needsRefresh() }.getOrDefault(false)) renew(force = false)
                delay(IBroadcast.CHECK_SECONDS * 1000)
            }
        }
    }

    /** One renewal at a time; a second caller gets the first one's answer. Network: IO only. */
    suspend fun renew(force: Boolean): IBroadcastAccount.Renewal = renewing.withLock {
        last = SystemClock.elapsedRealtime()
        val r = withContext(Dispatchers.IO) { account.freshToken(force = force) }
        if (r.token.isNotEmpty()) Log.i(TAG, "[ibroadcast] sign-in renewed" + if (r.lifetime > 0) ", good for ${r.lifetime / 60} min" else "")
        else Log.w(TAG, "[ibroadcast] sign-in renewal failed: ${r.problem}")
        // iBroadcast turned the sign-in itself down (build 176): forget it, so Settings and Add offer Sign in again.
        if (r.dead) {
            account.clear()
            Log.w(TAG, "[ibroadcast] the sign-in is no longer good; signed out on this phone")
        }
        if (r.problem == "signed out" || r.problem.startsWith("rejected")) _signedIn.value = account.signedIn
        r
    }

    /** For the player's loading thread, which may wait. */
    fun renewNow(force: Boolean): IBroadcastAccount.Renewal = runBlocking { renew(force) }

    /**
     * Sign in: the page opens in a Custom Tab; the phone listens on
     * 127.0.0.1:8731 for the answer (the desktop's registered address).
     * Returns null when signed in, else what went wrong in plain words.
     */
    suspend fun signIn(open: (String) -> Unit): String? {
        val verifier = IBroadcast.makeVerifier()
        val state = IBroadcast.makeState()
        catcher?.close()
        val made = withContext(Dispatchers.IO) { runCatching { RedirectCatcher() } }
        val c = made.getOrElse {
            Log.w(TAG, "[ibroadcast] sign-in: can't listen on 127.0.0.1:${IBroadcast.REDIRECT_PORT}: ${it.message}")
            return "Couldn't get ready for the sign-in. Close other sign-ins and try again."
        }
        catcher = c
        Log.i(TAG, "[ibroadcast] sign-in: waiting for the browser on 127.0.0.1:${c.port}")
        withContext(Dispatchers.Main) { open(IBroadcast.authorizeUrl(verifier, state)) }
        val answer = withContext(Dispatchers.IO) { c.await(LOGIN_SECONDS) }
        catcher = null
        if (answer == null) {
            Log.w(TAG, "[ibroadcast] sign-in: no answer from the browser" + c.lastSeen.let { if (it.isEmpty()) "" else " (last asked for $it)" })
            return "The sign-in wasn't finished. Try again when you're ready."
        }
        Log.i(TAG, "[ibroadcast] sign-in: code received, exchanging it")
        if (answer.error.isNotEmpty()) {
            Log.w(TAG, "[ibroadcast] sign-in: iBroadcast said ${answer.error}")
            return "iBroadcast didn't sign you in (${answer.error})."
        }
        if (answer.state != state || answer.code.isEmpty()) {
            Log.w(TAG, "[ibroadcast] sign-in: state mismatch or no code")
            return "That sign-in didn't come from this app. Try again."
        }
        // Twice: the network can be a moment behind when the browser hands back.
        var failed: Throwable? = null
        for (attempt in 1..2) {
            failed = withContext(Dispatchers.IO) { runCatching { account.exchangeCode(answer.code, verifier) }.exceptionOrNull() }
            if (failed == null || failed is IBroadcastAccount.HttpRefused) break
            Log.w(TAG, "[ibroadcast] sign-in: exchange failed (${failed.javaClass.simpleName}: ${failed.message}), trying again")
            delay(1_500)
        }
        _signedIn.value = account.signedIn
        if (failed != null || !account.signedIn) {
            val why = (failed as? IBroadcastAccount.HttpRefused)?.let { "HTTP ${it.code} ${it.body.trim()}" } ?: failed?.message ?: "no token came back"
            Log.w(TAG, "[ibroadcast] sign-in: exchange failed: $why")
            return "iBroadcast didn't finish the sign-in. Try again."
        }
        Log.i(TAG, "[ibroadcast] signed in")
        return null
    }

    fun cancelSignIn() {
        catcher?.close()
        catcher = null
    }

    suspend fun signOut() {
        withContext(Dispatchers.IO) { account.signOut() }
        _signedIn.value = false
        Log.i(TAG, "[ibroadcast] signed out")
    }

    companion object {
        private const val TAG = "Frequencies"
        /** mainwindow.IB_LOGIN_SECONDS */
        const val LOGIN_SECONDS = 120
    }
}

/**
 * YouTube through NewPipeExtractor (the desktop asks yt-dlp): a playlist
 * (or a channel's videos) listed once a day, a video resolved to its best
 * audio when it plays. Network: never on the main thread.
 */
class YouTubeSource(context: Context, private val http: OkHttpClient) {
    private val cache = YouTube.Cache(File(context.cacheDir, "youtube"))

    init { ensureInit(http) }

    /** The playlist's entries and its name (cached a day, like the desktop). */
    fun entries(url: String, force: Boolean = false): Pair<List<YouTube.Entry>, String> {
        if (!force) cache.read(url)?.let { return it }
        val (raw, title) = try {
            if (isChannel(url)) channel(url) else playlist(url)
        } catch (e: Exception) {
            throw YouTube.YouTubeError("Couldn't read that playlist: ${e.message ?: e.javaClass.simpleName}")
        }
        val entries = YouTube.keep(raw)
        if (entries.isEmpty()) throw YouTube.YouTubeError("No playable videos in that playlist.")
        cache.write(url, entries, title)
        return entries to title
    }

    private fun isChannel(url: String) = "list=" !in url && listOf("/channel/", "/@", "/user/", "/c/").any { it in url }

    private fun item(i: StreamInfoItem) = YouTube.Entry(videoId(i.url), i.name.orEmpty(), i.duration.toDouble().coerceAtLeast(0.0))

    private fun playlist(url: String): Pair<List<YouTube.Entry>, String> {
        val service = ServiceList.YouTube
        val info = PlaylistInfo.getInfo(service, url)
        val out = info.relatedItems.map(::item).toMutableList()
        var page: Page? = info.nextPage
        while (page != null && out.size < MOST) {
            val more = PlaylistInfo.getMoreItems(service, url, page)
            out += more.items.map(::item)
            page = more.nextPage
        }
        return out.take(MOST) to info.name.orEmpty()
    }

    private fun channel(url: String): Pair<List<YouTube.Entry>, String> {
        val service = ServiceList.YouTube
        val info = ChannelInfo.getInfo(service, url)
        val tab = info.tabs.firstOrNull { ChannelTabs.VIDEOS in it.contentFilters } ?: info.tabs.firstOrNull()
            ?: throw YouTube.YouTubeError("That channel has no videos.")
        val videos = ChannelTabInfo.getInfo(service, tab)
        val out = videos.relatedItems.filterIsInstance<StreamInfoItem>().map(::item).toMutableList()
        var page: Page? = videos.nextPage
        while (page != null && out.size < MOST) {
            val more = ChannelTabInfo.getMoreItems(service, tab, page)
            out += more.items.filterIsInstance<StreamInfoItem>().map(::item)
            page = more.nextPage
        }
        return out.take(MOST) to info.name.orEmpty()
    }

    /** One video's audio, resolved now (its link expires): the best progressive audio stream. */
    data class Audio(val url: String, val headers: Map<String, String>, val duration: Double, val title: String, val thumbnail: String)

    fun resolve(id: String): Audio {
        val info = try {
            StreamInfo.getInfo(ServiceList.YouTube, YouTube.videoUrl(id))
        } catch (e: Exception) {
            throw YouTube.YouTubeError("Couldn't resolve that video: ${e.message ?: e.javaClass.simpleName}")
        }
        val stream = info.audioStreams
            .filter { it.deliveryMethod == DeliveryMethod.PROGRESSIVE_HTTP && it.isUrl && it.content.isNotEmpty() }
            .maxByOrNull { it.averageBitrate }
            ?: throw YouTube.YouTubeError("No audio stream in that video.")
        val url = stream.content
        val thumb = info.thumbnails.maxByOrNull { it.width * it.height }?.url.orEmpty()
        return Audio(url, headersFor(url), info.duration.toDouble(), info.name.orEmpty(), thumb)
    }

    /** What YouTube wants with each kind of link (as NewPipe's own player sends). */
    private fun headersFor(url: String): Map<String, String> = when {
        YoutubeParsingHelper.isAndroidStreamingUrl(url) -> mapOf("User-Agent" to YoutubeParsingHelper.getAndroidUserAgent(null))
        YoutubeParsingHelper.isIosStreamingUrl(url) -> mapOf("User-Agent" to YoutubeParsingHelper.getIosUserAgent(null))
        YoutubeParsingHelper.isWebStreamingUrl(url) || YoutubeParsingHelper.isWebEmbeddedPlayerStreamingUrl(url) ->
            mapOf("Origin" to "https://www.youtube.com", "Referer" to "https://www.youtube.com/", "User-Agent" to NewPipeDownloader.AGENT)
        else -> mapOf("User-Agent" to NewPipeDownloader.AGENT)
    }

    /** The playlist's name for a new station ("" if it can't be read). */
    fun title(url: String): String = runCatching { entries(url).second }.getOrDefault("")

    companion object {
        /** Long enough for any station; past this a playlist is cut. */
        const val MOST = 500
        @Volatile private var ready = false

        fun ensureInit(http: OkHttpClient) = synchronized(this) {
            if (!ready) { NewPipe.init(NewPipeDownloader(http)); ready = true }
        }

        fun videoId(url: String?): String {
            val u = url.orEmpty()
            Regex("[?&]v=([A-Za-z0-9_-]{6,})").find(u)?.let { return it.groupValues[1] }
            Regex("(?:youtu\\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{6,})").find(u)?.let { return it.groupValues[1] }
            return u
        }
    }
}

/** NewPipeExtractor's network access, through OkHttp (as NewPipe's own DownloaderImpl). */
class NewPipeDownloader(http: OkHttpClient) : Downloader() {
    private val client = http.newBuilder().readTimeout(30, TimeUnit.SECONDS).build()

    override fun execute(request: Request): Response {
        val method = request.httpMethod()
        val data = request.dataToSend()
        val body = when {
            data != null -> data.toRequestBody(null)
            method == "POST" || method == "PUT" -> ByteArray(0).toRequestBody(null)
            else -> null
        }
        val builder = okhttp3.Request.Builder().method(method, body).url(request.url()).header("User-Agent", AGENT)
        for ((name, values) in request.headers()) {
            builder.removeHeader(name)
            values.forEach { builder.addHeader(name, it) }
        }
        client.newCall(builder.build()).execute().use { r ->
            if (r.code == 429) throw ReCaptchaException("reCaptcha Challenge requested", request.url())
            return Response(r.code, r.message, r.headers.toMultimap(), r.body?.string(), r.request.url.toString())
        }
    }

    companion object {
        const val AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
    }
}

/** The Twitch resolver, shared (one client). */
object TwitchSource {
    val resolver: Twitch.Resolver by lazy { Twitch.Resolver() }
}
