"""Derive recurring-booking suggestions from booking history.

A suggestion is a weekly slot: (activity name, center, weekday, local start time).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

from .client import Booking

OSLO = ZoneInfo("Europe/Oslo")
WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


@dataclass(frozen=True)
class SlotKey:
    activity_name: str
    center_id: str
    center_name: str
    weekday: int  # 0 = Monday
    time: str  # "HH:MM" local time
    activity_type_id: str | None = None

    def __str__(self) -> str:
        return f"{self.activity_name} – {self.center_name}, {WEEKDAYS[self.weekday]} {self.time}"


@dataclass(frozen=True)
class Suggestion:
    slot: SlotKey
    count: int
    no_shows: int
    last_seen: datetime


def slot_for(booking: Booking, tz: tzinfo = OSLO) -> SlotKey | None:
    a = booking.activity
    if a is None:
        return None
    local = a.start.astimezone(tz)
    return SlotKey(
        a.name, a.center_id, a.center_name, local.weekday(), local.strftime("%H:%M"), a.activity_type_id
    )


def suggest(bookings: list[Booking], min_count: int = 2, tz: tzinfo = OSLO) -> list[Suggestion]:
    """Group bookings into weekly slots, most frequent first.

    Waiting-list entries count as interest; no-shows are reported separately.
    """
    counts: dict[SlotKey, list[Booking]] = defaultdict(list)
    for b in bookings:
        if (key := slot_for(b, tz)) is not None:
            counts[key].append(b)
    result = [
        Suggestion(
            slot=key,
            count=len(items),
            no_shows=sum(1 for b in items if b.kind == "NoShowBooking"),
            last_seen=max(b.activity.start for b in items),
        )
        for key, items in counts.items()
        if len(items) >= min_count
    ]
    return sorted(result, key=lambda s: (-s.count, -s.last_seen.timestamp()))
