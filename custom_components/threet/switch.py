"""Switches for pausing a rule or skipping its next occurrence."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ThreeTConfigEntry
from .const import STATUS_BOOKING
from .entity import ThreeTRuleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThreeTConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = []
    for rule in coordinator.rules:
        entities += [RuleEnabledSwitch(coordinator, rule), RuleSkipNextSwitch(coordinator, rule)]
    async_add_entities(entities)


class RuleEnabledSwitch(ThreeTRuleEntity, SwitchEntity):
    """On = book automatically, off = paused."""

    _attr_name = "Aktiv"
    _attr_icon = "mdi:calendar-refresh"

    def __init__(self, coordinator, rule) -> None:
        super().__init__(coordinator, rule, "enabled")

    @property
    def is_on(self) -> bool:
        st = self.rule_state
        return st.enabled if st else True

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(self.rule.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_enabled(self.rule.id, False)


class RuleSkipNextSwitch(ThreeTRuleEntity, SwitchEntity):
    """On = don't book the next occurrence. Resets by itself once it has passed."""

    _attr_name = "Hopp over neste"
    _attr_icon = "mdi:debug-step-over"

    def __init__(self, coordinator, rule) -> None:
        super().__init__(coordinator, rule, "skip_next")

    @property
    def available(self) -> bool:
        st = self.rule_status
        return (
            super().available
            and st is not None
            and st.occurrence is not None
            and not st.predicted
            and st.status != STATUS_BOOKING
        )

    @property
    def is_on(self) -> bool:
        state, st = self.rule_state, self.rule_status
        return bool(
            state and st and st.occurrence and state.skip_activity_id == st.occurrence.id
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_skip(self.rule.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_skip(self.rule.id, False)
