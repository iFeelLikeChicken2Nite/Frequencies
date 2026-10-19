"""Frequencies — finding out what is blocking the interface.

A stall is easy to feel and hard to locate: by the time you notice it, the
thing that caused it has finished. This watches from a second thread and,
whenever the GUI thread stops answering for longer than it should, writes
down exactly where it was.

Two parts, because either alone would lie:

  * a heartbeat on the GUI thread, updating a timestamp
  * a watcher thread comparing that timestamp against the clock

If the gap exceeds the threshold, the watcher takes the GUI thread's stack
with sys._current_frames — which works precisely because the watcher is
*not* that thread and is therefore still running.

One entry per stall, kept short so a log left running all night stays
readable (build 54, task 24):

  * The stack is taken the moment the stall is noticed (that is when the
    GUI thread is inside the slow call), but written once the stall is
    over, so the entry gives the stall's real length, not just "longer
    than 250 ms". A stall that lasts more than LONG seconds is written
    straight away, so a real hang still leaves its trace.
  * Only the innermost MAX_FRAMES calls are kept, with source text on
    the innermost SOURCE_FRAMES.
  * Every stall is numbered. A stall whose stack is the same as an
    earlier one gets a single line pointing back at it.
  * A stall with an open dialog or menu at the bottom of the stack is
    just someone looking at a dialog: one line, no trace.

Always on (build 92: the Settings switch is gone). Settings > Library and
tools > "Create log" writes the log out as a file. FREQ_NO_STALL_WATCH=1
switches the watch off.
"""

import os
import sys
import threading
import time
import traceback

from PyQt5.QtCore import QTimer


# Source text that means "a dialog or menu is open and waiting for the
# user". A stall whose innermost line is one of these is not a freeze.
_DIALOG_HINTS = (".exec_(", ".exec(", "QFileDialog.", "QMessageBox.",
                 "QInputDialog.", "QColorDialog.", "QFontDialog.")


class StallWatch:
    """Logs a stack trace whenever the GUI thread stops responding."""

    BEAT = 0.05                 # how often the GUI says it is alive
    THRESHOLD = 0.25            # a gap longer than this is worth a report
    LONG = 5.0                  # still blocked after this? write it now
    MAX_FRAMES = 12             # innermost calls kept per trace
    SOURCE_FRAMES = 4           # of those, how many show their source line

    def __init__(self, threshold=None, log=None, enabled=None):
        self.threshold = threshold or self.THRESHOLD
        # Asked each time rather than captured once, so the setting can be
        # switched on mid-session and take effect without a restart.
        self._enabled = enabled or (lambda: True)
        self._log = log or self._default_log
        self._beat = time.monotonic()
        self._gui_thread = threading.get_ident()

        self._count = 0             # stalls seen so far (their numbers)
        self._seen = {}             # stack -> [first stall's number, times]
        # The stall in progress, if any.
        self._stall_beat = None     # the last heartbeat before it
        self._stall = None          # (number, header, trace, repeat-of)
        self._written = False

        # False until the first heartbeat, i.e. until the event loop has
        # run once. A gap before that is the app starting up (the window
        # being built before it shows), not a freeze (build 69).
        self._looping = False

        self._timer = QTimer()
        self._timer.setInterval(int(self.BEAT * 1000))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._watcher = threading.Thread(target=self._watch, daemon=True,
                                         name="StallWatch")
        self._watcher.start()

    def _tick(self):
        self._beat = time.monotonic()
        self._looping = True

    def _watch(self):
        while True:
            time.sleep(self.BEAT)
            try:
                self._step()
            except Exception:               # noqa: BLE001
                # A diagnostic must never take anything down with it.
                self._stall_beat = None

    def _step(self):
        beat = self._beat
        gap = time.monotonic() - beat

        if self._stall_beat is None:
            # Not in a stall. Has one started?
            if gap >= self.threshold and self._enabled():
                self._stall_beat = beat
                self._stall = self._capture()
                self._written = False
            return

        if beat != self._stall_beat:
            # The GUI thread is back: the stall is over.
            length = beat - self._stall_beat
            if self._written:
                if not self._stall[1].startswith(("a dialog", "start-up")):
                    self._log(f"#{self._stall[0]} ended after "
                              f"{length * 1000:.0f} ms")
            else:
                self._write(f"{length * 1000:.0f} ms")
            self._stall_beat = None
            self._stall = None
            return

        if not self._written and gap >= self.LONG:
            # Still stuck. Write it now, in case it never comes back.
            self._write(f"over {gap:.0f} s so far")

    def _write(self, length):
        number, place, trace, repeat = self._stall
        head = f"#{number} GUI thread blocked for {length}"
        if trace is None:
            # A dialog or menu (or an unreadable stack): one line.
            self._log(f"{head} — {place}")
        elif repeat:
            first, times = repeat
            self._log(f"{head} at {place} — same stack as #{first} "
                      f"(seen {times} times)")
        else:
            self._log("\n".join([head] + trace))
        self._written = True

    # -- reading the GUI thread's stack ------------------------------------

    def _capture(self):
        """(number, place, trace lines or None, (first, times) or None)."""
        self._count += 1
        number = self._count
        frame = sys._current_frames().get(self._gui_thread)
        if frame is None:
            return number, "its stack could not be read", None, None
        stack = traceback.extract_stack(frame)
        if not stack:
            return number, "its stack could not be read", None, None

        inner = stack[-1]
        place = f"{os.path.basename(inner.filename)}:{inner.lineno} " \
                f"in {inner.name}"
        if not self._looping:
            # Before the event loop's first turn: the app starting.
            return (number, f"start-up: the window was being built "
                    f"before it showed ({place} at the time), "
                    f"not a freeze", None, None)
        if self._main_loop(stack):
            # The app's own event loop is the only Python on the stack,
            # so Qt itself is busy: drawing, laying out, the window
            # system. Until build 69 this matched ".exec_(" below and was
            # written off as "a dialog or menu was open" — three real
            # freezes in one overnight log were hidden that way (task 62).
            return number, (f"inside Qt itself (no Python code running: "
                            f"drawing, layout or the window system; "
                            f"{place})"), None, None
        if any(hint in (inner.line or "") for hint in _DIALOG_HINTS):
            return (number, f"a dialog or menu was open ({place}), "
                    "not a freeze", None, None)

        key = tuple((e.filename, e.lineno, e.name) for e in stack)
        seen = self._seen.get(key)
        if seen:
            seen[1] += 1
            return number, place, [], (seen[0], seen[1])
        self._seen[key] = [number, 1]

        # Innermost last, as a traceback reads, so the bottom line is the
        # call that is actually taking the time.
        kept = stack[-self.MAX_FRAMES:]
        lines = []
        if len(stack) > len(kept):
            lines.append(f"  ({len(stack) - len(kept)} outer calls left out)")
        first_source = len(kept) - self.SOURCE_FRAMES
        for i, entry in enumerate(kept):
            lines.append(f"  {os.path.basename(entry.filename)}:"
                         f"{entry.lineno} in {entry.name}")
            if entry.line and i >= first_source:
                lines.append(f"      {entry.line}")
        return number, place, lines, None

    @staticmethod
    def _main_loop(stack):
        """True when the innermost Python frame is the app's own main
        event loop (main.py's `app.exec_()`), not a dialog's or menu's.
        A dialog's exec_ is called from a handler, deeper down."""
        inner = stack[-1]
        return (os.path.basename(inner.filename) == "main.py"
                and inner.name == "main"
                and ".exec_(" in (inner.line or ""))

    @staticmethod
    def _default_log(text):
        try:
            import crashlog
            crashlog.write("[stall] " + text)
        except Exception:                       # noqa: BLE001
            pass
        print("[stall] " + text, file=sys.stderr)


def start(settings):
    """Always watching; reports only when asked to.

    The watch itself is a timer and a sleeping thread, which costs nothing
    worth measuring — so it runs from launch and the setting decides
    whether anything is written down, live (no restart needed).
    """
    off_by_env = bool(os.environ.get("FREQ_NO_STALL_WATCH"))

    def enabled():
        # Always on (build 92, task 124: the Settings switch became the
        # "Create log" button). A freeze is exactly when nobody has the
        # log turned on. Only the environment switch can turn it off.
        return not off_by_env

    return StallWatch(enabled=enabled)
