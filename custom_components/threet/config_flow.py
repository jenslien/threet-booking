"""Config and options flow for the 3T integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .const import CONF_RULES, DOMAIN, HISTORY_DAYS
from .models import Rule
from .threet_api import GroupActivity, ThreeTAuthError, ThreeTClient, ThreeTError
from .threet_api.suggest import WEEKDAYS, Suggestion, suggest

MAX_SUGGESTIONS = 25

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _multi(options: list[SelectOptionDict]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
    )


def _dropdown(options: list[SelectOptionDict]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))


class ThreeTConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _async_login(self, username: str, password: str) -> tuple[str | None, dict]:
        client = ThreeTClient(
            async_get_clientsession(self.hass), username=username, password=password
        )
        try:
            await client.login()
            await client.get_my_bookings(dt_util.utcnow())
        except ThreeTAuthError:
            return None, {"base": "invalid_auth"}
        except (ThreeTError, aiohttp.ClientError, TimeoutError):
            return None, {"base": "cannot_connect"}
        return client.user_id or username.lower(), {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_id, errors = await self._async_login(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if not errors:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input, options={CONF_RULES: []}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            _, errors = await self._async_login(
                entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ThreeTOptionsFlow()


class ThreeTOptionsFlow(OptionsFlow):
    """Manage recurring bookings: from history suggestions, from the timetable, or remove."""

    def __init__(self) -> None:
        self._suggestions: list[Suggestion] = []
        self._classes: dict[str, GroupActivity] = {}
        self._center_id: str | None = None
        self._weekday: int = 0

    @property
    def _rules(self) -> list[Rule]:
        return [Rule.from_dict(r) for r in self.config_entry.options.get(CONF_RULES, [])]

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.hass.config.time_zone)

    def _client(self) -> ThreeTClient | None:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return None
        return self.config_entry.runtime_data.client

    def _save(self, rules: list[Rule]) -> ConfigFlowResult:
        return self.async_create_entry(
            data={**self.config_entry.options, CONF_RULES: [r.to_dict() for r in rules]}
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["suggestions", "add_center"]
        if self._rules:
            menu.append("remove")
        return self.async_show_menu(step_id="init", menu_options=menu)

    # -- suggestions from history ------------------------------------------

    async def async_step_suggestions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            chosen = [self._suggestions[int(i)] for i in user_input.get("slots", [])]
            return self._save(self._rules + [Rule.from_slot(s.slot) for s in chosen])

        if (client := self._client()) is None:
            return self.async_abort(reason="not_loaded")
        try:
            history = await client.get_my_bookings(
                dt_util.utcnow() - timedelta(days=HISTORY_DAYS)
            )
        except (ThreeTError, aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")

        existing = self._rules
        self._suggestions = [
            s
            for s in suggest(history, min_count=2, tz=self._tz)
            if not any(r.same_slot(Rule.from_slot(s.slot)) for r in existing)
        ][:MAX_SUGGESTIONS]
        if not self._suggestions:
            return self.async_abort(reason="no_suggestions")

        options = [
            SelectOptionDict(
                value=str(i),
                label=f"{s.slot}  ·  {s.count} ganger, sist {dt_util.as_local(s.last_seen):%d.%m.%Y}"
                + (f", {s.no_shows} no-show" if s.no_shows else ""),
            )
            for i, s in enumerate(self._suggestions)
        ]
        return self.async_show_form(
            step_id="suggestions",
            data_schema=vol.Schema({vol.Optional("slots", default=[]): _multi(options)}),
        )

    # -- pick from timetable -----------------------------------------------

    async def async_step_add_center(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._center_id = user_input["center"]
            self._weekday = int(user_input["weekday"])
            return await self.async_step_add_class()

        if (client := self._client()) is None:
            return self.async_abort(reason="not_loaded")
        try:
            centers = await client.get_centers()
        except (ThreeTError, aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")

        default_center = self._rules[0].center_id if self._rules else centers[0].id
        schema = vol.Schema(
            {
                vol.Required("center", default=default_center): _dropdown(
                    [SelectOptionDict(value=c.id, label=c.name) for c in centers]
                ),
                vol.Required("weekday", default="0"): _dropdown(
                    [
                        SelectOptionDict(value=str(i), label=d.capitalize())
                        for i, d in enumerate(WEEKDAYS)
                    ]
                ),
            }
        )
        return self.async_show_form(step_id="add_center", data_schema=schema)

    async def async_step_add_class(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new = [
                Rule.from_activity(self._classes[aid], self._tz)
                for aid in user_input.get("classes", [])
            ]
            existing = self._rules
            new = [r for r in new if not any(r.same_slot(e) for e in existing)]
            return self._save(existing + new)

        if (client := self._client()) is None:
            return self.async_abort(reason="not_loaded")
        # The coming occurrence of the weekday (tomorrow at the earliest, so the whole day is listed).
        tomorrow = dt_util.now(self._tz).date() + timedelta(days=1)
        day = tomorrow + timedelta(days=(self._weekday - tomorrow.weekday()) % 7)
        try:
            classes = await client.get_group_activities([self._center_id], day, day, take=200)
        except (ThreeTError, aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")
        if not classes:
            return self.async_abort(reason="no_classes")

        self._classes = {a.id: a for a in classes}
        options = [
            SelectOptionDict(
                value=a.id,
                label=f"{a.start.astimezone(self._tz):%H:%M}  {a.name}"
                + (f"  ({', '.join(a.instructors)})" if a.instructors else ""),
            )
            for a in sorted(classes, key=lambda a: a.start)
        ]
        return self.async_show_form(
            step_id="add_class",
            data_schema=vol.Schema({vol.Optional("classes", default=[]): _multi(options)}),
            description_placeholders={
                "day": f"{WEEKDAYS[self._weekday]} {day:%d.%m}",
                "center": classes[0].center_name,
            },
        )

    # -- remove ------------------------------------------------------------

    async def async_step_remove(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        rules = self._rules
        if user_input is not None:
            remove = set(user_input.get("rules", []))
            return self._save([r for r in rules if r.id not in remove])
        options = [SelectOptionDict(value=r.id, label=r.label) for r in rules]
        return self.async_show_form(
            step_id="remove",
            data_schema=vol.Schema({vol.Optional("rules", default=[]): _multi(options)}),
        )
