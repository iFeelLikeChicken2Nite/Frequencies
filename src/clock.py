"""Frequencies — how a time of day is written.

One setting, applied everywhere a person reads a time: the history, the
schedule and the health log. Kept in its own small module so every page
asks the same question and gets the same answer, rather than each one
deciding for itself and drifting apart.

Only the display changes. Times are still stored as 24-hour "HH:mm",
because that is what sorts and compares correctly — a schedule saved in
one mode must still work after switching to the other.
"""

import datetime

# Set from the user's settings at launch and whenever the toggle moves.
USE_12H = False


def set_mode(twelve_hour):
    global USE_12H
    USE_12H = bool(twelve_hour)


def fmt(when, seconds=False):
    """A datetime (or a timestamp) as the user wants to read it."""
    if when is None or when == "":
        return ""
    if not isinstance(when, datetime.datetime):
        try:
            when = datetime.datetime.fromtimestamp(float(when))
        except (TypeError, ValueError, OSError):
            return ""
    if USE_12H:
        text = when.strftime("%I:%M:%S %p" if seconds else "%I:%M %p")
        # "09:15 PM" reads as a timestamp; "9:15 PM" reads as a time.
        return text.lstrip("0") or text
    return when.strftime("%H:%M:%S" if seconds else "%H:%M")


def fmt_hour(hour):
    """An hour on its own, as the schedule's ruler shows it."""
    hour = int(hour) % 24
    if USE_12H:
        label = 12 if hour % 12 == 0 else hour % 12
        return f"{label} {'AM' if hour < 12 else 'PM'}"
    return f"{hour:02d}:00"


def qt_format():
    """The display format for Qt's time editors."""
    return "h:mm AP" if USE_12H else "HH:mm"
