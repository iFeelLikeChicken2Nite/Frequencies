"""Frequencies — sync between paired devices on the same network
(task 126, Batch CJ).

Protocol: notes/sync-protocol.md. The data side (stamps, merging, crypto,
framing) is `sync_core`; this file is the network and the window side:

  * `SyncService` (one per window, `service()`): pairs, finds paired
    devices (DNS-SD `_frequencies._tcp` through `zeroconf`, plus a UDP
    broadcast on 47615 when mDNS is blocked), keeps one encrypted session
    per paired device that is open on the network, sends this device's
    state at once and again 2 s after the last change (storage's save
    hook), merges what comes in, saves it and refreshes the window.
  * Every thread is a `jobs.Job`: `_Listener` (TCP 47614, or any free
    port), `_Session` (one connection), `_Announcer` (UDP), `_Mdns`.

Nothing listens until this device is paired or showing a pairing code,
so nobody who never uses sync gets a firewall question.

Log lines start with `[sync]`.
"""

import hashlib
import json
import os
import queue
import select
import socket
import threading
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

import storage
import sync_core as core
from jobs import Job

PUSH_AFTER_MS = 2000
TICK_MS = 3000
FOUND_FOR = 45.0            # a device not heard of for this long is gone
PING_EVERY = 30.0
IDLE_LIMIT = 90.0
# A sync run (build 167, task 223): at launch, every few minutes (the
# interval setting) and on Sync now, this device looks for paired devices
# it isn't connected to and syncs with them. Between runs it only answers.
RUN_TICK_MS = 1000
RUN_RETRY = 3.0             # seconds between tries of a device in a run
RUN_LOOK_FOR = 25.0         # how long a run looks for a device
RUN_ART_FOR = 90.0          # how long it waits for pictures it asked for
RUN_SHOW_AT_LEAST = 0.8     # the spinner never just flickers


def _log(text):
    try:
        import crashlog
        crashlog.write("[sync] " + text)
    except Exception:                           # noqa: BLE001
        pass


_service = None


def service():
    """The window's SyncService, or None before it exists."""
    return _service


# ----------------------------------------------------------------------
# Threads
# ----------------------------------------------------------------------
class _Listener(Job):
    """Accepts connections from other devices."""

    LOOPS = True
    accepted = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.bind(("0.0.0.0", core.TCP_PORT))
        except OSError:
            self.sock.bind(("0.0.0.0", 0))
        self.sock.listen(8)
        self.sock.settimeout(1.0)
        self.port = self.sock.getsockname()[1]

    def work(self):
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(core.STEP_TIMEOUT)
            self.send(self.accepted, (conn, addr))
        try:
            self.sock.close()
        except OSError:
            pass

    def stop(self):
        self._stop.set()


class _Announcer(Job):
    """The UDP broadcast fallback (section 2)."""

    LOOPS = True
    found = pyqtSignal(str, str, str, int, bool)

    def __init__(self, me, name, port):
        super().__init__()
        self.me, self.name, self.port = me, name, port
        self.pair = False
        self._stop = threading.Event()
        self._now = threading.Event()

    def announce_now(self):
        self._now.set()

    def _message(self):
        return json.dumps({"app": core.APP_TAG, "v": core.PROTOCOL,
                           "id": self.me, "name": self.name,
                           "port": self.port,
                           "pair": 1 if self.pair else 0}).encode("utf-8")

    def work(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("", core.UDP_PORT))
        except OSError as exc:
            _log(f"broadcast: can't listen on UDP {core.UDP_PORT} ({exc})")
            sock.bind(("", 0))
        sock.settimeout(1.0)
        last = 0.0
        probe = json.dumps({"app": core.APP_TAG, "v": core.PROTOCOL,
                            "probe": True, "id": self.me}).encode("utf-8")
        while not self._stop.is_set():
            if self._now.is_set() or time.monotonic() - last > 10:
                # The first look, and when asked, also asks the others
                # to say where they are, so nobody waits 10 s.
                send = [self._message()] + \
                    ([probe] if self._now.is_set() or not last else [])
                self._now.clear()
                last = time.monotonic()
                for addr in core.broadcast_addresses():
                    for data in send:
                        try:
                            sock.sendto(data, (addr, core.UDP_PORT))
                        except OSError:
                            pass
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(1)
                continue
            if len(data) > 1024:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if not isinstance(msg, dict) or msg.get("app") != core.APP_TAG \
                    or msg.get("v") != core.PROTOCOL \
                    or msg.get("id") == self.me:
                continue
            if msg.get("probe"):
                try:
                    sock.sendto(self._message(), addr)
                except OSError:
                    pass
                continue
            try:
                port = int(msg.get("port"))
            except (TypeError, ValueError):
                continue
            pid = str(msg.get("id") or "")
            if len(pid) == 32 and 0 < port < 65536:
                self.send(self.found, pid, str(msg.get("name") or "")[:80],
                          addr[0], port, bool(msg.get("pair")))
        sock.close()

    def stop(self):
        self._stop.set()


class _Mdns(Job):
    """DNS-SD: advertise this device and browse for the others."""

    LOOPS = True
    found = pyqtSignal(str, str, str, int, bool)
    lost = pyqtSignal(str)

    def __init__(self, me, name, port):
        super().__init__()
        self.me, self.name, self.port = me, name, port
        self._stop = threading.Event()
        self._todo = queue.Queue()
        self._names = {}            # service name -> device id

    def set_pair(self, on):
        self._todo.put(("pair", bool(on)))

    def _info(self, pair, name=None):
        from zeroconf import ServiceInfo
        label = self.name.replace(".", " ")[:50] or "Frequencies"
        addrs = [socket.inet_aton(ip) for ip in core.local_ipv4s()]
        return ServiceInfo(
            core.SERVICE, name or f"{label} {self.me[:6]}.{core.SERVICE}",
            server=f"frequencies-{self.me[:12]}.local.",
            port=self.port, addresses=addrs or None,
            properties={"v": str(core.PROTOCOL), "id": self.me,
                        "pair": "1" if pair else "0"})

    def work(self):
        try:
            from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
        except Exception as exc:                # noqa: BLE001
            _log(f"mDNS: not available ({exc!r}); the broadcast still works")
            return
        try:
            zc = Zeroconf()
        except Exception as exc:                # noqa: BLE001
            _log(f"mDNS: couldn't start ({exc!r}); the broadcast still works")
            return
        info = self._info(False)
        try:
            zc.register_service(info, allow_name_change=True)
        except Exception as exc:                # noqa: BLE001
            _log(f"mDNS: couldn't advertise ({exc!r})")
            info = None

        def changed(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Removed:
                self._todo.put(("lost", name))
            else:
                self._todo.put(("look", name))

        browser = ServiceBrowser(zc, core.SERVICE, handlers=[changed])
        while not self._stop.is_set():
            try:
                what, arg = self._todo.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if what == "pair" and info is not None:
                    new = self._info(arg, info.name)
                    zc.update_service(new)
                    info = new
                elif what == "lost":
                    pid = self._names.pop(arg, None)
                    if pid:
                        self.send(self.lost, pid)
                elif what == "look":
                    found = zc.get_service_info(core.SERVICE, arg, 3000)
                    if found is None:
                        continue
                    props = {k.decode("utf-8", "replace"):
                             (v or b"").decode("utf-8", "replace")
                             for k, v in (found.properties or {}).items()}
                    pid = props.get("id", "")
                    if props.get("v") != str(core.PROTOCOL) or \
                            len(pid) != 32 or pid == self.me:
                        continue
                    self._names[arg] = pid
                    label = arg[:-len(core.SERVICE) - 1] \
                        if arg.endswith(core.SERVICE) else arg
                    label = label.rsplit(" ", 1)[0] if label.endswith(
                        pid[:6]) else label
                    for ip in found.parsed_addresses():
                        if ":" not in ip:
                            self.send(self.found, pid, label, ip,
                                      int(found.port), props.get("pair") == "1")
            except Exception as exc:            # noqa: BLE001
                _log(f"mDNS: {exc!r}")
        try:
            browser.cancel()
            if info is not None:
                zc.unregister_service(info)
            zc.close()
        except Exception:                       # noqa: BLE001
            pass

    def stop(self):
        self._stop.set()


class _Session(Job):
    """One connection: pairing, or a sync session (sections 4 and 5)."""

    LOOPS = True
    ready = pyqtSignal(object)
    message = pyqtSignal(object, object)
    ended = pyqtSignal(object, str)
    paired = pyqtSignal(object, str, str, bytes)
    pair_wrong = pyqtSignal(object, str)

    def __init__(self, svc, sock=None, addrs=(), peer_id="", key=b"",
                 code=""):
        super().__init__()
        self.me, self.name = svc.me, svc.name
        self.sock = sock
        self.addrs = list(addrs)
        self.peer_id = peer_id
        self.peer_name = ""
        self.key = key
        self.code = code            # set: this is an outgoing pairing
        self.incoming = sock is not None
        self._peers = dict(svc.peers)
        self._code_now = svc.current_code
        self._art = svc.art_index_ref
        self._art_ok = svc.art_allowed
        self._out = queue.Queue()
        self._stop = threading.Event()
        self.chan = None
        self.last_sent = None
        self.last_seen = None
        self.last_state = None      # the last state it sent (build 171)
        self.started_at = time.time()
        self.ready_at = None        # monotonic, when the session was up
        self.got_state = None       # monotonic, the last state that came
        self.held = None            # a state kept until "What to sync"

    def queue(self, msg):
        self._out.put(msg)

    def _connect(self):
        last = None
        for ip, port in self.addrs:
            try:
                sock = socket.create_connection((ip, port), timeout=3.0)
                sock.settimeout(core.STEP_TIMEOUT)
                return sock
            except OSError as exc:
                last = exc
        raise core.SyncError(f"couldn't connect ({last})")

    def work(self):
        try:
            self._run()
        except (core.SyncError, OSError, ValueError) as exc:
            if not self._stop.is_set():
                self.send(self.ended, self, str(exc) or type(exc).__name__)
                return
        finally:
            try:
                if self.sock is not None:
                    self.sock.close()
            except OSError:
                pass
        self.send(self.ended, self, "closed")

    def _run(self):
        if not self.incoming:
            self.sock = self._connect()
            if self.code:
                pid, pname, key = core.client_pair(self.sock, self.me,
                                                   self.name, self.code)
                self.send(self.paired, self, pid, pname, key)
                return
            self.chan = core.client_session(self.sock, self.me, self.name,
                                            self.key, self.peer_id)
        else:
            first = core.recv_json(self.sock)
            if first.get("t") == "pair":
                code = self._code_now()
                if not code:
                    return              # not showing a code: nothing
                try:
                    pid, pname, key = core.server_pair(
                        self.sock, first, self.me, self.name, code)
                except core.SyncError as exc:
                    if str(exc) == "wrong code":
                        self.send(self.pair_wrong, self,
                                  str(first.get("name") or ""))
                        return
                    raise
                self.send(self.paired, self, pid, pname, key)
                return
            self.chan = core.server_session(self.sock, first, self.me,
                                            self.name, self._peers)
            if self.chan is None:
                _log(f"an unpaired device ({str(first.get('name'))[:40]!r}) "
                     f"knocked; it got nothing")
                return
        self.peer_id = self.chan.peer_id
        self.peer_name = self.chan.peer_name
        # The 15 s limit is for each handshake step (section 3). Once the
        # session is open, a phone with hundreds of stations can take
        # longer than that to read a state or a batch of pictures; a 15 s
        # limit on each send dropped the session every 15 s with "timed
        # out", so the pictures never finished and the phone's spinner
        # never stopped (build 177, task 231). An open session is only
        # given up after IDLE_LIMIT without progress.
        self.sock.settimeout(IDLE_LIMIT)
        self.send(self.ready, self)
        self._loop()

    def _loop(self):
        """Read on this thread, write on another (build 178, task 231).

        One thread that only read between its own sends deadlocked with a
        big library: both devices sent their whole state (megabytes with
        800 stations) at the same moment, neither read until its own send
        was done, the socket buffers filled both ways and the session hung
        until it timed out, so the phone never got to ask for pictures.
        Reading never waits for a send now."""
        sock = self.sock
        self._done = threading.Event()
        self._write_error = None
        writer = threading.Thread(target=self._write_loop, daemon=True,
                                  name="sync-write")
        writer.start()
        last_in = time.monotonic()
        try:
            while not self._stop.is_set() and not self._done.is_set():
                if time.monotonic() - last_in > IDLE_LIMIT:
                    raise core.SyncError("the other device went quiet")
                readable, _, _ = select.select([sock], [], [], 0.25)
                if not readable:
                    continue
                msg = self.chan.recv()
                last_in = time.monotonic()
                kind = msg.get("t")
                if kind == "ping":
                    continue
                if kind == "bye":
                    return
                if kind == "art?":
                    # Answered by the writer, after what is queued before it.
                    self._out.put({"t": "_art?", "sha": msg.get("sha") or []})
                    continue
                self.send(self.message, self, msg)
                if kind == "forget":
                    return
        except (core.SyncError, OSError, ValueError):
            if self._write_error is not None:
                raise self._write_error
            raise
        finally:
            self._done.set()
            writer.join(2.0)
        if self._write_error is not None:
            raise self._write_error

    def _write_loop(self):
        """Send what is queued (and the pings) until the session ends."""
        last_out = time.monotonic()
        try:
            while not self._stop.is_set() and not self._done.is_set():
                try:
                    msg = self._out.get(timeout=0.25)
                except queue.Empty:
                    msg = None
                if msg is not None:
                    if msg.get("t") == "_art?":
                        self._send_art(msg.get("sha") or [])
                    else:
                        self.chan.send(msg)
                    last_out = time.monotonic()
                    if msg.get("t") in ("bye", "forget"):
                        self._done.set()
                        return
                if time.monotonic() - last_out > PING_EVERY:
                    self.chan.send({"t": "ping"})
                    last_out = time.monotonic()
        except (core.SyncError, OSError, ValueError) as exc:
            if not self._done.is_set() and not self._stop.is_set():
                self._write_error = exc
            self._done.set()
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _send_art(self, wanted):
        if not self._art_ok(self.peer_id):
            return                  # Station pictures are off for it
        index = self._art()
        items, size = [], 0
        sent = missing = total = 0
        for sha in wanted[:500]:
            path = index.get(str(sha))
            if not path:
                missing += 1
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                missing += 1
                continue
            art = core.art_of({"custom_art_path": path}) or {}
            text = core.b64(data)
            if len(text) > core.ART_BATCH:
                missing += 1
                continue
            # A few pictures per message (ART_SEND_BATCH, build 177).
            if items and size + len(text) > core.ART_SEND_BATCH:
                self.chan.send({"t": "art", "items": items})
                items, size = [], 0
            items.append({"sha": sha, "ext": art.get("ext", "png"),
                          "data": text})
            size += len(text)
            sent += 1
            total += len(data)
        if items:
            self.chan.send({"t": "art", "items": items})
        if sent or missing:
            _log(f"sent {sent} picture(s) to "
                 f"{(self.peer_name or 'the other device')!r} "
                 f"({total // 1024} KB)"
                 + (f"; {missing} asked for aren't here" if missing else ""))

    def stop(self):
        self._stop.set()
        try:
            if self.sock is not None:
                self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# ----------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------
class SyncService(QObject):
    """Owned by the window. Everything here runs on the GUI thread."""

    status = pyqtSignal()
    # Busy or the error text changed (the spinners and Settings' line).
    activity = pyqtSignal()
    pairing_done = pyqtSignal(str)
    # Paired from here (this device typed the code): choose what to sync
    # with it before the first sync (build 169, task 225). The peer id.
    choose_needed = pyqtSignal(str)
    pairing_failed = pyqtSignal(str)
    _poke = pyqtSignal()

    def __init__(self, window):
        super().__init__()
        global _service
        _service = self
        self.win = window
        self.lib = window.lib
        self.ok = core.crypto_ok()
        ident = core.identity()
        self.me, self.name = ident["id"], ident["name"]
        self.peers = core.load_peers()
        self.store = core.load_store()
        self.found = {}             # id -> {"name","addrs","pair","seen","first"}
        self.sessions = {}          # peer id -> _Session (ready)
        self._connecting = {}       # peer id -> _Session
        self._tried = {}            # peer id -> monotonic of the last try
        self._art_index = {}
        self.code = ""
        self._code_until = 0.0
        self._wrong = 0
        self._pair_job = None
        self._pair_code = ""
        self._pair_targets = []
        self._pair_deadline = 0.0
        self.listener = self.announcer = self.mdns = None
        self.last_text = ""
        self._run = None            # the sync run going on, if any
        self._pulse_until = 0.0
        self.error = ""             # what went wrong in the last Sync now
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self._interval_run)
        self._push_timer = QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(PUSH_AFTER_MS)
        self._push_timer.timeout.connect(self.push)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._poke.connect(self._push_timer.start)
        storage.add_save_hook(self._poke.emit)

    # -- starting --------------------------------------------------------

    def start(self):
        if not self.ok:
            _log("off: the cryptography package is missing")
            return
        _log(f"this device: {self.name!r} ({self.me[:8]}…), "
             f"{len(self.peers)} paired, "
             f"syncs {_every(self.interval_minutes())}")
        self._restart_interval()
        if self.peers:
            self._online()
            if self.interval_minutes():
                self.start_run(manual=False, why="launch")

    def _online(self):
        if self.listener is not None or not self.ok:
            return
        try:
            self.listener = _Listener()
        except OSError as exc:
            _log(f"can't listen: {exc}")
            self.listener = None
            return
        self.listener.accepted.connect(self._accepted)
        self.listener.start()
        port = self.listener.port
        # FREQ_SYNC_NO_BROADCAST / FREQ_SYNC_NO_MDNS: tests of each way
        # of finding devices on its own.
        self.announcer = _Announcer(self.me, self.name, port)
        self.announcer.found.connect(self._found)
        if not os.environ.get("FREQ_SYNC_NO_BROADCAST"):
            self.announcer.start()
        self.mdns = _Mdns(self.me, self.name, port)
        self.mdns.found.connect(self._found)
        self.mdns.lost.connect(self._lost)
        if not os.environ.get("FREQ_SYNC_NO_MDNS"):
            self.mdns.start()
        self._tick_timer.start()
        _log(f"listening on TCP {port}")

    def stop(self):
        for job in (self.listener, self.announcer, self.mdns):
            if job is not None:
                job.stop()
        for sess in list(self.sessions.values()) + \
                list(self._connecting.values()):
            sess.stop()

    # -- what the window asks --------------------------------------------

    def current_code(self):
        """The code on screen, if any (any thread)."""
        if self.code and time.time() < self._code_until:
            return self.code
        return ""

    def art_index_ref(self):
        return self._art_index

    def open_pairing(self):
        """Show a code. Returns (shown code, QR text)."""
        self._online()
        self.code = core.new_code()
        self._code_until = time.time() + core.CODE_LIFETIME
        self._wrong = 0
        self._set_pair_flag(True)
        port = self.listener.port if self.listener else core.TCP_PORT
        _log("pairing: showing a code")
        return (core.show_code(self.code),
                core.qr_text(self.me, self.name, self.code, port,
                             core.local_ipv4s()))

    def close_pairing(self):
        if self.code:
            _log("pairing: code closed")
        self.code = ""
        self._set_pair_flag(False)

    def _set_pair_flag(self, on):
        if self.announcer is not None:
            self.announcer.pair = on
            self.announcer.announce_now()
        if self.mdns is not None:
            self.mdns.set_pair(on)

    def pair_with(self, text):
        """Pair with the device showing the code typed in `text`."""
        code = core.clean_code(text)
        if not code:
            self.pairing_failed.emit(
                "That isn't a code. It's 10 letters and numbers, "
                "like ABCDE-12345.")
            return
        self._online()
        if self.announcer is not None:
            self.announcer.announce_now()
        self._pair_code = code
        self._pair_deadline = time.monotonic() + 8
        self._pair_try()

    def _pair_try(self):
        now = time.monotonic()
        targets = [(pid, f) for pid, f in self.found.items()
                   if f["pair"] and now - f["seen"] < FOUND_FOR]
        if not targets:
            if now < self._pair_deadline:
                QTimer.singleShot(1000, self._pair_try)
                return
            self.pairing_failed.emit(
                "Couldn't find a device showing that code on this network. "
                "Check both are on the same Wi-Fi and the code is still "
                "showing.")
            return
        addrs = [a for _pid, f in targets for a in f["addrs"]]
        job = _Session(self, addrs=addrs[:1], code=self._pair_code)
        self._pair_targets = addrs[1:]
        job.paired.connect(self._paired)
        job.ended.connect(self._pair_ended)
        self._pair_job = job
        job.start()

    def _pair_ended(self, job, reason):
        if job is not self._pair_job:
            return
        self._pair_job = None
        if reason == "closed":
            return
        if self._pair_targets:
            addr = self._pair_targets.pop(0)
            nxt = _Session(self, addrs=[addr], code=self._pair_code)
            nxt.paired.connect(self._paired)
            nxt.ended.connect(self._pair_ended)
            self._pair_job = nxt
            nxt.start()
            return
        _log(f"pairing failed: {reason}")
        self.pairing_failed.emit(
            "That code didn't match. Check it and try again."
            if reason == "wrong code" else
            "Couldn't pair with that device. Try again.")

    def _paired(self, job, pid, pname, key):
        before = self.peers.get(pid) or {}
        self.peers[pid] = {"name": pname or "another device", "key": key,
                           "paired_at": time.time(), "last_sync": 0}
        if "what" in before:
            self.peers[pid]["what"] = before["what"]    # asked again below
        if not job.incoming:
            # This device starts the sync: "What to sync" first (build
            # 169). Until then it sends and takes nothing from it. Only
            # desktops show codes, so the other one is a desktop.
            self.peers[pid]["ask"] = True
            self.peers[pid]["platform"] = "desktop"
        core.save_peers(self.peers)
        _log(f"paired with {pname!r} ({pid[:8]}…)")
        if job.incoming:
            self.close_pairing()
        # Old sessions to it used the old key.
        old = self.sessions.pop(pid, None)
        if old is not None:
            old.stop()
        self._tried.pop(pid, None)
        if not job.incoming:
            self._pair_job = None
        self.pairing_done.emit(pname or "the other device")
        self.status.emit()
        if self.peers[pid].get("ask"):
            self.choose_needed.emit(pid)
        else:
            self.start_run(manual=False, why="paired", peers={pid})

    def forget(self, pid):
        peer = self.peers.pop(pid, None)
        if peer is None:
            return
        core.save_peers(self.peers)
        sess = self.sessions.pop(pid, None)
        if sess is not None:
            sess.queue({"t": "forget"})
        _log(f"forgot {peer['name']!r}")
        self.status.emit()

    def rows(self):
        """[(peer id, name, status text)] for Settings."""
        out = []
        for pid, peer in sorted(self.peers.items(),
                                key=lambda p: p[1]["name"].lower()):
            out.append((pid, peer["name"], self._peer_text(pid, peer)))
        return out

    def _peer_text(self, pid, peer):
        if peer.get("ask"):
            return "Paired · choose what to sync to start syncing"
        last = peer.get("last_sync") or 0
        if pid in self.sessions:
            age = time.time() - last if last else None
            if age is not None and age < 60:
                return "Last synced just now"
            return ("Connected, last synced " + _ago(age)) if last \
                else "Connected"
        if last:
            return "Last synced " + _ago(time.time() - last) + \
                " · waiting for it on this network"
        return "Waiting for it on this network"

    def summary(self):
        """The one-line status under Settings' heading."""
        if not self.ok:
            return "Sync isn't available in this build."
        if not self.peers:
            return "Not paired with any device yet."
        best = max(self.peers.items(),
                   key=lambda p: p[1].get("last_sync") or 0)
        pid, peer = best
        if not peer.get("last_sync"):
            return f"Waiting for {peer['name']}"
        age = time.time() - peer["last_sync"]
        when = "just now" if age < 60 or pid in self.sessions else _ago(age)
        return f"Last synced with {peer['name']} {when}"

    # -- finding devices -------------------------------------------------

    def _found(self, pid, name, ip, port, pair):
        now = time.monotonic()
        entry = self.found.get(pid)
        if entry is None:
            entry = self.found[pid] = {"name": name, "addrs": [],
                                       "pair": pair, "seen": now,
                                       "first": now}
            if pid in self.peers:
                _log(f"found {self.peers[pid]['name']!r} at {ip}:{port}")
        entry["seen"] = now
        entry["pair"] = pair
        if name:
            entry["name"] = name
        addr = (ip, int(port))
        if addr in entry["addrs"]:
            entry["addrs"].remove(addr)
        entry["addrs"].insert(0, addr)
        del entry["addrs"][4:]

    def _lost(self, pid):
        entry = self.found.get(pid)
        if entry is not None:
            entry["seen"] = 0

    def _tick(self):
        now = time.monotonic()
        if self.code and not self.current_code():
            self.close_pairing()
        run = self._run
        for pid, peer in self.peers.items():
            if pid in self.sessions or pid in self._connecting:
                continue
            # Only a sync run calls a device (build 167): at launch, at
            # the interval and on Sync now. Between runs this device
            # answers whoever calls it.
            if run is None or pid not in run["peers"]:
                continue
            entry = self.found.get(pid)
            if entry is None or now - entry["seen"] > FOUND_FOR:
                continue
            if now - self._tried.get(pid, -1e9) < RUN_RETRY:
                continue
            self._tried[pid] = now
            job = _Session(self, addrs=entry["addrs"], peer_id=pid,
                           key=peer["key"])
            self._wire(job)
            self._connecting[pid] = job
            job.start()
        self._check_run()

    def _accepted(self, pair):
        conn, addr = pair
        job = _Session(self, sock=conn)
        self._wire(job)
        job.paired.connect(self._paired)
        job.pair_wrong.connect(self._pair_wrong)
        job.start()

    def _pair_wrong(self, _job, who):
        self._wrong += 1
        _log(f"pairing: a wrong code from {who[:40]!r} ({self._wrong})")
        if self._wrong >= core.CODE_TRIES:
            self.close_pairing()
            self.pairing_failed.emit(
                "Too many wrong codes. Close this window and open it again "
                "for a new code.")

    def _wire(self, job):
        job.ready.connect(self._ready)
        job.message.connect(self._message)
        job.ended.connect(self._ended)

    # -- sessions ----------------------------------------------------------

    def _ready(self, sess):
        pid = sess.peer_id
        self._connecting.pop(pid, None)
        if pid not in self.peers:
            sess.stop()
            return
        old = self.sessions.get(pid)
        if old is not None and old is not sess:
            # Two sessions with one device (both called at once): both
            # sides keep the one the smaller id called (section 2).
            def smaller_called(x):
                return (self.me < pid) != bool(x.incoming)
            if smaller_called(old) and not smaller_called(sess):
                sess.stop()
                return
            old.stop()
        sess.ready_at = time.monotonic()
        self.sessions[pid] = sess
        self._tried.pop(pid, None)
        _log(f"connected to {self.peers[pid]['name']!r} "
             f"({'they' if sess.incoming else 'we'} called)")
        name = sess.peer_name
        if name and name != self.peers[pid]["name"]:
            self.peers[pid]["name"] = name
            core.save_peers(self.peers)
        self.push(only=sess, force=True)
        self._ask_art(sess)
        self.activity.emit()        # a device answered: the spinner may turn
        if self._run is None:
            # The other device called: the spinner turns while the two
            # exchange what they have.
            self.start_run(manual=False, why="called", peers={pid})
        self.status.emit()
        self._check_run()

    def _ended(self, sess, reason):
        pid = sess.peer_id
        if self._connecting.get(pid) is sess:
            self._connecting.pop(pid, None)
            if pid in self.peers:
                _log(f"couldn't reach {self.peers[pid]['name']!r}: {reason}")
        if self.sessions.get(pid) is sess:
            self.sessions.pop(pid, None)
            if reason == "wrong key":
                _log(f"{self.peers.get(pid, {}).get('name')!r} has another "
                     f"key now: pair again")
            else:
                _log(f"session with "
                     f"{self.peers.get(pid, {}).get('name')!r} ended: "
                     f"{reason}")
            self._tried[pid] = time.monotonic()
            self.status.emit()
            self.activity.emit()
        run = self._run
        if run is not None and pid in run["peers"] and reason == "wrong key":
            run["errors"][pid] = (
                f"{self.peers.get(pid, {}).get('name', 'The other device')} "
                f"was paired again with something else. Forget it here and "
                f"pair again.")
            run["done"].add(pid)
        self._check_run()

    def _state(self):
        """Date this device's own edits (and update the picture index)."""
        lib = self.lib
        if core.observe(lib.stations, lib.settings, lib.history, self.store,
                        self.me):
            core.save_store(self.store)
        self._art_index = core.art_index(lib.stations)

    # -- what to sync (build 169, task 225) ------------------------------

    def what_for(self, pid):
        """This device's what-to-sync choice for one paired device
        (sync_core.clean_what). A pairing still waiting for its choice
        sends and takes nothing."""
        peer = self.peers.get(pid) or {}
        if peer.get("ask"):
            return core.nothing_what()
        return core.clean_what(peer.get("what"))

    def saved_what(self, pid):
        """The choice to show in the What to sync window."""
        return core.clean_what((self.peers.get(pid) or {}).get("what"))

    def asking(self, pid):
        return bool((self.peers.get(pid) or {}).get("ask"))

    def art_allowed(self, pid):
        """Any thread: are Station pictures on for this device?"""
        peer = self.peers.get(pid)
        return peer is not None and self.what_for(pid)["art"]

    def set_what(self, choices, sync=False):
        """Save {peer id: choice}. A pairing waiting for its choice starts
        syncing; a part turned on syncs at once (or everything does, with
        sync=True: the window's Sync button)."""
        start = set()
        for pid, what in choices.items():
            peer = self.peers.get(pid)
            if peer is None:
                continue
            what = core.clean_what(what)
            was = self.what_for(pid)
            peer["what"] = what
            asked = peer.pop("ask", False)
            turned_on = any(what[p] and not was[p] for p in core.WHAT_PARTS) \
                or any(what["groups"][g] and not was["groups"][g]
                       for g in what["groups"])
            off = [p for p in core.WHAT_PARTS if not what[p]] + \
                [g for g, on in what["groups"].items() if not on]
            _log(f"what to sync with {peer['name']!r}: "
                 f"{'everything' if not off else 'all but ' + ', '.join(off)}")
            sess = self.sessions.get(pid)
            if sess is not None:
                sess.last_sent = sess.last_seen = None
                held, sess.held = sess.held, None
                if held is None and turned_on:
                    # The other device already sent everything and won't
                    # send it again while nothing changes there: take the
                    # part just turned on from its last state (build 171).
                    held = sess.last_state
                if held is not None:
                    # As in any session: both states cross first, then
                    # each side merges. Sending after the merge would
                    # tombstone the other's twins before it saw ours.
                    self.push(only=sess, force=True)
                    self._take_state(sess, held)
            if asked or turned_on or sync:
                start.add(pid)
        core.save_peers(self.peers)
        self.status.emit()
        if sync:
            self._set_error("")
        if start:
            self.start_run(manual=sync, why="Sync now" if sync
                           else "what to sync", peers=start)

    def push(self, only=None, force=False):
        """Send this device's state wherever it says something new."""
        if not self.peers:
            return                  # never paired: nothing to date or send
        self._state()               # dated now, even with nobody to tell
        lib = self.lib
        for sess in ([only] if only else list(self.sessions.values())):
            state = core.build_state(lib.stations, lib.settings, lib.history,
                                     self.store, self.me,
                                     what=self.what_for(sess.peer_id))
            mine = core.digest(state)
            if not force and mine in (sess.last_sent, sess.last_seen):
                continue
            sess.last_sent = mine
            sess.queue(state)

    def _ask_art(self, sess):
        if not self.art_allowed(sess.peer_id):
            return
        wanted = sorted({p["sha"] for p in self.store["pending_art"].values()
                         if isinstance(p, dict) and p.get("sha")})
        if wanted:
            sess.queue({"t": "art?", "sha": wanted})

    def _message(self, sess, msg):
        pid = sess.peer_id
        kind = msg.get("t")
        if pid not in self.peers:
            return
        if kind == "forget":
            peer = self.peers.pop(pid, None)
            core.save_peers(self.peers)
            _log(f"{(peer or {}).get('name')!r} forgot this device")
            self.status.emit()
            return
        if kind == "state":
            self._take_state(sess, msg)
        elif kind == "art":
            self._take_art(msg.get("items") or [])

    def _take_state(self, sess, msg):
        lib = self.lib
        began = time.monotonic()
        peer = self.peers[sess.peer_id]
        if msg.get("platform") in ("desktop", "android") and \
                peer.get("platform") != msg["platform"]:
            peer["platform"] = msg["platform"]
            core.save_peers(self.peers)
        if peer.get("ask"):
            # Paired from here and What to sync isn't answered yet: keep
            # it for then (set_what), take nothing now.
            sess.held = msg
            sess.got_state = time.monotonic()
            self._check_run()
            return
        # Own edits first, so they're dated before anything is taken.
        if core.observe(lib.stations, lib.settings, lib.history, self.store,
                        self.me):
            pass
        res = core.merge(lib.stations, lib.settings, lib.history, self.store,
                         self.me, msg, storage.history_limit(lib.settings),
                         what=self.what_for(sess.peer_id))
        # Two default stations whose old address lacked its query string
        # (task 227): one station under two ids, joined here.
        joined = core.join_old_addresses(lib.stations, lib.settings,
                                         self.store, self.me)
        if joined.stations:
            res.stations = True
            res.renamed.update(joined.renamed)
            res.removed |= joined.removed
            res.settings |= joined.settings
        core.save_store(self.store)
        sess.last_seen = core.digest(msg)
        sess.last_state = msg
        sess.got_state = time.monotonic()
        peer["last_sync"] = time.time()
        core.save_peers(self.peers)
        if res.changed:
            health_moved = False
            for old, new in res.renamed.items():
                if old in lib.health:
                    # Keep whichever of the two was checked last.
                    was = lib.health.pop(old)
                    have = lib.health.get(new)
                    if have is None or (was.get("last_check") or 0) > \
                            (have.get("last_check") or 0):
                        lib.health[new] = was
                    health_moved = True
            for sid in res.removed:
                if lib.health.pop(sid, None) is not None:
                    health_moved = True
            if health_moved:
                try:
                    storage.save_health(lib.health)
                except Exception as exc:        # noqa: BLE001
                    _log(f"health: {exc!r}")
            if res.stations:
                lib.save_stations()
            if res.settings:
                lib.save_settings()
            if res.history:
                lib.save_history()
            self._apply(res)
            _log(f"took from {peer['name']!r}: "
                 f"{'stations ' if res.stations else ''}"
                 f"{'history ' if res.history else ''}"
                 f"{('settings ' + ', '.join(sorted(res.settings))) if res.settings else ''}"
                 f" ({(time.monotonic() - began) * 1000:.0f} ms)")
        if res.need_art:
            sess.queue({"t": "art?", "sha": sorted(res.need_art)})
        # What this device has that it didn't (and the others).
        self.push()
        if res.changed and self._run is None:
            self._pulse()
        self.status.emit()
        self._check_run()

    def _take_art(self, items):
        lib = self.lib
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            sha = str(item.get("sha") or "")
            waiting = [sid for sid, p in self.store["pending_art"].items()
                       if isinstance(p, dict) and p.get("sha") == sha]
            if not waiting:
                continue
            try:
                data = core.unb64(item.get("data"))
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("picture doesn't match its sha")
            except (ValueError, OSError) as exc:
                _log(f"a picture didn't arrive whole: {exc}")
                continue
            for sid in waiting:
                station = lib.station_by_id(sid)
                pend = self.store["pending_art"].pop(sid)
                if station is None:
                    continue
                try:
                    path = core.save_art(sha, str(item.get("ext") or "png"),
                                         data, sid)
                except (ValueError, OSError) as exc:
                    _log(f"a picture couldn't be saved: {exc}")
                    continue
                station["custom_art_path"] = path
                art = core.art_of(station)
                rec = self.store["st"].setdefault(sid, {}).get("art")
                if rec and art:
                    rec[2] = core.fingerprint(art)
                elif rec:
                    rec[2] = core.fingerprint(pend)
                changed = True
        if changed:
            core.save_store(self.store)
            lib.save_stations()
            self._apply_stations()
        self._check_run()

    # -- sync runs: launch, interval, Sync now (build 167) ---------------

    def interval_minutes(self):
        """Minutes between sync runs; 0 = only Sync now."""
        try:
            value = int(self.lib.settings.get(core.INTERVAL_KEY,
                                              core.INTERVAL_DEFAULT))
        except (TypeError, ValueError):
            value = core.INTERVAL_DEFAULT
        return value if value in core.INTERVAL_CHOICES else \
            core.INTERVAL_DEFAULT

    def set_interval(self, minutes):
        minutes = int(minutes)
        if minutes not in core.INTERVAL_CHOICES:
            return
        self.lib.settings[core.INTERVAL_KEY] = minutes
        self.lib.save_settings()
        _log(f"syncs {_every(minutes)}")
        self._restart_interval()

    def _restart_interval(self):
        self._interval_timer.stop()
        minutes = self.interval_minutes()
        if minutes:
            self._interval_timer.start(minutes * 60 * 1000)

    def _interval_run(self):
        if self.peers:
            self.start_run(manual=False, why="interval")

    @property
    def busy(self):
        # Only while a device that answered is being synced with (task
        # 245): looking for devices that aren't running Frequencies stays
        # quiet.
        run = self._run
        if run is not None and any(
                p in self.sessions for p in run["peers"]):
            return True
        return time.monotonic() < self._pulse_until

    def busy_text(self):
        """The spinner's tooltip."""
        run = self._run
        names = [self.peers[p]["name"] for p in sorted(run["peers"])
                 if p in self.peers and p in self.sessions] if run else []
        if not names:
            return "Syncing…"
        return "Syncing with " + _names(names) + "…"

    def sync_now(self):
        """Settings' Sync now: look for paired devices and sync at once."""
        if not self.ok:
            self._set_error("Sync isn't available in this build.")
            return
        if not self.peers:
            self._set_error("Pair a device first.")
            return
        self._set_error("")
        self.start_run(manual=True, why="Sync now")

    def start_run(self, manual, why="", peers=None):
        """Look for paired devices that aren't connected, call them, and
        exchange states; the spinner turns until each one has answered
        and its pictures have come (or the run gives up on it)."""
        if not self.peers or not self.ok:
            return
        self._online()
        now = time.monotonic()
        wanted = {p for p in set(peers or self.peers) & set(self.peers)
                  if not self.asking(p)}
        if not wanted:
            return
        run = self._run
        if run is None:
            run = self._run = {"manual": False, "why": why, "started": now,
                               "peers": set(), "done": set(), "errors": {},
                               "until": now, "art_until": now,
                               "show_until": now + RUN_SHOW_AT_LEAST}
            _log(f"sync run ({why}): {len(wanted)} device(s)")
        run["manual"] = run["manual"] or manual
        run["peers"] |= wanted
        run["until"] = max(run["until"], now + RUN_LOOK_FOR)
        run["art_until"] = max(run["art_until"], now + RUN_ART_FOR)
        for pid in wanted:
            run["done"].discard(pid)
            run["errors"].pop(pid, None)
            self._tried.pop(pid, None)
            sess = self.sessions.get(pid)
            if sess is not None and sess.got_state is not None:
                # Connected already: the two have been keeping each other
                # up to date. Say everything once more and count it done.
                self.push(only=sess, force=True)
                self._ask_art(sess)
                run["done"].add(pid)
        if self.announcer is not None:
            self.announcer.announce_now()   # and asks who's there
        self._tick_timer.setInterval(RUN_TICK_MS)
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        self.activity.emit()
        QTimer.singleShot(0, self._tick)
        QTimer.singleShot(int(RUN_SHOW_AT_LEAST * 1000) + 50,
                          self._check_run)

    def _check_run(self):
        run = self._run
        if run is None:
            return
        now = time.monotonic()
        for pid in list(run["peers"]):
            if pid not in self.peers:
                run["peers"].discard(pid)
                continue
            sess = self.sessions.get(pid)
            if sess is not None and sess.got_state is not None \
                    and sess.got_state >= run["started"]:
                run["done"].add(pid)
        waiting = run["peers"] - run["done"]
        art = bool(self.store["pending_art"]) and now < run["art_until"] \
            and any(p in self.sessions for p in run["done"])
        if not waiting and not art and now >= run["show_until"]:
            self._end_run([])
        elif waiting and now >= run["until"]:
            self._end_run(sorted(waiting))
        elif not waiting and art:
            # Only pictures left: a slower look is enough.
            QTimer.singleShot(1000, self._check_run)

    def _end_run(self, missing):
        run, self._run = self._run, None
        self._tick_timer.setInterval(TICK_MS)
        names = [self.peers[p]["name"] for p in missing if p in self.peers]
        errors = list(run["errors"].values())
        if names and run["manual"]:
            errors.append(f"{_names(names)} "
                          f"{'isn' if len(names) == 1 else 'aren'}'t running "
                          f"Frequencies right now. Open it there, on the "
                          f"same network.")
        if self.store["pending_art"] and now_art_late(run):
            _log(f"sync run: {len(self.store['pending_art'])} picture(s) "
                 f"still on the way")
        _log(f"sync run ({run['why']}) done"
             + (f"; not found: {', '.join(names)}" if names else ""))
        if run["manual"] or errors or not names:
            # A run that reached everyone also clears an old problem.
            self._set_error(" ".join(errors))
        self.activity.emit()
        self.status.emit()

    def _pulse(self):
        """Something came in between runs: a short turn of the spinner."""
        self._pulse_until = time.monotonic() + 1.0
        self.activity.emit()
        QTimer.singleShot(1050, self.activity.emit)

    def _set_error(self, text):
        if text != self.error:
            self.error = text
            if text:
                _log(f"shown: {text}")
            self.activity.emit()

    # -- the window --------------------------------------------------------

    def _apply(self, res):
        win = self.win
        if res.stations or res.history:
            self._apply_stations()
        keys = res.settings
        if "theme" in keys and hasattr(win, "apply_theme"):
            try:
                win.apply_theme(self.lib.settings.get("theme"))
            except Exception as exc:            # noqa: BLE001
                _log(f"theme: {exc!r}")
        if keys & {"accent_source", "accent_fixed"}:
            accent = getattr(win, "accent", None)
            if accent is not None:
                try:
                    accent.set_source(
                        self.lib.settings.get("accent_source", "artwork"),
                        self.lib.settings.get("accent_fixed"))
                except Exception as exc:        # noqa: BLE001
                    _log(f"accent: {exc!r}")
        if "clock_12h" in keys:
            try:
                import clock
                clock.set_mode(bool(self.lib.settings.get("clock_12h")))
            except Exception:                   # noqa: BLE001
                pass

    def _apply_stations(self):
        win = self.win
        if hasattr(win, "reload"):
            try:
                win.reload()
            except Exception as exc:            # noqa: BLE001
                _log(f"reload: {exc!r}")


def now_art_late(run):
    return time.monotonic() >= run["art_until"]


def _names(names, word="and"):
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {word} " + names[-1]


def _every(minutes):
    if not minutes:
        return "only when asked (Sync now)"
    if minutes % 60 == 0:
        h = minutes // 60
        return "every hour" if h == 1 else f"every {h} hours"
    return f"every {minutes} minutes"


def _ago(seconds):
    if seconds is None:
        return "a while ago"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"
