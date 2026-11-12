package com.frequencies.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.SystemClock
import android.util.Log
import com.frequencies.core.LatestSaver
import com.frequencies.core.LocalStations
import com.frequencies.core.Station
import com.frequencies.core.SyncData
import com.frequencies.core.SyncPhone
import com.frequencies.core.SyncRun
import com.frequencies.core.SyncRuns
import com.frequencies.core.SyncSession
import com.frequencies.core.SyncWire
import com.frequencies.core.readJson
import com.frequencies.core.str
import com.frequencies.core.num
import com.frequencies.core.truthy
import com.frequencies.core.writeJsonAtomic
import java.io.File
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import kotlin.concurrent.thread
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Sync with a paired desktop on the same network (desktop build 160,
 * notes/sync-protocol.md section 8): the phone pairs by scanning the
 * desktop's QR code or typing its code, finds desktops with DNS-SD
 * (NsdManager) and the UDP probe, and keeps short sessions: open while
 * the app is on screen, closed with `bye` when it goes to the background.
 * The phone never listens, so there is no background server. Nothing
 * here touches the internet.
 *
 * Sync runs (section 5.1, desktop build 167): coming to the foreground
 * starts one at most once per `sync_interval_minutes` (default 30, 0 =
 * only Sync now), another follows every interval while the app stays
 * open, and Sync now starts one at once. Only a run calls a computer; a
 * session it opens stays open while the app is on screen, so edits on
 * either side arrive at once. The spinner ([activity]) turns while a run
 * goes.
 *
 * Files (app storage, never in a backup; android:allowBackup is off):
 * sync/sync.json (this phone's id and name), sync/sync-peers.json (the
 * pair keys), sync/sync-state.json (the stamps). Log lines: `[sync] …`.
 */
@OptIn(FlowPreview::class)
class SyncKeeper(
    private val context: Context,
    private val library: Library,
    private val localMusic: LocalMusic,
    private val scope: CoroutineScope,
    /** A shared setting the desktop changed: what has to follow (photo pack, health checks). */
    private val settingsTaken: (Set<String>) -> Unit,
) {
    private val dir = File(context.filesDir, "sync")
    private val idFile get() = File(dir, "sync.json")
    private val peersFile get() = File(dir, "sync-peers.json")
    private val stateFile get() = File(dir, "sync-state.json")

    /**
     * sync-peers.json is written on its own thread (build 198, batch 26): savePeers used to take
     * this keeper's lock and write the file on the caller's thread, and Settings' Forget and
     * Frequensync's What to sync call it on the main thread, which froze (ANR) while a sync run
     * held the lock. The snapshot is taken here without a lock (peers is a ConcurrentHashMap);
     * the saver writes the newest one.
     */
    private val peersSaver = LatestSaver<JsonObject>("sync-peers", onError = { log("couldn't save the paired devices: $it") }) {
        writeJsonAtomic(peersFile, it)
    }

    val me: String
    val name: String

    /**
     * A paired computer. [what]: what this phone syncs with it (section 6.4, kept as the
     * desktop keeps it in sync-peers.json); [ask]: paired from here and the first
     * "What to sync" isn't answered yet, so nothing is sent or taken and runs skip it.
     */
    class Peer(val id: String, var name: String, val key: ByteArray, val pairedAt: Double, var lastSync: Double, var pairAgain: Boolean = false,
               @Volatile var what: SyncData.What? = null, @Volatile var ask: Boolean = false)

    private val peers = ConcurrentHashMap<String, Peer>()
    private val store: SyncData.Store
    private val phone: SyncPhone

    /** A desktop seen on the network. */
    private class Seen(var name: String, val addrs: MutableList<InetSocketAddress>, var pair: Boolean, var at: Long)
    private val found = ConcurrentHashMap<String, Seen>()
    private val sessions = ConcurrentHashMap<String, SyncSession>()
    private val connecting = ConcurrentHashMap<String, SyncSession>()
    private val tried = ConcurrentHashMap<String, Long>()

    data class Row(val id: String, val name: String, val status: String, val asking: Boolean = false)
    data class Ui(val name: String, val summary: String, val rows: List<Row>, val interval: Int = SyncRuns.INTERVAL_DEFAULT)

    private val _ui = MutableStateFlow(Ui("", "", emptyList()))
    val ui: StateFlow<Ui> = _ui.asStateFlow()

    /**
     * What the spinners show: a device that answered syncing ([busy], with [text] as its words) and the last
     * run's problem ("" for none). [looking]: a run is still looking for its devices, which turns nothing
     * (task 245, build 198): the icon spins only once a paired device has answered.
     */
    data class Activity(val busy: Boolean = false, val text: String = "", val problem: String = "", val paired: Boolean = false, val looking: Boolean = false)

    private val _activity = MutableStateFlow(Activity())
    val activity: StateFlow<Activity> = _activity.asStateFlow()

    // Sync runs (section 5.1). Times on SystemClock.elapsedRealtime.
    @Volatile private var run: SyncRun? = null
    @Volatile private var lastRunAt = 0L
    @Volatile private var pulseUntil = 0L
    @Volatile private var problem = ""
    /** Device id -> when its state last came in a session. */
    private val stateAt = ConcurrentHashMap<String, Long>()
    @Volatile private var probeNow = false

    @Volatile private var foreground = false
    @Volatile private var pairing = 0
    private var onlineJob: Job? = null
    private var leaveJob: Job? = null

    init {
        SyncWire.log = { Log.i(TAG, "[sync] $it") }
        dir.mkdirs()
        val ident = readJson(idFile) as? JsonObject
        val savedId = ident?.get("device_id").str()
        me = if (SyncWire.isDeviceId(savedId)) savedId!! else SyncWire.newDeviceId()
        name = ident?.get("device_name").str()?.takeIf { it.isNotBlank() } ?: (Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android phone")
        if (savedId != me || ident?.get("device_name").str() != name) {
            writeJsonAtomic(idFile, JsonObject(mapOf("device_id" to JsonPrimitive(me), "device_name" to JsonPrimitive(name))))
        }
        loadPeers()
        val artDir = library.files.artDir
        SyncData.shrink = { big, sha -> smallerCopy(big, sha, artDir) }
        store = SyncData.Store.from(readJson(stateFile))
        phone = SyncPhone(
            me = me,
            store = store,
            saveStore = { writeJsonAtomic(stateFile, it.toJson()) },
            artDir = library.files.artDir,
            library = library,
            pathHere = ::folderHere,
            taken = ::taken,
            forgot = { pid -> forgetLocally(pid, "forgot this device") },
            what = ::whatFor,
        )
        refresh()
        // Date this phone's edits when they are made (2 s after the last
        // change), and send them to an open session at once.
        scope.launch(Dispatchers.IO) {
            combine(library.stations, library.settings, library.history) { a, b, c -> Triple(a, b, c) }
                .drop(1)
                .debounce(2_000)
                .collect {
                    if (peers.isEmpty()) return@collect
                    if (sessions.isEmpty()) runCatching { phone.observeNow() }
                    else sessions.values.forEach { s -> runCatching { s.push() } }
                }
        }
        log("this device: '$name' (${me.take(8)}…), ${peers.size} paired")
    }

    /** A music folder station's own folder is here, so an incoming one doesn't replace it. */
    private fun folderHere(s: Station): Boolean {
        val path = LocalStations.localPath(s).trim()
        if (path.isEmpty()) return false
        return if (LocalStations.isLocal(s)) localMusic.onThisPhone(s) else File(path).exists()
    }

    // -- the app on screen or not ---------------------------------------------

    /** MainActivity's onStart / onStop. */
    fun foreground(on: Boolean) {
        foreground = on
        if (on) {
            leaveJob?.cancel()
            // At most once per interval (section 8); never with the interval off.
            if (SyncRuns.dueOnForeground(interval(), lastRunAt, SystemClock.elapsedRealtime())) {
                val why = if (lastRunAt == 0L) "launch" else "back on screen"
                scope.launch(Dispatchers.IO) { startRun(manual = false, why = why) }
            }
            if (onlineJob?.isActive != true) onlineJob = scope.launch(Dispatchers.IO) { online() }
        } else {
            leaveJob?.cancel()
            leaveJob = scope.launch(Dispatchers.IO) {
                // A turn of the screen is not leaving: wait a moment.
                delay(3_000)
                if (foreground) return@launch
                onlineJob?.cancel()
                stopLooking()
                // Each session finishes what it came for (at most 8 s more), then says bye.
                val until = System.currentTimeMillis() + 8_000
                while (sessions.values.any { !it.settled(800) } && System.currentTimeMillis() < until && !foreground) delay(250)
                if (foreground) return@launch
                for (s in sessions.values + connecting.values) s.close("bye")
                // A run still going ends quietly: the phone isn't on screen to wait for it.
                run?.let { endRun(it, emptyList(), quiet = true) }
            }
        }
    }

    private suspend fun online() {
        while (scope.isActive && (foreground || pairing > 0 || run != null)) {
            val r = run
            val now = System.currentTimeMillis()
            val clock = SystemClock.elapsedRealtime()
            // The interval while the app stays open.
            val minutes = interval()
            if (r == null && foreground && minutes != 0 && peers.values.any { !it.ask } && clock - lastRunAt >= minutes * 60_000L) {
                startRun(manual = false, why = "interval")
            }
            // Only a run calls a computer (desktop build 167).
            val missing = run?.peers.orEmpty().filter { peers.containsKey(it) && !sessions.containsKey(it) && !connecting.containsKey(it) }
            if (missing.isNotEmpty() || pairing > 0) startLooking() else stopLooking()
            for (pid in missing) {
                val seen = found[pid] ?: continue
                if (now - seen.at > FOUND_FOR_MS) continue
                if (now - (tried[pid] ?: 0L) < SyncRuns.RETRY_MS) continue
                tried[pid] = now
                connect(pid, seen.addrs.toList())
            }
            checkRun()
            refresh()
            delay(if (run != null) 250 else 1_000)
        }
    }

    // -- sync runs (section 5.1) ---------------------------------------------------

    private fun interval(): Int = SyncRuns.interval(library.settings.value)

    /** Settings' Sync automatically: this phone's own, never sent. */
    fun setInterval(minutes: Int) {
        if (minutes !in SyncRuns.INTERVAL_CHOICES) return
        // Off the main thread (build 198): the library's lock can be held by a sync merge.
        scope.launch(Dispatchers.IO) {
            library.updateSettings { it[SyncRuns.INTERVAL_KEY] = JsonPrimitive(minutes) }
            // Turned on after a launch with it off: the first run comes one interval from now.
            if (lastRunAt == 0L) lastRunAt = SystemClock.elapsedRealtime()
            log("syncs " + if (minutes == 0) "only when asked (Sync now)" else SyncRuns.label(minutes).lowercase())
            refresh()
        }
    }

    /** Settings' Sync now: look for the paired computers and sync at once. */
    fun syncNow() {
        if (peers.isEmpty()) {
            setProblem("Pair a device first.")
            return
        }
        setProblem("")
        // startRun takes this keeper's lock, which a sync run's thread may hold: never on the main thread (build 198).
        scope.launch(Dispatchers.IO) { startRun(manual = true, why = "Sync now") }
    }

    @Synchronized
    private fun startRun(manual: Boolean, why: String, only: Set<String>? = null) {
        // A pairing still waiting for its "What to sync" isn't called (desktop build 169).
        val wanted = peers.values.filter { !it.ask && (only == null || it.id in only) }.map { it.id }
        if (wanted.isEmpty()) return
        val now = SystemClock.elapsedRealtime()
        val r = run ?: SyncRun(why, now).also {
            run = it
            lastRunAt = now
            log("sync run ($why): ${wanted.size} device(s)")
        }
        r.add(wanted, manual, now, why)
        for (pid in wanted) {
            tried.remove(pid)
            val s = sessions[pid]
            if (s != null && s.open && s.gotState) {
                // Connected already: the two keep each other up to date. Say everything once more and count it done.
                thread(isDaemon = true, name = "sync-push") { runCatching { s.push(force = true); s.askArt() } }
                r.reached(pid)
            }
        }
        probeNow = true
        if (onlineJob?.isActive != true) onlineJob = scope.launch(Dispatchers.IO) { online() }
        publish()
    }

    private fun checkRun() {
        if (run == null) return
        // Asked before this keeper's lock is taken, never under it: a session thread holds the
        // sync store's lock while a state is taken and then calls taken() -> savePeers(), which
        // needs this keeper's lock. Asking under it deadlocked both threads (and the screen) for
        // good with a big library (desktop build 178 notes).
        val artWaiting = peers.keys.any { phone.waitingArt(it).isNotEmpty() }
        synchronized(this) {
            val r = run ?: return
            val verdict = r.check(
                SystemClock.elapsedRealtime(),
                peers.keys.toSet(),
                { pid -> stateAt[pid] },
                artWaiting,
                { pid -> sessions[pid]?.open == true },
            )
            if (verdict is SyncRun.Verdict.Over) endRun(r, verdict.missing, quiet = false)
        }
    }

    @Synchronized
    private fun endRun(r: SyncRun, missing: List<String>, quiet: Boolean) {
        if (run !== r) return
        run = null
        val names = missing.mapNotNull { peers[it]?.name }
        log("sync run (${r.why}) done" + if (names.isNotEmpty()) "; not found: ${names.joinToString(", ")}" else "")
        if (!quiet) r.problem(missing) { peers[it]?.name }?.let(::setProblem)
        publish()
    }

    /** A state that changed something came in between runs: a short turn of the spinner. */
    private fun pulse() {
        pulseUntil = SystemClock.elapsedRealtime() + SyncRuns.PULSE_MS
        publish()
        scope.launch { delay(SyncRuns.PULSE_MS + 50); publish() }
    }

    private fun setProblem(text: String) {
        if (text == problem) return
        problem = text
        if (text.isNotEmpty()) log("shown: $text")
        publish()
    }

    private fun publish() {
        val r = run
        // Only the devices in the run that answered (a session open) make it busy; looking stays quiet (task 245).
        val answered = runCatching { r?.peers.orEmpty().filter { sessions[it]?.open == true } }.getOrDefault(emptyList())
        val busy = answered.isNotEmpty() || SystemClock.elapsedRealtime() < pulseUntil
        val names = answered.mapNotNull { peers[it]?.name }.sorted()
        _activity.value = Activity(busy, if (busy) SyncRuns.busyText(names) else "", problem, peers.isNotEmpty(), looking = r != null && !busy)
    }

    private fun connect(pid: String, addrs: List<InetSocketAddress>) {
        val peer = peers[pid] ?: return
        val session = SyncSession(phone, pid)
        connecting[pid] = session
        thread(name = "sync-$pid".take(15), isDaemon = true) {
            val reason = session.run(addrs, me, name, peer.key) { s ->
                connecting.remove(pid)
                sessions[pid]?.close(null)
                sessions[pid] = s
                peer.pairAgain = false
                if (s.peerName.isNotEmpty() && s.peerName != peer.name) { peer.name = s.peerName; savePeers() }
                log("connected to '${peer.name}' (we called)")
                refresh()
                if (!foreground && pairing == 0) s.close("bye")
            }
            val wasOpen = sessions.remove(pid, session)
            connecting.remove(pid, session)
            tried[pid] = System.currentTimeMillis()
            if (reason == "wrong key") {
                log("'${peer.name}' has another key now: pair again")
                peer.pairAgain = true
                synchronized(this@SyncKeeper) { run?.failed(pid, SyncRuns.pairedAgain(peer.name)) }
            } else if (wasOpen) {
                log("session with '${peer.name}' ended: $reason")
            } else if (peers.containsKey(pid)) {
                log("couldn't reach '${peer.name}': $reason")
            }
            refresh()
        }
    }

    private fun taken(pid: String, res: SyncData.Merged) {
        val peer = peers[pid] ?: return
        peer.lastSync = System.currentTimeMillis() / 1000.0
        stateAt[pid] = SystemClock.elapsedRealtime()
        savePeers()
        if (res.changed && run == null) pulse()
        if (res.changed) {
            val what = buildList {
                if (res.stations) add("stations")
                if (res.history) add("history")
                if (res.settings.isNotEmpty()) add("settings " + res.settings.sorted().joinToString(", "))
            }
            log("took from '${peer.name}': ${what.joinToString(" ")}")
            if (res.settings.isNotEmpty()) scope.launch { settingsTaken(res.settings) }
        }
        refresh()
    }

    // -- finding desktops (section 2) ------------------------------------------

    private val nsd by lazy { context.getSystemService(NsdManager::class.java) }
    private var browsing: NsdManager.DiscoveryListener? = null
    private var multicast: WifiManager.MulticastLock? = null
    @Volatile private var probing: Thread? = null
    private val resolving = ArrayDeque<NsdServiceInfo>()
    @Volatile private var resolveBusy = false

    @Synchronized
    private fun startLooking() {
        if (browsing != null || probing != null) return
        runCatching {
            val wifi = context.applicationContext.getSystemService(WifiManager::class.java)
            multicast = wifi.createMulticastLock("frequencies-sync").apply { setReferenceCounted(false); acquire() }
        }
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) { log("mDNS: couldn't look ($errorCode); the broadcast still works") }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceFound(info: NsdServiceInfo) = resolve(info)
            override fun onServiceLost(info: NsdServiceInfo) {}
        }
        browsing = listener
        runCatching { nsd.discoverServices(SyncWire.SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener) }
            .onFailure { log("mDNS: not available ($it); the broadcast still works"); browsing = null }
        probing = thread(name = "sync-probe", isDaemon = true) { probeLoop() }
    }

    @Synchronized
    private fun stopLooking() {
        browsing?.let { l -> runCatching { nsd.stopServiceDiscovery(l) } }
        browsing = null
        probing?.interrupt()
        probing = null
        runCatching { multicast?.release() }
        multicast = null
    }

    /** One resolve at a time (older Androids refuse a second one meanwhile). */
    private fun resolve(info: NsdServiceInfo) {
        synchronized(resolving) {
            resolving.addLast(info)
            if (resolveBusy) return
            resolveBusy = true
        }
        nextResolve()
    }

    @Suppress("DEPRECATION")
    private fun nextResolve() {
        val info = synchronized(resolving) {
            resolving.removeFirstOrNull().also { if (it == null) resolveBusy = false }
        } ?: return
        runCatching {
            nsd.resolveService(info, object : NsdManager.ResolveListener {
                override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = nextResolve()
                override fun onServiceResolved(r: NsdServiceInfo) {
                    val (pid, pair) = SyncWire.parseTxt(r.attributes, me) ?: return nextResolve()
                    val host = r.host
                    if (host is Inet4Address) {
                        seen(pid, SyncWire.nameOfInstance(r.serviceName, pid), host, r.port, pair)
                    }
                    nextResolve()
                }
            })
        }.onFailure { nextResolve() }
    }

    /** The UDP fallback: ask every desktop to say where it is, every 5 s. */
    private fun probeLoop() {
        val sock = runCatching { DatagramSocket().apply { broadcast = true; soTimeout = 1_000 } }.getOrNull() ?: return
        try {
            val probe = SyncWire.probe(me)
            val buf = ByteArray(2048)
            var last = 0L
            while (!Thread.currentThread().isInterrupted) {
                // A run asks at once (section 5.1), then every 5 s.
                if (probeNow || System.currentTimeMillis() - last > 5_000) {
                    probeNow = false
                    last = System.currentTimeMillis()
                    for (addr in broadcastAddresses()) {
                        runCatching { sock.send(DatagramPacket(probe, probe.size, addr, SyncWire.UDP_PORT)) }
                    }
                }
                val packet = DatagramPacket(buf, buf.size)
                try {
                    sock.receive(packet)
                } catch (e: java.net.SocketTimeoutException) {
                    continue
                } catch (e: Exception) {
                    break
                }
                val f = SyncWire.parseAnnouncement(packet.data, packet.length, me) ?: continue
                seen(f.id, f.name, packet.address, f.port, f.pair)
            }
        } finally {
            sock.close()
        }
    }

    private fun broadcastAddresses(): List<InetAddress> {
        val out = linkedSetOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
        runCatching {
            for (nif in NetworkInterface.getNetworkInterfaces()) {
                if (!nif.isUp || nif.isLoopback) continue
                for (a in nif.interfaceAddresses) a.broadcast?.let { out.add(it) }
            }
        }
        return out.toList()
    }

    private fun seen(pid: String, name: String, ip: InetAddress, port: Int, pair: Boolean) {
        val addr = InetSocketAddress(ip, port)
        val entry = found.getOrPut(pid) {
            if (peers.containsKey(pid)) log("found '${peers[pid]?.name}' at ${ip.hostAddress}:$port")
            Seen(name, ArrayList(), pair, 0)
        }
        synchronized(entry) {
            entry.at = System.currentTimeMillis()
            entry.pair = pair
            if (name.isNotBlank()) entry.name = name
            entry.addrs.remove(addr)
            entry.addrs.add(0, addr)
            while (entry.addrs.size > 4) entry.addrs.removeAt(entry.addrs.lastIndex)
        }
    }

    // -- pairing (section 4: this phone enters the desktop's code) ---------------

    /** Pair with what a QR code says, or a typed code. Returns the line to show. */
    suspend fun pair(qrText: String?, typed: String?): Triple<Boolean, String, String?> = withContext(Dispatchers.IO) {
        val qr = SyncWire.parseQr(qrText)
        val code = qr?.code ?: SyncWire.cleanCode(typed)
        if (code.isEmpty()) return@withContext Triple(false, "That isn't a code. It's 10 letters and numbers, like ABCDE-12345.", null)
        pairing++
        if (onlineJob?.isActive != true) onlineJob = scope.launch(Dispatchers.IO) { online() }
        try {
            startLooking()
            val tries = ArrayList<InetSocketAddress>()
            if (qr != null) qr.ips.forEach { ip -> runCatching { tries.add(InetSocketAddress(InetAddress.getByName(ip), qr.port)) } }
            var reached = false
            var lastError = ""
            suspend fun attempt(addrs: List<InetSocketAddress>): Triple<Boolean, String, String?>? {
                for (a in addrs) {
                    val sock = Socket()
                    try {
                        sock.connect(a, 3_000)
                        sock.soTimeout = SyncWire.STEP_TIMEOUT_MS
                        reached = true
                        val p = SyncWire.clientPair(sock.getInputStream().buffered(), sock.getOutputStream(), me, name, code)
                        paired(p, a)
                        return Triple(true, "Paired with ${p.name}. Choose what to sync.", p.id)
                    } catch (e: SyncWire.SyncError) {
                        lastError = e.message.orEmpty()
                        if (lastError == "wrong code") return Triple(false, "That code didn't match. Check it and try again.", null)
                    } catch (e: SyncWire.OtherVersion) {
                        lastError = e.message.orEmpty()
                    } catch (e: Exception) {
                        lastError = e.message ?: e.javaClass.simpleName
                    } finally {
                        runCatching { sock.close() }
                    }
                }
                return null
            }
            attempt(tries)?.let { return@withContext it }
            // Not there (or no QR): look for it on the network for up to 8 s.
            val until = System.currentTimeMillis() + 8_000
            val done = HashSet<InetSocketAddress>(tries)
            while (System.currentTimeMillis() < until) {
                val targets = found.entries.filter { (pid, f) ->
                    if (qr != null) pid == qr.id else f.pair && System.currentTimeMillis() - f.at < FOUND_FOR_MS
                }.flatMap { synchronized(it.value) { it.value.addrs.toList() } }.filter { it !in done }
                done.addAll(targets)
                attempt(targets)?.let { return@withContext it }
                delay(500)
            }
            log("pairing failed: ${lastError.ifEmpty { "no device showing a code" }}")
            if (reached) Triple(false, "Couldn't pair with that device. Try again.", null)
            else Triple(false, "Couldn't find a device showing that code on this network. Check both are on the same Wi-Fi and the code is still showing.", null)
        } finally {
            pairing--
        }
    }

    private fun paired(p: SyncWire.Paired, at: InetSocketAddress) {
        // The phone always starts the sync (it typed the code or scanned it), so "What to sync"
        // comes first (desktop build 169); a choice from an earlier pairing is shown again.
        peers[p.id] = Peer(p.id, p.name.ifEmpty { "another device" }, p.key, System.currentTimeMillis() / 1000.0, 0.0,
            what = peers[p.id]?.what, ask = true)
        savePeers()
        log("paired with '${p.name}' (${p.id.take(8)}…)")
        sessions.remove(p.id)?.close(null)         // it used the old key
        tried.remove(p.id)
        found.getOrPut(p.id) { Seen(p.name, ArrayList(), false, 0) }.let { seen ->
            synchronized(seen) {
                seen.at = System.currentTimeMillis()
                seen.addrs.remove(at); seen.addrs.add(0, at)
            }
        }
        refresh()
        // The run comes when What to sync is answered (setWhat).
    }

    // -- what to sync (section 6.4, desktop build 169) ---------------------------

    /** This phone's choice for one computer; a pairing waiting for its choice sends and takes nothing. */
    fun whatFor(pid: String): SyncData.What {
        val peer = peers[pid] ?: return SyncData.What.ALL
        return if (peer.ask) SyncData.nothingWhat() else peer.what ?: SyncData.What.ALL
    }

    /** The choice to show in the Frequensync dialog. */
    fun savedWhat(pid: String): SyncData.What = peers[pid]?.what ?: SyncData.What.ALL

    /**
     * Save {device id: choice}. A pairing waiting for its choice starts syncing; a part
     * turned on syncs at once (or everything does, with [sync]: the dialog's Sync button).
     */
    fun setWhat(choices: Map<String, SyncData.What>, sync: Boolean) {
        val start = HashSet<String>()
        for ((pid, raw) in choices) {
            val peer = peers[pid] ?: continue
            val what = SyncData.cleanWhat(raw.toJson())
            val was = whatFor(pid)
            val asked = peer.ask
            peer.what = what
            peer.ask = false
            val turnedOn = SyncData.WHAT_PARTS.any { what.on(it) && !was.on(it) } ||
                SyncData.SETTING_GROUPS.any { (g, _) -> what.group(g) && !was.group(g) }
            val off = what.offList().filter { it != "desktop" }
            log("what to sync with '${peer.name}': " + if (off.isEmpty()) "everything" else "all but " + off.joinToString(", "))
            // An open session says everything again under the new choice; a part turned on
            // is also taken at once from the other device's last state (desktop build 171).
            sessions[pid]?.let { s ->
                thread(isDaemon = true, name = "sync-push") {
                    runCatching { if (turnedOn) s.retake() else { s.push(force = true); s.askArt() } }
                }
            }
            if (asked || turnedOn || sync) start.add(pid)
        }
        savePeers()
        if (sync) setProblem("")
        refresh()
        if (start.isNotEmpty()) scope.launch(Dispatchers.IO) { startRun(manual = sync, why = if (sync) "Sync now" else "what to sync", only = start) }
    }

    /** Settings' "Forget this device": both sides stop; nothing is deleted on either. */
    fun forget(pid: String) {
        val s = sessions.remove(pid)
        forgetLocally(pid, "forgot")
        if (s != null) thread(isDaemon = true) { s.close("forget") }
    }

    private fun forgetLocally(pid: String, how: String) {
        val peer = peers.remove(pid) ?: return
        stateAt.remove(pid)
        savePeers()
        log(if (how == "forgot") "forgot '${peer.name}'" else "'${peer.name}' forgot this device")
        refresh()
    }

    // -- what Settings shows (section 9) -----------------------------------------

    fun refresh() {
        val rows = peers.values.sortedBy { it.name.lowercase() }.map { Row(it.id, it.name, peerText(it), it.ask) }
        _ui.value = Ui(name, summary(), rows, interval())
        publish()
    }

    /** Settings' "… ago" lines get older by themselves; Settings calls this while open. */
    fun tick() = refresh()

    private fun peerText(p: Peer): String {
        if (p.pairAgain) return "Pair again: it has another key now"
        if (p.ask) return "Paired · choose what to sync to start syncing"
        val last = p.lastSync
        val now = System.currentTimeMillis() / 1000.0
        if (sessions.containsKey(p.id)) {
            if (last > 0 && now - last < 60) return "Last synced just now"
            return if (last > 0) "Connected, last synced " + ago(now - last) else "Connected"
        }
        if (last > 0) return "Last synced " + ago(now - last) + " · waiting for it on this network"
        return "Waiting for it on this network"
    }

    private fun summary(): String {
        if (peers.isEmpty()) return "Not paired with any device yet."
        val best = peers.values.maxBy { it.lastSync }
        if (best.lastSync <= 0) return "Waiting for ${best.name}"
        val age = System.currentTimeMillis() / 1000.0 - best.lastSync
        val whenText = if (age < 60 || sessions.containsKey(best.id)) "just now" else ago(age)
        return "Last synced with ${best.name} $whenText"
    }

    // -- files ----------------------------------------------------------------------

    private fun loadPeers() {
        val all = (readJson(peersFile) as? JsonObject)?.get("peers") as? JsonObject ?: return
        for ((pid, e) in all) {
            val o = e as? JsonObject ?: continue
            val key = runCatching { SyncWire.unb64(o["key"].str()) }.getOrNull() ?: continue
            if (key.size != 32 || !SyncWire.isDeviceId(pid)) continue
            peers[pid] = Peer(pid, o["name"].str() ?: "another device", key, o["paired_at"].num() ?: 0.0, o["last_sync"].num() ?: 0.0,
                what = (o["what"] as? JsonObject)?.let(SyncData::cleanWhat), ask = o["ask"]?.let(::truthy) == true)
        }
    }

    private fun savePeers() {
        peersSaver.save(JsonObject(mapOf(
            "v" to JsonPrimitive(1),
            "peers" to JsonObject(peers.values.associate { p ->
                p.id to JsonObject(buildMap {
                    put("name", JsonPrimitive(p.name))
                    put("key", JsonPrimitive(SyncWire.b64(p.key)))
                    put("paired_at", JsonPrimitive(p.pairedAt))
                    put("last_sync", JsonPrimitive(p.lastSync))
                    // Build 170 (desktop 169's keys): what to sync with it, and whether that is still to be chosen.
                    p.what?.let { put("what", it.toJson()) }
                    if (p.ask) put("ask", JsonPrimitive(true))
                })
            }),
        )))
    }

    private fun log(text: String) = Log.i(TAG, "[sync] $text")

    companion object {
        private const val TAG = "Frequencies"
        private const val FOUND_FOR_MS = 45_000L

        fun ago(seconds: Double): String {
            val s = seconds.toLong().coerceAtLeast(0)
            return when {
                s < 60 -> "just now"
                s < 3600 -> (s / 60).let { "$it minute${if (it != 1L) "s" else ""} ago" }
                s < 86400 -> (s / 3600).let { "$it hour${if (it != 1L) "s" else ""} ago" }
                else -> (s / 86400).let { "$it day${if (it != 1L) "s" else ""} ago" }
            }
        }
    }
}

/**
 * sync_core._smaller_copy (section 6.1, desktop build 167): a picture over
 * 8 MiB goes to the other device as a copy at most 2048 px on its long
 * side, PNG when it has see-through parts, else JPEG at quality 90. Made
 * once, named after the big file's sha; null when it can't be made.
 */
internal fun smallerCopy(big: File, sha: String, artDir: File): File? {
    for (ext in listOf("jpg", "png")) {
        val have = File(artDir, SyncData.smallCopyName(sha, ext))
        if (have.isFile && have.length() in 1..SyncData.ART_SEND_MAX) return have
    }
    val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
    android.graphics.BitmapFactory.decodeFile(big.path, bounds)
    val w = bounds.outWidth
    val h = bounds.outHeight
    if (w <= 0 || h <= 0) return null
    var sample = 1
    while (maxOf(w, h) / (sample * 2) >= SyncData.ART_SHRINK_PX) sample *= 2
    val decoded = android.graphics.BitmapFactory.decodeFile(big.path, android.graphics.BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
    val long = maxOf(decoded.width, decoded.height)
    val bitmap = if (long > SyncData.ART_SHRINK_PX) {
        val scale = SyncData.ART_SHRINK_PX.toFloat() / long
        android.graphics.Bitmap.createScaledBitmap(decoded, maxOf(1, (decoded.width * scale).toInt()), maxOf(1, (decoded.height * scale).toInt()), true)
    } else decoded
    val png = bitmap.hasAlpha()
    val out = File(artDir, SyncData.smallCopyName(sha, if (png) "png" else "jpg"))
    val tmp = File(out.path + ".tmp")
    return try {
        artDir.mkdirs()
        tmp.outputStream().use { bitmap.compress(if (png) android.graphics.Bitmap.CompressFormat.PNG else android.graphics.Bitmap.CompressFormat.JPEG, 90, it) }
        if (!tmp.renameTo(out)) { out.delete(); tmp.renameTo(out) }
        out.takeIf { it.isFile && it.length() in 1..SyncData.ART_SEND_MAX }
    } catch (e: Exception) {
        tmp.delete()
        null
    } finally {
        if (bitmap !== decoded) bitmap.recycle()
        decoded.recycle()
    }
}

