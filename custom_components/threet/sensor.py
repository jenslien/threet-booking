"""Sensors for the 3T integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ThreeTConfigEntry
from .const import BOOKING_SLOTS, RULE_STATUSES
from .entity import ThreeTAccountEntity, ThreeTRuleEntity
from .models import fmt_local
from .threet_api import Booking


async def async_setup_entry(
    hass: HomeAssistant, entry: ThreeTConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [UpcomingCountSensor(coordinator)]
    entities += [BookingSlotSensor(coordinator, i) for i in range(BOOKING_SLOTS)]
    for rule in coordinator.rules:
        entities += [
            RuleStatusSensor(coordinator, rule),
            RuleNextClassSensor(coordinator, rule),
            RuleOpensSensor(coordinator, rule),
        ]
    async_add_entities(entities)


def booking_attributes(b: Booking) -> dict[str, Any]:
    a = b.activity
    return {
        "booking_id": b.id,
        "activity_id": b.activity_id,
        "name": a.name if a else None,
        "center": a.center_name if a else None,
        "start": a.start.isoformat() if a else None,
        "end": a.end.isoformat() if a and a.end else None,
        "instructors": a.instructors if a else [],
        "waiting_list": b.is_waiting_list,
        "waiting_list_position": b.waiting_list_position,
        "can_cancel": b.can_cancel,
        "cancel_code": b.cancel_code or None,
    }


class UpcomingCountSensor(ThreeTAccountEntity, SensorEntity):
    _attr_name = "Kommende bookinger"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "upcoming_count")

    @property
    def native_value(self) -> int:
        return sum(1 for b in self.coordinator.data.upcoming if not b.is_waiting_list)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        upcoming = self.coordinator.data.upcoming
        return {
            "waiting_list": sum(1 for b in upcoming if b.is_waiting_list),
            "max_bookings": 5,
            "bookings": [booking_attributes(b) for b in upcoming],
        }


class BookingSlotSensor(ThreeTAccountEntity, SensorEntity):
    """The n-th upcoming booking; pairs with the matching cancel button."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:dumbbell"

    def __init__(self, coordinator, index: int) -> None:
        super().__init__(coordinator, f"booking_{index + 1}")
        self.index = index
        self._attr_name = f"Booking {index + 1}"

    @property
    def booking(self) -> Booking | None:
        upcoming = self.coordinator.data.upcoming
        return upcoming[self.index] if self.index < len(upcoming) else None

    @property
    def native_value(self) -> datetime | None:
        b = self.booking
        return b.activity.start if b and b.activity else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self.booking
        if b is None:
            return {}
        attrs = booking_attributes(b)
        attrs["title"] = f"{attrs['name']} – {attrs['center']}"
        attrs["when"] = fmt_local(b.activity.start if b.activity else None, self.coordinator.tz)
        return attrs


class RuleStatusSensor(ThreeTRuleEntity, SensorEntity):
    _attr_name = "Status"
    _attr_translation_key = "rule_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = RULE_STATUSES
    _attr_icon = "mdi:calendar-sync"

    def __init__(self, coordinator, rule) -> None:
        super().__init__(coordinator, rule, "status")

    @property
    def native_value(self) -> str | None:
        st = self.rule_status
        return st.status if st else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.rule_status
        occ = st.occurrence if st else None
        tz = self.coordinator.tz
        booked = []
        for b in st.booked if st else []:
            text = fmt_local(b.occurrence.start, tz)
            if b.booking and b.booking.is_waiting_list:
                text += f" (venteliste #{b.booking.waiting_list_position})"
            booked.append(text)
        return {
            "rule": self.rule.label,
            "booked": booked,
            "next_class": fmt_local(occ.start, tz) if occ else None,
            "booking_opens": fmt_local(occ.bookable_earliest, tz) if occ else None,
            "predicted": st.predicted if st else False,
            "activity_id": occ.id if occ and not st.predicted else None,
            "start": occ.start.isoformat() if occ else None,
            "bookable_earliest": occ.bookable_earliest.isoformat()
            if occ and occ.bookable_earliest
            else None,
            "slots_available": occ.slots_available if occ else None,
            "slots_total": occ.slots_total if occ else None,
            "error": st.error if st else None,
        }


class RuleNextClassSensor(ThreeTRuleEntity, SensorEntity):
    _attr_name = "Neste time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, rule) -> None:
        super().__init__(coordinator, rule, "next_class")

    @property
    def native_value(self) -> datetime | None:
        st = self.rule_status
        return st.occurrence.start if st and st.occurrence else None


class RuleOpensSensor(ThreeTRuleEntity, SensorEntity):
    _attr_name = "Booking åpner"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator, rule) -> None:
        super().__init__(coordinator, rule, "booking_opens")

    @property
    def native_value(self) -> datetime | None:
        st = self.rule_status
        return st.occurrence.bookable_earliest if st and st.occurrence else None
