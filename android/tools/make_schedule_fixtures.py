#!/usr/bin/env python3
"""Answers from the desktop's own schedule, clock and health code, for
core's ScheduleTest and HealthTest.

    python3 -I tools/make_schedule_fixtures.py <desktop source>/src core/src/test/resources/schedule

Writes answers.json: schedule.playing_now / live_now over a week of
times, Reminders' announcements minute by minute, clock.fmt / fmt_hour /
_shown_time in both clock styles, and storage's health functions
(mark_ok, mark_failed, humanize_age, describe_health, skipped_when_down).
PyQt5 isn't needed: its names are stubbed.
"""
import datetime as real_datetime
import json
import os
import sys
import types

src, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, src)


class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, name): return _Any()
    def __call__(self, *a, **k): return _Any()
    def __mro_entries__(self, bases): return (object,)


def _module(name):
    m = types.ModuleType(name)
    m.__getattr__ = lambda attr: _Any()
    sys.modules[name] = m
    return m


qt = _module("PyQt5")
for sub in ("QtCore", "QtGui", "QtWidgets", "sip"):
    setattr(qt, sub, _module("PyQt5." + sub))
sys.modules["PyQt5.QtCore"].pyqtSignal = lambda *a, **k: _Any()
theme = types.ModuleType("theme")
theme.T = {}
sys.modules["theme"] = theme

import clock      # noqa: E402
import schedule   # noqa: E402
import storage    # noqa: E402

os.makedirs(out, exist_ok=True)

SHOWS = {
    "morning": [{"name": "Morning Drive", "days": [0, 1, 2, 3, 4], "start": "06:00", "end": "09:30", "notify": True}],
    "late": [{"name": "Night Owls", "days": [4, 5], "start": "22:00", "end": "02:00"}],
    "noend": [{"name": "Lunch Hour", "days": [6], "start": "12:15"}],
    "midnight": [{"name": "Midnight Mix", "days": [0, 2, 4, 6], "start": "00:00", "end": "01:00", "notify": False}],
    "twoshows": [
        {"name": "Early", "days": [0, 1, 2, 3, 4, 5, 6], "start": "05:00", "end": "06:00"},
        {"name": "Allday", "days": [3], "start": "06:00", "end": "06:00"},
    ],
    "odd": [
        {"name": "Bad time", "days": [1], "start": "9", "end": "10:00"},
        {"name": "Text days", "days": ["2"], "start": "10:00", "end": "11:00"},
        {"name": "Float days", "days": [2.0], "start": "13:00", "end": "13:30"},
        {"days": [5], "start": "18:00", "end": "19:00"},
    ],
    "none": [],
}
stations = [{"id": key, "name": key.title(), "shows": shows} for key, shows in SHOWS.items()]

# A week from Monday 2026-11-02, every 7 minutes (hits starts, ends and past midnight).
start = real_datetime.datetime(2026, 11, 2, 0, 0)
times = [start + real_datetime.timedelta(minutes=7 * i) for i in range(0, 7 * 24 * 60 // 7 + 1)]
times += [real_datetime.datetime(2026, 11, 2, h, m) for h, m in ((6, 0), (9, 29), (9, 30), (5, 59))]
times += [real_datetime.datetime(2026, 11, 7, 1, 59), real_datetime.datetime(2026, 11, 7, 2, 0),
          real_datetime.datetime(2026, 11, 8, 12, 15), real_datetime.datetime(2026, 11, 8, 13, 14),
          real_datetime.datetime(2026, 11, 8, 13, 15), real_datetime.datetime(2026, 11, 5, 5, 59)]

playing = []
for t in times:
    row = {"t": t.strftime("%Y-%m-%dT%H:%M")}
    for st in stations:
        show = schedule.playing_now(st, t)
        row[st["id"]] = None if show is None else show.get("name", "")
    row["live"] = [s["id"] for s, _show in schedule.live_now(stations, t)]
    playing.append(row)


# Reminders: driven minute by minute with a stand-in clock.
class FakeLib:
    def __init__(self):
        self.stations = stations
        self.settings = {"show_reminders": True}


now = {"t": real_datetime.datetime(2026, 11, 2, 5, 58)}


class FakeDatetimeModule:
    class datetime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now["t"]


schedule.datetime = FakeDatetimeModule
announced = []
reminders = schedule.Reminders(FakeLib(), lambda st, show: announced.append(
    {"t": now["t"].strftime("%Y-%m-%dT%H:%M"), "station": st["id"], "show": show.get("name")}))
reminder_start = now["t"].strftime("%Y-%m-%dT%H:%M")
for i in range(1, 3 * 24 * 60):
    now["t"] = real_datetime.datetime(2026, 11, 2, 5, 58) + real_datetime.timedelta(minutes=i)
    reminders.check()
schedule.datetime = real_datetime

# clock.py
clocks = {}
for mode in (False, True):
    clock.set_mode(mode)
    key = "12h" if mode else "24h"
    clocks[key] = {
        "fmt": {f"{h:02d}:{m:02d}": clock.fmt(real_datetime.datetime(2000, 1, 1, h, m))
                for h in range(24) for m in (0, 5, 59)},
        "hour": {str(h): clock.fmt_hour(h) for h in range(24)},
        "shown": {text: schedule._shown_time(text) for text in
                  ("00:00", "07:05", "12:00", "12:30", "13:45", "23:59", "24:30", "9", "", "abc")},
    }
clock.set_mode(False)

# storage.py health
import time as real_time  # noqa: E402
clockvals = iter([1000.5, 2000.25, 3000.0, 4000.75, 5000.0, 6000.0])
storage.time.time = lambda: next(clockvals)
health = {"a": {"last_ok": None, "last_check": None, "fail_count": 0, "down": False, "last_error": "", "fails": 1}}
steps = []
storage.mark_failed(health, "a", "HTTP 404")
steps.append(json.loads(json.dumps(health)))
storage.mark_failed(health, "a", "")
steps.append(json.loads(json.dumps(health)))
storage.mark_ok(health, "a")
steps.append(json.loads(json.dumps(health)))
storage.mark_failed(health, "b", "ReadTimeout", down_after=3)
steps.append(json.loads(json.dumps(health)))
storage.time.time = real_time.time

ages = {str(s): storage.humanize_age(s) for s in (0, 59, 60, 3599, 3600, 86399, 86400, 172800, -5, 59.9)}
ages["None"] = storage.humanize_age(None)
storage.time.time = lambda: 100000.0
describe = {
    "up": storage.describe_health({"down": False, "last_ok": 5.0}),
    "never": storage.describe_health({"down": True, "last_ok": None}),
    "hours": storage.describe_health({"down": True, "last_ok": 100000.0 - 3 * 3600 - 5}),
    "empty": storage.describe_health({}),
}
storage.time.time = real_time.time
skipped = {
    "stream": storage.skipped_when_down({"kind": "stream", "url": "http://x"}),
    "ibroadcast_kind": storage.skipped_when_down({"kind": "ibroadcast"}),
    "ibroadcast_urls": storage.skipped_when_down({"ibroadcast_urls": ["http://y"]}),
    "twitch": storage.skipped_when_down({"kind": "twitch"}),
}

with open(os.path.join(out, "answers.json"), "w", encoding="utf-8") as f:
    json.dump({
        "stations": stations,
        "playing": playing,
        "reminders": {"start": reminder_start, "announced": announced},
        "clock": clocks,
        "health_steps": steps,
        "ages": ages,
        "describe": describe,
        "skipped_when_down": skipped,
    }, f, indent=1, ensure_ascii=False)
print(f"{len(playing)} times, {len(announced)} reminders, {len(steps)} health steps -> {out}/answers.json")
