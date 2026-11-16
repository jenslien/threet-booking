"""Calendar with booked classes and planned automatic bookings."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ThreeTConfigEntry
from .const import STATUS_PLANNED
from .entity import ThreeTAccountEntity
from .threet_api import GroupActivity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThreeTConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ThreeTCalendar(entry.runtime_data.coordinator)])


def _event(a: GroupActivity, summary: str, description: str) -> CalendarEvent:
    return CalendarEvent(
        start=a.start,
        end=a.end or a.start + timedelta(hours=1),
        summary=summary,
        location=a.center_name,
        description=description,
        uid=a.id,
    )


class ThreeTCalendar(ThreeTAccountEntity, CalendarEntity):
    _attr_name = "Bookinger"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "calendar")

    def _events(self) -> list[CalendarEvent]:
        events = []
        for b in self.coordinator.data.upcoming:
            a = b.activity
            suffix = f" (venteliste #{b.waiting_list_position})" if b.is_waiting_list else ""
            desc = ", ".join(a.instructors)
            events.append(_event(a, f"{a.name}{suffix}", desc))
        booked = {e.uid for e in events}
        for status in self.coordinator.statuses.values():
            occ = status.occurrence
            if status.status == STATUS_PLANNED and occ and occ.id not in booked:
                opens = occ.bookable_earliest
                desc = f"Bookes automatisk {dt_util.as_local(opens):%a %d.%m %H:%M}" if opens else ""
                events.append(_event(occ, f"{occ.name} (planlagt)", desc))
        return sorted(events, key=lambda e: e.start)

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.utcnow()
        return next((e for e in self._events() if e.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [e for e in self._events() if e.end > start_date and e.start < end_date]
