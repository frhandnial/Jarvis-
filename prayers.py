"""Prayer times from the mosque's annual timetable (prayer_times.csv, converted from its PDF).

The table has no year, so the same file works every year. Its times are UK clock times that
switch to summer time on 28 March and back on 30 October. The real clock-change dates move a
little each year, so every time is converted to the actual UK time for its date; otherwise a few
days around each change would be an hour out.
"""
import csv
import datetime
import os
import threading

import timetable

HERE = os.path.dirname(os.path.abspath(__file__))
PRAYER_FILE = os.getenv("JARVIS_PRAYERS", os.path.join(HERE, "prayer_times.csv"))
UK = timetable.UK
PRAYERS = [("fajr", "Fajr"), ("sunrise", "Sunrise"), ("dhuhr", "Dhuhr"), ("asr", "Asr"),
           ("maghrib", "Maghrib"), ("isha", "Isha")]
SUMMER_ROWS = ((3, 28), (10, 29))  # first and last (month, day) rows printed in summer time

_cache = None
_lock = threading.Lock()


def load():
    """{(month, day): row} from the CSV. Empty if the file is missing."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = {}
            if os.path.exists(PRAYER_FILE):
                with open(PRAYER_FILE, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        d = datetime.datetime.strptime(row["date"], "%d-%b")
                        _cache[(d.month, d.day)] = row
        return _cache


def _to_uk(day, hhmm, summer):
    """A table time on `day` as the real UK time."""
    t = datetime.datetime.combine(day, datetime.datetime.strptime(hhmm, "%H:%M").time())
    printed = datetime.timezone(datetime.timedelta(hours=1 if summer else 0))
    return t.replace(tzinfo=printed).astimezone(UK)


def times_on(day):
    """The day's prayers in order: dicts with key, name, start and iqamah (None for sunrise).
    On Fridays Dhuhr becomes Jumu'ah with the Jumu'ah time as its iqamah."""
    table = load()
    row = table.get((day.month, day.day)) or table.get((day.month, day.day - 1))  # 29 Feb -> 28 Feb
    if not row:
        return []
    summer = SUMMER_ROWS[0] <= (day.month, day.day) <= SUMMER_ROWS[1]
    out = []
    for key, name in PRAYERS:
        iqamah = row.get(key + "_iqamah")
        if key == "dhuhr" and day.weekday() == 4:
            name, iqamah = "Jumu'ah", row["jummah"]
        out.append({"key": key, "name": name, "start": _to_uk(day, row[key], summer),
                    "iqamah": _to_uk(day, iqamah, summer) if iqamah else None})
    return out


def _done(p, now):
    return (p["iqamah"] or p["start"]) <= now


def upcoming(now=None):
    """(day shown, its prayers, the next prayer) - today until Isha's iqamah has passed, then tomorrow.
    The next prayer is the first one whose iqamah is still to come (sunrise isn't a prayer)."""
    now = now or timetable.uk_now()
    for day in (now.date(), now.date() + datetime.timedelta(days=1)):
        found = times_on(day)
        nxt = next((p for p in found if p["key"] != "sunrise" and not _done(p, now)), None)
        if nxt or not found:
            return day, found, nxt
    return day, found, None


def until(t, now):
    """Minutes from now to t as '2h 41m' / '15m'."""
    mins = max(0, int((t - now).total_seconds() + 59) // 60)
    return "{}h {:02d}m".format(mins // 60, mins % 60) if mins >= 60 else "{}m".format(mins)


def get_prayer_times(day="today"):
    """Tool for the AI: the mosque's prayer start and iqamah times for one day."""
    if not load():
        return "No prayer timetable file found (prayer_times.csv)."
    try:
        date = timetable.resolve_day(day)
    except ValueError:
        return "Could not understand the day '{}'. Use today, tomorrow, a weekday or YYYY-MM-DD.".format(day)
    lines = ["Prayer times for {} (UK time):".format(date.strftime("%A %d %B %Y"))]
    for p in times_on(date):
        iqamah = ", iqamah {}".format(p["iqamah"].strftime("%H:%M")) if p["iqamah"] else ""
        lines.append("- {}: starts {}{}".format(p["name"], p["start"].strftime("%H:%M"), iqamah))
    now = timetable.uk_now()
    if date == now.date():
        _, _, nxt = upcoming(now)
        if nxt:
            if nxt["start"] > now:
                lines.append("Next: {} starts in {}.".format(nxt["name"], until(nxt["start"], now)))
            else:
                lines.append("{} has started; iqamah in {}.".format(nxt["name"], until(nxt["iqamah"], now)))
    return "\n".join(lines)
