"""Frequencies — a Twitch channel's chat, read only (task 78).

Twitch lets anyone read a channel's chat without an account: connect to
its chat server, log in as "justinfanNNNNN" (Twitch's name for an
anonymous reader, who can never send anything), join "#<channel>", and
the messages arrive one per line, with the sender's name colour in the
line's tags.

`ChatReader` (build 77) does that for as long as a Twitch station plays
and hands the messages to the window in batches, a few times a second,
so a busy chat can't flood the window. It reconnects when the line
drops. It never sends a message and never changes playback.

Log (`[chat] …`): connecting / joined / connection lost, trying again in
N s / stopped, with how many messages came. Build 76's 20 s check
(`[twitch] chat check …`) is gone: these lines say the same and more.

The sandbox this was written in can't reach Twitch: tested only against
a fake chat server, so whether it reaches the real one is a guess until
the `[chat]` lines turn up in a log from the user's PC.
"""

import queue
import random
import socket
import ssl
import threading
import time

from PyQt5.QtCore import pyqtSignal

from jobs import Job

HOST = "irc.chat.twitch.tv"
PORT = 6697                     # TLS
BATCH_MS = 250                  # how often messages are handed over
BATCH_MAX = 80                  # a batch keeps only its newest this many
RETRY_S = (2, 4, 8, 15, 30)     # waits between reconnects


def _log(text):
    try:
        import crashlog
        crashlog.write("[chat] " + text)
    except Exception:                           # noqa: BLE001
        pass


def parse(line):
    """One line from the chat server → (command, params, tags, prefix).

    `@badge-info=;color=#FF0000;display-name=Someone :someone!someone@…
    PRIVMSG #channel :hello there` → ("PRIVMSG", ["#channel", "hello
    there"], {"color": "#FF0000", "display-name": "Someone", …},
    "someone!someone@…").
    """
    tags, prefix = {}, ""
    line = line.rstrip("\r\n")
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        for part in raw[1:].split(";"):
            key, _, value = part.partition("=")
            tags[key] = (value.replace("\\s", " ").replace("\\:", ";")
                         .replace("\\\\", "\\"))
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    params = line.split()
    command = params.pop(0) if params else ""
    if trailing is not None:
        params.append(trailing)
    return command.upper(), params, tags, prefix


def message(line):
    """(name, colour, text) for a chat message, else None."""
    command, params, tags, prefix = parse(line)
    if command != "PRIVMSG" or len(params) < 2:
        return None
    name = tags.get("display-name") or prefix.split("!", 1)[0]
    return name, tags.get("color", ""), params[1]


def chat_item(command, params, tags, prefix):
    """A message as the chat panel wants it, from one parsed PRIVMSG.

    {"id", "login", "name", "colour", "text", "action", "emotes",
    "user_id"}; "action" is a "/me" line (shown in italics). "emotes" is
    Twitch's tag saying where its emotes sit in the text, "user_id" the
    sender's Twitch number (PronounDB asks by it) — build 81, task 92.
    "badges" is Twitch's badges tag ("broadcaster/1,subscriber/12"),
    shown as pictures before the name (build 85, task 95).
    """
    login = prefix.split("!", 1)[0]
    text = params[1] if len(params) > 1 else ""
    action = text.startswith("\x01ACTION ") and text.endswith("\x01")
    if action:
        text = text[len("\x01ACTION "):-1]
    return {"id": tags.get("id", ""), "login": login,
            "name": tags.get("display-name") or login,
            "colour": tags.get("color", ""), "text": text,
            "action": action, "emotes": tags.get("emotes", ""),
            "user_id": tags.get("user-id", ""),
            "badges": tags.get("badges", "")}


class ChatReader(Job):
    """Read one channel's chat until stopped (build 77, task 78).

    `messages(seq, items)`: new messages, oldest first (chat_item dicts).
    `removed(seq, kind, key)`: a moderator removed something — kind
    "message" (key = message id), "user" (key = login: all their lines)
    or "all" (the chat was cleared).
    `auth(seq, login)`: who the reader is in: your Twitch login when
    signed in (build 82, task 92e), "" when reading anonymously.
    `note(seq, text)`: something Twitch said that belongs in the chat
    ("Your message was not sent because…"), or why a message wasn't sent.
    `say(text)` (any thread) sends a message as you, when signed in;
    it shows in the chat at once (Twitch doesn't send your own back).
    `me_changed(seq, me)`: what Twitch says about you (signed in): your
    emote sets ("emote-sets") and badges, for the emote picker (task 95).
    `room(seq, room_id)`: the channel's Twitch number, once joined
    (BetterTTV's channel emotes are asked for by it; build 81).
    `status(seq, text)`: "" once joined, else what is going on
    ("Connecting to chat…", "Chat dropped, trying again…").
    `seq` is the number the window gave the reader, so answers from a
    reader it has since replaced are easy to ignore.
    """

    LOOPS = True                # runs as long as the station plays

    messages = pyqtSignal(int, list)
    removed = pyqtSignal(int, str, str)
    me_changed = pyqtSignal(int, dict)      # task 95: your emote sets, badges
    status = pyqtSignal(int, str)
    room = pyqtSignal(int, str)
    auth = pyqtSignal(int, str)
    note = pyqtSignal(int, str)

    def __init__(self, seq, channel, cancel=None, host=HOST, port=PORT,
                 use_tls=True):
        super().__init__()
        self.seq = seq
        self.channel = (channel or "").strip().lower()
        self.cancel = cancel or threading.Event()
        self.host, self.port, self.use_tls = host, port, use_tls
        self.count = 0
        self.skipped = 0
        self.room_id = ""
        self.login = ""             # signed in as (build 82); "" = anonymous
        self.me = {}                # our name colour etc. from USERSTATE
        self._outbox = queue.Queue()
        self._auth_failures = 0
        self._joined_now = False

    def say(self, text):
        """Send `text` to the chat as you (any thread)."""
        text = " ".join((text or "").split())[:500]
        if text:
            self._outbox.put(text)
    def stop(self):
        self.cancel.set()

    def work(self):
        if not self.channel:
            return
        began = time.monotonic()
        tries, first = 0, True
        while not self.cancel.is_set():
            self.send(self.status, self.seq,
                      "Connecting to chat…" if first
                      else "Chat dropped, trying again…")
            first = False
            joined, why = self._session()
            self._joined_now = False
            self._drop_outbox("Not sent: the chat is reconnecting.")
            if self.cancel.is_set():
                break
            # A session that got in starts the waits over.
            tries = 1 if joined else tries + 1
            wait = RETRY_S[min(tries, len(RETRY_S)) - 1]
            _log(f"'{self.channel}': connection lost ({why}), trying "
                 f"again in {wait} s")
            self.send(self.status, self.seq,
                      "Chat dropped, trying again…")
            if self.cancel.wait(wait):
                break
        minutes = (time.monotonic() - began) / 60
        _log(f"'{self.channel}': stopped after {minutes:.1f} min, "
             f"{self.count} messages"
             f"{f' ({self.skipped} skipped in busy moments)' if self.skipped else ''}")

    def _drop_outbox(self, why):
        dropped = 0
        try:
            while True:
                self._outbox.get_nowait()
                dropped += 1
        except queue.Empty:
            pass
        if dropped:
            self.send(self.note, self.seq, why)

    def _credentials(self):
        """(login, token) to sign in with, or None to read anonymously.

        After Twitch refused the token once, it is renewed first; after
        twice, this session reads anonymously and says why (build 82).
        """
        if self._auth_failures >= 2:
            return None
        try:
            import twitchauth
            had = bool(twitchauth.signed_in_as())
            creds = twitchauth.chat_credentials(
                force_renew=self._auth_failures == 1)
            if had and creds is None:
                self.send(self.note, self.seq,
                          "Twitch ended your sign-in. Chat is read-only; "
                          "sign in again to write.")
            return creds
        except Exception as exc:                # noqa: BLE001
            _log(f"'{self.channel}': sign-in not used "
                 f"({type(exc).__name__}: {str(exc)[:80]})")
            return None

    def _session(self):
        """One connection: (joined, why it ended)."""
        creds = self._credentials()
        try:
            raw = socket.create_connection((self.host, self.port), timeout=8)
            if self.use_tls:
                raw = ssl.create_default_context().wrap_socket(
                    raw, server_hostname=self.host)
        except Exception as exc:                # noqa: BLE001
            return False, f"could not connect: {type(exc).__name__}: " \
                          f"{str(exc)[:80]}"
        joined, why = False, ""
        batch = []
        last_send = time.monotonic()
        last_heard = time.monotonic()
        try:
            raw.settimeout(BATCH_MS / 1000.0)
            if creds:
                # Signed in (build 82): the token is never logged.
                self.login = creds[0].lower()
                hello = (f"PASS oauth:{creds[1]}\r\n"
                         f"NICK {self.login}\r\n")
            else:
                self.login = ""
                hello = f"NICK justinfan{random.randint(10000, 99999)}\r\n"
            raw.sendall(("CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
                         + hello + f"JOIN #{self.channel}\r\n")
                        .encode("utf-8"))
            _log(f"'{self.channel}': connected to {self.host}, joining "
                 f"{'as ' + repr(self.login) if self.login else 'anonymously'}")
            buffer = b""
            while not self.cancel.is_set():
                try:
                    chunk = raw.recv(65536)
                except socket.timeout:
                    chunk = None
                if chunk == b"":
                    why = "the server closed the connection"
                    break
                if chunk:
                    last_heard = time.monotonic()
                    buffer += chunk
                    while b"\r\n" in buffer:
                        line, buffer = buffer.split(b"\r\n", 1)
                        item = self._line(raw, line.decode("utf-8",
                                                           "replace"))
                        if item == "joined" and not joined:
                            joined = True
                            self._joined_now = True
                            if self.login:
                                self._auth_failures = 0
                            _log(f"'{self.channel}': joined the chat")
                            self.send(self.status, self.seq, "")
                            self.send(self.auth, self.seq, self.login)
                        elif item == "reconnect":
                            why = "Twitch asked for a reconnect"
                            raise _Done()
                        elif isinstance(item, str) and \
                                item.startswith("notice:") and not joined:
                            why = "Twitch said: " + item[7:]
                            if self.login and _auth_refused(item[7:]):
                                self._auth_failures += 1
                                _log(f"'{self.channel}': Twitch refused the "
                                     f"sign-in ({item[7:]})"
                                     + (", renewing it" if
                                        self._auth_failures == 1 else
                                        ", reading anonymously"))
                                if self._auth_failures >= 2:
                                    self.send(self.note, self.seq,
                                              "Twitch refused your sign-in. "
                                              "Chat is read-only; sign in "
                                              "again to write.")
                            raise _Done()
                        elif isinstance(item, str) and \
                                item.startswith("notice:"):
                            self.send(self.note, self.seq, item[7:])
                        elif isinstance(item, dict):
                            batch.append(item)
                # Twitch pings every ~5 minutes; nothing for 7 is a dead
                # line that never said so.
                if time.monotonic() - last_heard > 420:
                    why = "nothing heard for 7 minutes"
                    break
                if joined:
                    self._send_waiting(raw, batch)
                if batch and (time.monotonic() - last_send) * 1000 >= BATCH_MS:
                    self._hand_over(batch)
                    batch = []
                    last_send = time.monotonic()
        except _Done:
            pass
        except Exception as exc:                # noqa: BLE001
            why = f"{type(exc).__name__}: {str(exc)[:80]}"
        finally:
            if batch and not self.cancel.is_set():
                self._hand_over(batch)
            try:
                raw.close()
            except Exception:                   # noqa: BLE001
                pass
        return joined, why or "stopped"

    def _send_waiting(self, raw, batch):
        """Messages you typed: sent, and shown at once (Twitch doesn't
        echo your own lines back)."""
        while True:
            try:
                text = self._outbox.get_nowait()
            except queue.Empty:
                return
            if not self.login:
                self.send(self.note, self.seq,
                          "Not sent: sign in to write in chat.")
                continue
            action = False
            if text.startswith("/me "):
                action, text = True, text[4:].strip()
            elif text.startswith("/") or text.startswith("."):
                self.send(self.note, self.seq,
                          "Not sent: only /me works here, not other chat "
                          "commands.")
                continue
            body = f"\x01ACTION {text}\x01" if action else text
            raw.sendall(f"PRIVMSG #{self.channel} :{body}\r\n"
                        .encode("utf-8"))
            self._sent = getattr(self, "_sent", 0) + 1
            if self._sent <= 3:
                _log(f"'{self.channel}': sent a message "
                     f"({len(text)} characters)")
            batch.append({"id": "", "login": self.login,
                          "name": self.me.get("display-name") or self.login,
                          "colour": self.me.get("color", ""), "text": text,
                          "action": action, "emotes": "",
                          "user_id": self.me.get("user-id", ""),
                          "badges": self.me.get("badges", ""),
                          "mine": True})

    def _hand_over(self, batch):
        if len(batch) > BATCH_MAX:
            self.skipped += len(batch) - BATCH_MAX
            batch = batch[-BATCH_MAX:]
        self.count += len(batch)
        self.send(self.messages, self.seq, batch)

    def _line(self, raw, text):
        """React to one line; returns what the session loop needs."""
        command, params, tags, prefix = parse(text)
        if command == "PING":
            raw.sendall(("PONG :" + (params[-1] if params
                                     else "tmi.twitch.tv")
                         + "\r\n").encode("utf-8"))
        elif command in ("JOIN", "ROOMSTATE", "366"):
            room_id = tags.get("room-id", "")
            if room_id and room_id != self.room_id:
                self.room_id = room_id
                _log(f"'{self.channel}': the channel's number is {room_id}")
                self.send(self.room, self.seq, room_id)
            return "joined"
        elif command == "PRIVMSG":
            return chat_item(command, params, tags, prefix)
        elif command == "CLEARMSG":
            self.send(self.removed, self.seq, "message",
                      tags.get("target-msg-id", ""))
        elif command == "CLEARCHAT":
            if len(params) > 1:
                self.send(self.removed, self.seq, "user", params[1].lower())
            else:
                self.send(self.removed, self.seq, "all", "")
        elif command in ("GLOBALUSERSTATE", "USERSTATE"):
            before = (self.me.get("emote-sets"), self.me.get("badges"))
            self.me.update({k: v for k, v in tags.items()
                            if k in ("display-name", "color", "user-id",
                                     "badges", "emote-sets")})
            # Your emote sets: the emote picker and ":" list (task 95).
            if (self.me.get("emote-sets"), self.me.get("badges")) != before:
                self.send(self.me_changed, self.seq, dict(self.me))
        elif command == "RECONNECT":
            return "reconnect"
        elif command == "NOTICE":
            return "notice:" + (params[-1][:80] if params else "?")
        return None


def _auth_refused(text):
    """Twitch's answer to a token it won't take."""
    text = (text or "").lower()
    return "authentication failed" in text or "improperly formatted" in text


class _Done(Exception):
    """Ends one session from inside the line loop."""
