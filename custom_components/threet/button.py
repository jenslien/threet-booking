"""Cancel buttons, one per upcoming-booking slot."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ThreeTConfigEntry
from .const import BOOKING_SLOTS
from .entity import ThreeTAccountEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThreeTConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(CancelBookingButton(coordinator, i) for i in range(BOOKING_SLOTS))


class CancelBookingButton(ThreeTAccountEntity, ButtonEntity):
    """Cancels the booking shown in sensor "Booking N"."""

    _attr_icon = "mdi:calendar-remove"

    def __init__(self, coordinator, index: int) -> None:
        super().__init__(coordinator, f"cancel_booking_{index + 1}")
        self.index = index
        self._attr_name = f"Avbestill booking {index + 1}"

    @property
    def _booking(self):
        upcoming = self.coordinator.data.upcoming
        return upcoming[self.index] if self.index < len(upcoming) else None

    @property
    def available(self) -> bool:
        b = self._booking
        return super().available and b is not None and b.can_cancel

    @property
    def extra_state_attributes(self):
        b = self._booking
        if b is None or b.activity is None:
            return {}
        return {"booking_id": b.id, "title": f"{b.activity.name} – {b.activity.center_name}"}

    async def async_press(self) -> None:
        b = self._booking
        if b is not None:
            await self.coordinator.async_cancel(b.id)
