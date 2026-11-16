"""Base entities for the 3T integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThreeTCoordinator
from .models import Rule, RuleState, RuleStatus


class ThreeTAccountEntity(CoordinatorEntity[ThreeTCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ThreeTCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.device_prefix,
            manufacturer="3T-Treningssenter",
            entry_type=DeviceEntryType.SERVICE,
        )


class ThreeTRuleEntity(CoordinatorEntity[ThreeTCoordinator]):
    """Entity belonging to one recurring-booking rule (one device per rule)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ThreeTCoordinator, rule: Rule, key: str) -> None:
        super().__init__(coordinator)
        self.rule = rule
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{rule.id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{rule.id}")},
            name=f"{coordinator.device_prefix} fast: {rule.label}",
            manufacturer="3T-Treningssenter",
            model="Fast booking",
            entry_type=DeviceEntryType.SERVICE,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def rule_status(self) -> RuleStatus | None:
        return self.coordinator.statuses.get(self.rule.id)

    @property
    def rule_state(self) -> RuleState | None:
        return self.coordinator.rule_state.get(self.rule.id)
