"""A desktop on the other end of the phone's sync tests (SyncDesktopTest).

    python3 tools/sync_desktop_peer.py <desktop source>/src <work folder> <code>

Runs the desktop's own sync code (sync_core: framing, pairing, crypto,
observe, build_state, merge; storage: the files) as the server side of
notes/sync-protocol.md, with its config folder in <work folder>/config.
The session handling copies desktop sync.py's SyncService
(_take_state, _take_art, push, _Session._send_art) without Qt.

It shows <code> for pairing, then serves sync sessions one after the
other until killed. Before each session it reads the library from disk
(so a test can edit the desktop's files between sessions, which the
desktop then dates as its own edits); after each one it saves and adds a
line to <work folder>/sessions.log.
"""

import json
import os
import socket
import sys
import time

SRC, WORK, CODE = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["XDG_CONFIG_HOME"] = os.path.join(WORK, "config")
os.environ["FREQ_SYNC_NAME"] = "Test Desktop"
os.makedirs(os.environ["XDG_CONFIG_HOME"], exist_ok=True)
sys.path.insert(0, SRC)

import storage                  # noqa: E402
import sync_core as core        # noqa: E402

LOG = os.path.join(WORK, "sessions.log")


def note(text):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")


class Lib:
    def __init__(self):
        self.stations = storage.load_stations()
        self.settings = storage.load_settings()
        self.history = storage.load_history()
        self.health = storage.load_health()

    def save(self):
        storage.save_stations(self.stations)
        storage.save_settings(self.settings)
        storage.save_history(self.history)

    def station_by_id(self, sid):
        return next((s for s in self.stations if s.get("id") == sid), None)


def serve(conn, ident, peers):
    me, name = ident["id"], ident["name"]
    first = core.recv_json(conn)
    if first.get("t") == "pair":
        try:
            pid, pname, key = core.server_pair(conn, first, me, name, CODE)
        except core.SyncError as exc:
            note(f"pair failed: {exc}")
            return
        peers[pid] = {"name": pname, "key": key, "paired_at": time.time(),
                      "last_sync": 0}
        core.save_peers(peers)
        note(f"paired {pname}")
        return
    chan = core.server_session(conn, first, me, name, peers)
    if chan is None:
        note("unpaired hello: nothing sent")
        return
    lib = Lib()
    store = core.load_store()
    art_index = {}

    def state():
        if core.observe(lib.stations, lib.settings, lib.history, store, me):
            core.save_store(store)
        art_index.clear()
        art_index.update(core.art_index(lib.stations))
        return core.build_state(lib.stations, lib.settings, lib.history,
                                store, me)

    sent = {"last": None, "seen": None}

    def push(force=False):
        st = state()
        mine = core.digest(st)
        if not force and mine in (sent["last"], sent["seen"]):
            return
        sent["last"] = mine
        chan.send(st)

    push(force=True)
    wanted = sorted({p["sha"] for p in store["pending_art"].values()
                     if isinstance(p, dict) and p.get("sha")})
    if wanted:
        chan.send({"t": "art?", "sha": wanted})
    took = []
    while True:
        try:
            msg = chan.recv()
        except core.SyncError as exc:
            note(f"ended: {exc}")
            break
        kind = msg.get("t")
        if kind == "bye":
            break
        if kind == "ping":
            continue
        if kind == "art?":
            items = []
            for sha in msg.get("sha") or []:
                path = art_index.get(str(sha))
                if not path:
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                art = core.art_of({"custom_art_path": path}) or {}
                items.append({"sha": sha, "ext": art.get("ext", "png"),
                              "data": core.b64(data)})
            if items:
                chan.send({"t": "art", "items": items})
            continue
        if kind == "state":
            core.observe(lib.stations, lib.settings, lib.history, store, me)
            res = core.merge(lib.stations, lib.settings, lib.history, store,
                             me, msg, storage.history_limit(lib.settings))
            core.save_store(store)
            sent["seen"] = core.digest(msg)
            if res.changed:
                took.append("stations" if res.stations else "")
                took.append("history" if res.history else "")
                took.extend(sorted(res.settings))
                lib.save()
            if res.need_art:
                chan.send({"t": "art?", "sha": sorted(res.need_art)})
            push()
            continue
        if kind == "art":
            for item in msg.get("items") or []:
                sha = str(item.get("sha") or "")
                waiting = [sid for sid, p in store["pending_art"].items()
                           if isinstance(p, dict) and p.get("sha") == sha]
                if not waiting:
                    continue
                path = core.save_art(sha, str(item.get("ext") or "png"),
                                     core.unb64(item.get("data")))
                for sid in waiting:
                    station = lib.station_by_id(sid)
                    pend = store["pending_art"].pop(sid)
                    if station is None:
                        continue
                    station["custom_art_path"] = path
                    art = core.art_of(station)
                    rec = store["st"].setdefault(sid, {}).get("art")
                    if rec and art:
                        rec[2] = core.fingerprint(art)
                    elif rec:
                        rec[2] = core.fingerprint(pend)
            core.save_store(store)
            lib.save()
            took.append("art")
    lib.save()
    note("session " + json.dumps(sorted(t for t in set(took) if t)))


def main():
    ident = core.identity()
    peers = core.load_peers()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    with open(os.path.join(WORK, "port"), "w") as f:
        f.write(f"{srv.getsockname()[1]} {ident['id']}")
    while True:
        conn, _addr = srv.accept()
        conn.settimeout(20)
        try:
            serve(conn, ident, peers)
        except (core.SyncError, OSError, ValueError) as exc:
            note(f"error: {exc!r}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
