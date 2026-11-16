"""Cancel buttons, one per upcoming-booking slot.

The button's name is the booking itself ("man. 23.11 kl. 19:00  Spinn 60 – 3T-Rosten"),
so a dashboard row shows the booking and the press action on the same line.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ThreeTConfigEntry
from .const import BOOKING_SLOTS
from .entity import ThreeTAccountEntity
from .models import booking_title, fmt_local
from .threet_api import Booking


async def async_setup_entry(
    hass: HomeAssistant, entry: ThreeTConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(CancelBookingButton(coordinator, i) for i in range(BOOKING_SLOTS))


class CancelBookingButton(ThreeTAccountEntity, ButtonEntity):
    """Cancels the n-th upcoming booking. Unavailable when the slot is empty."""

    _attr_icon = "mdi:calendar-remove"
    # The name changes with the booking, so don't derive the entity_id from it.
    _attr_has_entity_name = False

    def __init__(self, coordinator, index: int) -> None:
        super().__init__(coordinator, f"cancel_booking_{index + 1}")
        self.index = index
        self.entity_id = f"button.3t_avbestill_booking_{index + 1}"

    @property
    def _booking(self) -> Booking | None:
        upcoming = self.coordinator.data.upcoming
        return upcoming[self.index] if self.index < len(upcoming) else None

    @property
    def name(self) -> str:
        b = self._booking
        if b is None:
            return f"3T booking {self.index + 1} (ledig)"
        title = booking_title(b, self.coordinator.tz)
        return title if b.can_cancel else f"{title} · kan ikke avbestilles"

    @property
    def available(self) -> bool:
        return super().available and self._booking is not None

    @property
    def extra_state_attributes(self):
        b = self._booking
        if b is None or b.activity is None:
            return {}
        return {
            "booking_id": b.id,
            "title": f"{b.activity.name} – {b.activity.center_name}",
            "when": fmt_local(b.activity.start, self.coordinator.tz),
            "can_cancel": b.can_cancel,
        }

    async def async_press(self) -> None:
        b = self._booking
        if b is None:
            raise HomeAssistantError("Ingen booking på denne plassen")
        if not b.can_cancel:
            raise HomeAssistantError(
                "Kan ikke avbestilles lenger (fristen er 2 timer før timen starter)"
            )
        await self.coordinator.async_cancel(b.id)
