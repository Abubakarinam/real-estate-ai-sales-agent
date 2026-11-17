"""Viewing calendar: free slots per consultant and bookings.

The demo keeps its own calendar in SQLite. To use Google Calendar or Cal.com in
production, replace `busy_slots()` and `book()` with their API calls."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db


def local_now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)


def busy_slots(agent: str) -> set[str]:
    rows = db.query("SELECT start_at FROM viewings WHERE agent=? AND status='booked'", (agent,))
    return {r["start_at"] for r in rows}


def free_slots(agent: str, from_date: str | None = None, days: int = 4, limit: int = 8) -> list[str]:
    now = local_now()
    min_start = now + timedelta(hours=3)  # needs a few hours' notice
    start_day = now.date()
    if from_date:
        try:
            start_day = max(start_day, datetime.strptime(from_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    busy = busy_slots(agent)
    out: list[str] = []
    day = start_day
    counted = 0
    while counted < days and len(out) < limit and (day - start_day).days < 21:
        if day.weekday() in config.VIEWING_DAYS:
            counted += 1
            for hour in config.VIEWING_HOURS:
                slot = datetime(day.year, day.month, day.day, hour)
                key = slot.strftime("%Y-%m-%dT%H:%M")
                if slot >= min_start and key not in busy:
                    out.append(key)
                    if len(out) >= limit:
                        break
        day += timedelta(days=1)
    return out


def pretty(slot: str) -> str:
    dt = datetime.strptime(slot, "%Y-%m-%dT%H:%M")
    return dt.strftime("%A %d %B, %H:%M").replace(" 0", " ")


def is_free(agent: str, slot: str) -> bool:
    try:
        dt = datetime.strptime(slot, "%Y-%m-%dT%H:%M")
    except ValueError:
        return False
    if dt.weekday() not in config.VIEWING_DAYS or dt.hour not in config.VIEWING_HOURS or dt.minute:
        return False
    if dt < local_now() + timedelta(hours=3):
        return False
    return slot not in busy_slots(agent)


def google_calendar_link(title: str, slot: str, details: str, location: str) -> str:
    from urllib.parse import urlencode

    start = datetime.strptime(slot, "%Y-%m-%dT%H:%M")
    end = start + timedelta(hours=1)
    fmt = "%Y%m%dT%H%M%S"
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": title, "details": details, "location": location,
        "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}", "ctz": config.TIMEZONE,
    })


def ics(title: str, slot: str, description: str, location: str, uid: str) -> str:
    start = datetime.strptime(slot, "%Y-%m-%dT%H:%M")
    end = start + timedelta(hours=1)
    f = "%Y%m%dT%H%M%S"
    esc = lambda s: s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//real-estate-ai-sales-agent//EN", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{datetime.utcnow().strftime(f)}Z",
        f"DTSTART;TZID={config.TIMEZONE}:{start.strftime(f)}", f"DTEND;TZID={config.TIMEZONE}:{end.strftime(f)}",
        f"SUMMARY:{esc(title)}", f"DESCRIPTION:{esc(description)}", f"LOCATION:{esc(location)}",
        "END:VEVENT", "END:VCALENDAR", "",
    ])
