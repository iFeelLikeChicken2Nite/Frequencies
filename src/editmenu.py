"""Frequencies — the right-click menu on text fields, with a Paste that works.

On the user's desktop (KDE, Wayland) right-click > Paste in a text field
did nothing, while Ctrl+V in the same field worked (task 20).

The menu is a popup window of its own. While it is open the keyboard
belongs to it, and when it closes the keyboard goes back to the field.
Qt's own Paste reads the clipboard at that moment, after the menu has
gone, and on Wayland the clipboard offer it reads has just been swapped
for a new one, so it gets nothing. Ctrl+V never opens a popup, so it never
hits this.

So the clipboard is read when the menu opens (Qt reads it then anyway, to
decide whether Paste is enabled) and Paste inserts that text. If nothing
could be read then, it is read again if it changes while the menu is open,
and again a few times after Paste is clicked, once the field has its
keyboard back (task 41, build 59). Paste is never disabled. Each use logs
`[paste] …` lines.

Task 72 (build 72): in Edit this artist…'s Biography box both Paste and
Ctrl+V found the clipboard empty, while the station filter's worked. KDE's
compositor takes the clipboard offer away from the app at focus changes
and offers it again a moment later (seen here under KWin: an empty offer,
then the text, at each menu and dialog), and on the user's desktop the
offer had not come back by the time the dialog asked. So on Wayland the
last text the app was offered is remembered (`_Remembered`), and Paste or
Ctrl+V that finds the clipboard empty pastes that instead. An empty offer
doesn't wipe it (that's the focus churn); an offer of something that
isn't text (a picture) does. Logged as `[paste] …` lines.

Installed once, on the application:

    import editmenu
    editmenu.install(app)

Fields that make their own menu (a custom context-menu policy, or their
own contextMenuEvent, like the station filter's pills) are left alone.
"""

import time

from PyQt5.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QAbstractSpinBox, QApplication, QLineEdit,
                             QPlainTextEdit, QTextEdit)


def _wayland():
    from PyQt5.QtGui import QGuiApplication
    return QGuiApplication.platformName().startswith("wayland")


class _Remembered(QObject):
    """The last text the app was offered on the clipboard (task 72).

    Wayland only. Read when the offer arrives: an offer with text in it
    is read at once (a short read from the app that copied it); an empty
    offer is the compositor's focus churn and changes nothing; an offer
    of something else (a picture, files) forgets the text, so an old
    copy is never pasted over a new one.
    """

    def __init__(self, app):
        super().__init__(app)
        self.text = ""
        self.when = 0.0
        self.empty_offers = 0
        self._clipboard = app.clipboard()
        self._clipboard.dataChanged.connect(self._changed)
        self._changed()

    def _changed(self):
        try:
            mime = self._clipboard.mimeData()
            formats = mime.formats() if mime is not None else []
            if mime is not None and mime.hasText():
                text = mime.text()
                if text:
                    self.text, self.when = text, time.time()
                    self.empty_offers = 0
                    return
            if formats:
                # Something that isn't text was copied: nothing to paste.
                self.text, self.when = "", time.time()
            else:
                self.empty_offers += 1
        except RuntimeError:
            pass

    def describe(self):
        if not self.text:
            return "nothing remembered"
        return (f"remembered {len(self.text)} chars from "
                f"{time.time() - self.when:.1f} s ago, "
                f"{self.empty_offers} empty offer(s) since")


_remembered = None


def clipboard_text():
    """The clipboard's text, or on Wayland the last text it was offered
    when the compositor has just taken the offer away (task 72)."""
    text = QApplication.clipboard().text()
    if not text and _remembered is not None:
        return _remembered.text
    return text


def _default_menu(widget, base):
    """True when right-click on this widget gets Qt's standard menu."""
    if widget.contextMenuPolicy() != Qt.DefaultContextMenu:
        return False
    # A subclass with its own contextMenuEvent wants its own say.
    #
    # Looked up in the class dictionaries: PyQt hands out a new method
    # object on every attribute access, so comparing those with "is" is
    # never true.
    for klass in type(widget).__mro__:
        if klass is base:
            return True
        if "contextMenuEvent" in klass.__dict__:
            return False
    return True


def _paste_action(menu):
    """Qt's Paste item in a standard edit menu.

    Text boxes name it "edit-paste"; a QLineEdit gives its items no
    names, so it is found by its (translated) label instead.
    """
    label = QCoreApplication.translate("QLineEdit", "&Paste")
    for action in menu.actions():
        if action.objectName() == "edit-paste":
            return action
        if action.text().split("\t")[0] == label:
            return action
    return None


# How long to keep trying when the clipboard reads empty, in ms after the
# Paste click. On Wayland the app is only handed the clipboard when one of
# its windows gets the keyboard, so a right-click that is also the click
# that brought the window forward reads it before it has arrived.
RETRY_MS = (30, 100, 250, 500, 1000, 1600)


def _log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:  # noqa: BLE001
        pass


def _where(widget):
    """A short name for the field, for the log."""
    try:
        name = widget.objectName() or type(widget).__name__
        hint = ""
        if hasattr(widget, "placeholderText"):
            hint = (widget.placeholderText() or "")[:24]
        return f"{name}" + (f" '{hint}'" if hint else "")
    except RuntimeError:
        return "?"


def _show(menu, paste, insert, fallback, where, field=None):
    """Wire Paste to the clipboard, then pop the menu up.

    Task 41 (build 59): Paste is never disabled here. It used to follow
    what could be read when the menu opened — and when that read came
    back empty (Wayland, the window not focused yet), Paste was disabled.
    A disabled item looked exactly like an enabled one (the menu sheet
    had no :disabled colour), so clicking it did nothing at all: no text,
    no menu closing. Now the text is read when the menu opens, read again
    if the clipboard changes while it is open, and if it is still empty
    when Paste is clicked, read again a few times over the next moment,
    once the field has its window back.
    """
    clipboard = QApplication.clipboard()
    live = clipboard.text()
    state = {"text": live or clipboard_text()}
    try:
        active = field.window().isActiveWindow() if field is not None \
            else None
    except RuntimeError:
        active = None
    from PyQt5.QtGui import QGuiApplication
    extra = ""
    if not live and _remembered is not None:
        extra = f"; {_remembered.describe()}"
    _log(f"[paste] menu on {_where(field)}: clipboard had "
         f"{len(live)} chars (window active: {active}, "
         f"{QGuiApplication.platformName()}{extra})")

    def refresh(*_args):
        text = clipboard.text()
        if text:
            state["text"] = text

    if paste is not None:
        try:
            paste.triggered.disconnect()
        except (TypeError, RuntimeError):
            pass
        clipboard.dataChanged.connect(refresh)

        def unhook():
            try:
                clipboard.dataChanged.disconnect(refresh)
            except (TypeError, RuntimeError):
                pass

        menu.aboutToHide.connect(unhook)

        def do_paste(_checked=False):
            text = state["text"] or clipboard_text()
            if text:
                how = "" if text == clipboard.text() else \
                    " (the remembered copy: the clipboard read empty)"
                _log(f"[paste] pasted {len(text)} chars{how}")
                insert(text)
                return
            _retry(0)

        def _retry(step):
            if step >= len(RETRY_MS):
                _log("[paste] clipboard still empty "
                     f"{RETRY_MS[-1]} ms after Paste — nothing pasted "
                     "(try Ctrl+V)")
                return
            wait = RETRY_MS[step] - (RETRY_MS[step - 1] if step else 0)

            def look():
                try:
                    text = clipboard_text()
                except RuntimeError:
                    return
                if text:
                    _log(f"[paste] clipboard readable {RETRY_MS[step]} ms "
                         f"after Paste: pasted {len(text)} chars")
                    try:
                        insert(text)
                    except RuntimeError:
                        pass                # the field went away
                else:
                    _retry(step + 1)

            QTimer.singleShot(wait, look)

        paste.triggered.connect(do_paste)
        # Always clickable: the item only exists on editable fields, and
        # an empty read at this moment does not mean an empty clipboard.
        paste.setEnabled(True)
    # Qt's to delete when it closes, not Python's when this returns:
    # the menu has no parent, so PyQt would otherwise delete it as soon
    # as the last Python name for it went away, before it was seen.
    from PyQt5 import sip
    sip.transferto(menu, None)
    menu.setAttribute(Qt.WA_DeleteOnClose, True)
    menu.popup(where)


def _paste_key(obj, event):
    """Ctrl+V (or Shift+Insert) in a text field that finds the clipboard
    empty, on Wayland: paste the remembered copy (task 72). Anything else
    is left to Qt."""
    if _remembered is None or not event.matches(QKeySequence.Paste):
        return False
    if isinstance(obj, QLineEdit):
        insert = obj.insert
    elif isinstance(obj, (QPlainTextEdit, QTextEdit)):
        def insert(text):
            obj.insertPlainText(text)
            obj.ensureCursorVisible()
    else:
        return False
    if obj.isReadOnly() or QApplication.clipboard().text():
        return False
    text = _remembered.text
    if not text:
        _log(f"[paste] Ctrl+V on {_where(obj)}: clipboard empty, "
             f"nothing remembered either")
        return False
    _log(f"[paste] Ctrl+V on {_where(obj)}: clipboard read empty, pasted "
         f"the remembered copy ({_remembered.describe()})")
    insert(text)
    event.accept()
    return True


class _EditMenu(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            try:
                return _paste_key(obj, event)
            except RuntimeError:
                return False
        if event.type() != QEvent.ContextMenu:
            return False
        try:
            if isinstance(obj, QLineEdit):
                return self._line(obj, event)
            parent = obj.parent()
            if isinstance(parent, (QPlainTextEdit, QTextEdit)) \
                    and obj is parent.viewport():
                return self._text(parent, event)
        except RuntimeError:
            pass                            # widget went away mid-event
        return False

    @staticmethod
    def _line(field, event):
        if not _default_menu(field, QLineEdit):
            return False
        # A spin box's field: the spin box has its own menu (step up,
        # step down) and should keep it.
        if isinstance(field.parent(), QAbstractSpinBox):
            return False
        return show_line_menu(field, event)

    @staticmethod
    def _text(edit, event):
        base = QPlainTextEdit if isinstance(edit, QPlainTextEdit) \
            else QTextEdit
        if not _default_menu(edit, base):
            return False
        # Same as Qt's own: a read-only box with links gets no edit menu
        # worth changing.
        if edit.isReadOnly():
            return False
        menu = edit.createStandardContextMenu(event.pos())
        if menu is None:
            return False

        def insert(text):
            if not edit.isReadOnly():
                edit.insertPlainText(text)
                edit.ensureCursorVisible()

        def fallback():
            try:
                edit.paste()
            except RuntimeError:
                pass

        _show(menu, _paste_action(menu), insert, fallback,
              event.globalPos(), field=edit)
        event.accept()
        return True


def show_line_menu(field, event):
    """The standard menu for a QLineEdit, with the working Paste.

    For fields with their own contextMenuEvent (the station filter's
    pills) to call instead of QLineEdit's, when the click is on text.
    """
    menu = field.createStandardContextMenu()
    if menu is None:
        return False

    def insert(text):
        if not field.isReadOnly():
            field.insert(text)

    def fallback():
        try:
            field.paste()
        except RuntimeError:
            pass

    _show(menu, _paste_action(menu), insert, fallback, event.globalPos(),
          field=field)
    event.accept()
    return True


_filter = None


def install(app):
    """Once, on the application."""
    global _filter, _remembered
    if _filter is None:
        _filter = _EditMenu(app)
        app.installEventFilter(_filter)
    if _remembered is None and _wayland():
        _remembered = _Remembered(app)
    return _filter
