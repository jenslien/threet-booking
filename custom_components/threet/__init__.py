"""3T Treningssenter: automatic booking of group classes."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import ThreeTCoordinator
from .threet_api import ThreeTClient

PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ACTIVITY_ID = "activity_id"
ATTR_BOOKING_ID = "booking_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"


@dataclass
class ThreeTRuntime:
    client: ThreeTClient
    coordinator: ThreeTCoordinator


type ThreeTConfigEntry = ConfigEntry[ThreeTRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    def _coordinators(call: ServiceCall) -> list[ThreeTCoordinator]:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        coordinators = [
            e.runtime_data.coordinator
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
        ]
        if not coordinators:
            raise ServiceValidationError("No loaded 3T account found")
        return coordinators

    async def _book(call: ServiceCall) -> ServiceResponse:
        coordinators = _coordinators(call)
        if len(coordinators) > 1:
            raise ServiceValidationError("Several 3T accounts: choose one with config_entry_id")
        return await coordinators[0].async_book(call.data[ATTR_ACTIVITY_ID])

    async def _cancel(call: ServiceCall) -> None:
        booking_id = call.data[ATTR_BOOKING_ID]
        for coordinator in _coordinators(call):
            if coordinator.data and any(b.id == booking_id for b in coordinator.data.bookings):
                await coordinator.async_cancel(booking_id)
                return
        raise ServiceValidationError(f"Unknown booking {booking_id}")

    hass.services.async_register(
        DOMAIN,
        "book",
        _book,
        schema=vol.Schema(
            {vol.Required(ATTR_ACTIVITY_ID): cv.string, vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "cancel",
        _cancel,
        schema=vol.Schema(
            {vol.Required(ATTR_BOOKING_ID): cv.string, vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
        ),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ThreeTConfigEntry) -> bool:
    client = ThreeTClient(
        async_get_clientsession(hass),
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )
    coordinator = ThreeTCoordinator(hass, entry, client)
    await coordinator.async_load_state()
    await coordinator.async_config_entry_first_refresh()

    _remove_stale_rule_devices(hass, entry, {r.id for r in coordinator.rules})
    entry.runtime_data = ThreeTRuntime(client, coordinator)
    entry.async_on_unload(coordinator.cancel_all_timers)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThreeTConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ThreeTConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_stale_rule_devices(hass: HomeAssistant, entry: ConfigEntry, rule_ids: set[str]) -> None:
    registry = dr.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, ident in device.identifiers:
            if domain == DOMAIN and ident.startswith(prefix) and ident[len(prefix):] not in rule_ids:
                registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
