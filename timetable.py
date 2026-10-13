"""University timetable: which classes are on a given day.

The timetable is in UK time (University of Sheffield), so "today" and all class times
are worked out in Europe/London, whatever timezone the laptop is set to.
"""
import datetime
import os
import threading
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
TIMETABLE_FILE = os.getenv("JARVIS_TIMETABLE", os.path.join(HERE, "timetable.xlsx"))
UK = ZoneInfo("Europe/London")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_cache = None
_lock = threading.Lock()  # the window and the voice thread may both load at startup


def uk_now():
    return datetime.datetime.now(UK)


def load():
    """All classes as dicts, sorted by start time. Empty list if the file is missing."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read_file()
        return _cache


def _read_file():
    classes = []
    if not os.path.exists(TIMETABLE_FILE):
        return classes
    import openpyxl
    wb = openpyxl.load_workbook(TIMETABLE_FILE, read_only=True, data_only=True)
    for row in wb.active.iter_rows(values_only=True):
        if not row or not isinstance(row[0], str) or not isinstance(row[1], str):
            continue
        try:
            day = datetime.datetime.strptime(row[0].strip(), "%A, %d %B %Y").date()
            start_s, end_s = [t.strip() for t in row[1].split("-")]
            start = datetime.datetime.combine(day, datetime.datetime.strptime(start_s, "%H:%M").time())
            end = datetime.datetime.combine(day, datetime.datetime.strptime(end_s, "%H:%M").time())
        except ValueError:
            continue  # week headers, column headers, blank rows
        classes.append({
            "date": day,
            "start": start.replace(tzinfo=UK),
            "end": end.replace(tzinfo=UK),
            "module": (row[2] or "").strip(),
            "activity": (row[3] or "").strip(),
            "location": (row[4] or "").strip(),
            "lecturers": (row[5] or "").strip(),
        })
    classes.sort(key=lambda c: c["start"])
    return classes


def classes_on(day):
    return [c for c in load() if c["date"] == day]


def short_name(c):
    """'Additive Manufacturing - Principles and Applications' -> 'Additive Manufacturing'."""
    if c["activity"].lower() == "academic tutorial":
        return "Academic Tutorial"
    return c["module"].split(";")[0].split(" - ")[0].strip()


def building(c):
    return c["location"].split(",")[0].strip()


def spoken_time(t):
    """09:00 -> '9 AM', 14:30 -> '2:30 PM'."""
    hour = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    return "{} {}".format(hour, suffix) if t.minute == 0 else "{}:{:02d} {}".format(hour, t.minute, suffix)


def local_offset_note():
    """A reminder of the time difference, or '' when the laptop is already on UK time."""
    local = datetime.datetime.now().astimezone()
    diff = (local.utcoffset() - uk_now().utcoffset()).total_seconds() / 3600
    if diff == 0:
        return ""
    hours = int(abs(diff)) if diff == int(diff) else abs(diff)
    return "Those are UK times; you are {} hour{} {} of the UK.".format(
        hours, "" if hours == 1 else "s", "ahead" if diff > 0 else "behind")


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + ", and " + items[-1]


def _describe(c):
    activity = "" if short_name(c) == c["activity"] else " " + c["activity"].lower()
    return "{}{} at {} in {}".format(short_name(c), activity, spoken_time(c["start"]), building(c))


def briefing():
    """One or two spoken sentences about today's classes for the startup greeting."""
    if not load():
        return ""
    now = uk_now()
    today = classes_on(now.date())
    if not today:
        upcoming = [c for c in load() if c["start"] > now]
        if not upcoming:
            return ""
        nxt = upcoming[0]
        when = "tomorrow" if nxt["date"] == now.date() + datetime.timedelta(days=1) else "on " + nxt["start"].strftime("%A")
        note = local_offset_note()
        text = "You have no classes today. Your next one is {} {}.".format(_describe(nxt), when)
        return text + (" " + note if note else "")

    n = len(today)
    remaining = [c for c in today if c["end"] > now]
    plural = "class" if n == 1 else "classes"
    listed = _join([_describe(c) for c in today])  # always every class with its time
    if len(remaining) == n:
        text = "You have {} {} today: {}.".format(n, plural, listed)
    elif remaining:
        done = n - len(remaining)
        text = "You have {} {} today: {}. {} finished, and {} still to go; next is {}.".format(
            n, plural, listed, "One is" if done == 1 else "{} are".format(done), len(remaining),
            _describe(remaining[0]))
    else:
        text = "You had {} {} today: {}. {} finished.".format(
            n, plural, listed, "It's" if n == 1 else "They're all")
    note = local_offset_note()
    return text + (" " + note if note else "")


def resolve_day(day):
    """'today', 'tomorrow', 'yesterday', a weekday name or YYYY-MM-DD -> a date (UK calendar)."""
    today = uk_now().date()
    d = (day or "today").strip().lower()
    if d in ("", "today"):
        return today
    if d == "tomorrow":
        return today + datetime.timedelta(days=1)
    if d == "yesterday":
        return today - datetime.timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if name in d:
            ahead = (i - today.weekday()) % 7
            if "next" in d and ahead == 0:
                ahead = 7
            return today + datetime.timedelta(days=ahead)
    return datetime.datetime.strptime(d, "%Y-%m-%d").date()


def get_classes(day="today"):
    """Tool for the AI: full details of the classes on one day."""
    if not load():
        return "No timetable file found."
    try:
        date = resolve_day(day)
    except ValueError:
        return "Could not understand the day '{}'. Use today, tomorrow, a weekday or YYYY-MM-DD.".format(day)
    found = classes_on(date)
    header = "{} (UK times):".format(date.strftime("%A %d %B %Y"))
    if not found:
        first, last = load()[0]["date"], load()[-1]["date"]
        if not first <= date <= last:
            return "{} outside the timetable, which runs {} to {}.".format(
                header, first.strftime("%d %b"), last.strftime("%d %b %Y"))
        return header + " no classes."
    lines = [header, "{} class(es).".format(len(found))]
    local_tz = datetime.datetime.now().astimezone().tzinfo
    for c in found:
        local = c["start"].astimezone(local_tz)
        local_part = "" if local.utcoffset() == c["start"].utcoffset() else " (= {} local time)".format(
            local.strftime("%a %H:%M"))
        lines.append("- {}-{}{}: {} [{}] at {}; lecturer(s): {}".format(
            c["start"].strftime("%H:%M"), c["end"].strftime("%H:%M"), local_part, short_name(c),
            c["activity"], c["location"], c["lecturers"] or "not listed"))
    return "\n".join(lines)
