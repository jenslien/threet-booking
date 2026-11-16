"""Data coordinator and booking scheduler for the 3T integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BOOKING_OFFSET_SECONDS,
    CONF_RULES,
    DOMAIN,
    EVENT_BOOKED,
    EVENT_BOOKING_FAILED,
    LOOKAHEAD_DAYS,
    RETRY_DELAYS,
    STATUS_BOOKED,
    STATUS_BOOKING,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_FAILED,
    STATUS_NO_CLASS,
    STATUS_PAUSED,
    STATUS_PLANNED,
    STATUS_SKIPPED,
    STATUS_WAITLIST,
    TIMETABLE_INTERVAL,
    UPDATE_INTERVAL,
    WAITLIST_CLOSES_BEFORE,
    WAITLIST_POLLING,
)
from .models import Rule, RuleState, RuleStatus
from .threet_api import Booking, GroupActivity, ThreeTAuthError, ThreeTClient, ThreeTError

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

API_ERRORS = (ThreeTError, aiohttp.ClientError, TimeoutError)

_sleep = asyncio.sleep  # patched in tests


@dataclass
class ThreeTData:
    bookings: list[Booking] = field(default_factory=list)
    timetable: dict[str, list[GroupActivity]] = field(default_factory=dict)

    @property
    def upcoming(self) -> list[Booking]:
        now = dt_util.utcnow()
        return sorted(
            (
                b
                for b in self.bookings
                if b.kind != "NoShowBooking" and b.activity and b.activity.start > now
            ),
            key=lambda b: b.activity.start,
        )


class ThreeTCoordinator(DataUpdateCoordinator[ThreeTData]):
    """Fetches bookings + timetable and books rule occurrences when booking opens."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ThreeTClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.tz = ZoneInfo(hass.config.time_zone)
        self.rules = [Rule.from_dict(r) for r in entry.options.get(CONF_RULES, [])]
        self.rule_state: dict[str, RuleState] = {}
        self.statuses: dict[str, RuleStatus] = {}
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._timers: dict[str, CALLBACK_TYPE] = {}  # activity_id -> unsubscribe
        self._in_progress: set[str] = set()
        self._timetable: dict[str, list[GroupActivity]] = {}
        self._timetable_fetched: datetime | None = None

    # -- persistence -------------------------------------------------------

    async def async_load_state(self) -> None:
        stored = await self._store.async_load() or {}
        saved = stored.get("rules", {})
        self.rule_state = {
            r.id: RuleState(**saved[r.id]) if r.id in saved else RuleState() for r in self.rules
        }

    @callback
    def _state_data(self) -> dict:
        return {"rules": {rid: asdict(st) for rid, st in self.rule_state.items()}}

    async def _async_save_state(self) -> None:
        await self._store.async_save(self._state_data())

    # -- fetching ----------------------------------------------------------

    async def _async_update_data(self) -> ThreeTData:
        now = dt_util.utcnow()
        centers = sorted({r.center_id for r in self.rules})
        refresh_timetable = (
            self._timetable_fetched is None
            or now - self._timetable_fetched >= TIMETABLE_INTERVAL
            or set(centers) != set(self._timetable)
        )
        try:
            bookings = await self.client.get_my_bookings(now - timedelta(days=1))
            if refresh_timetable:
                today = now.astimezone(self.tz).date()
                timetable = {}
                for center_id in centers:
                    timetable[center_id] = await self.client.get_group_activities(
                        [center_id], today, today + timedelta(days=LOOKAHEAD_DAYS), take=500
                    )
                self._timetable, self._timetable_fetched = timetable, now
        except ThreeTAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except API_ERRORS as err:
            raise UpdateFailed(f"Error talking to 3T: {err}") from err

        data = ThreeTData(bookings=bookings, timetable=self._timetable)
        self._evaluate(data)
        self.update_interval = self._poll_interval(data, now)
        return data

    @staticmethod
    def _poll_interval(data: ThreeTData, now: datetime) -> timedelta:
        """Hourly, but faster while on a waiting list that can still move."""
        interval = UPDATE_INTERVAL
        for booking in data.upcoming:
            if not booking.is_waiting_list:
                continue
            start = booking.activity.start
            closes = start - WAITLIST_CLOSES_BEFORE
            if closes <= now:
                continue
            left = start - now
            tier = next((i for limit, i in WAITLIST_POLLING if left <= limit), UPDATE_INTERVAL)
            # Wake up exactly when the next (faster) tier starts or the waiting list closes.
            boundaries = [start - limit for limit, _ in WAITLIST_POLLING] + [closes]
            if (nxt := min((b for b in boundaries if b > now), default=None)) is not None:
                tier = min(tier, max(nxt - now, timedelta(seconds=30)))
            interval = min(interval, tier)
        return interval

    async def async_refresh_now(self) -> None:
        """Manual refresh (bookings only; the timetable keeps its 6 h cache)."""
        await self.async_request_refresh()

    # -- rule evaluation and scheduling ------------------------------------

    def _occurrences(self, rule: Rule, data: ThreeTData) -> list[GroupActivity]:
        now = dt_util.utcnow()
        return sorted(
            (
                a
                for a in data.timetable.get(rule.center_id, [])
                if a.start > now and rule.matches(a, self.tz)
            ),
            key=lambda a: a.start,
        )

    @staticmethod
    def _predict_next_week(occ: GroupActivity) -> GroupActivity:
        """The following week's occurrence, before it shows up in the timetable."""
        week = timedelta(days=7)
        start = occ.start + week
        return replace(
            occ,
            id=f"predicted-{occ.id}",
            start=start,
            end=occ.end + week if occ.end else None,
            bookable_earliest=occ.bookable_earliest + week if occ.bookable_earliest else None,
            bookable_latest=occ.bookable_latest + week if occ.bookable_latest else None,
            slots_available=None,
            raw_start=start.isoformat(),
        )

    @callback
    def _evaluate(self, data: ThreeTData) -> None:
        """Compute rule statuses and (re)schedule a booking for every open-able occurrence."""
        now = dt_util.utcnow()
        booked = {b.activity_id: b for b in data.bookings if b.kind != "NoShowBooking"}
        wanted: dict[str, tuple[str, GroupActivity]] = {}

        for rule in self.rules:
            state = self.rule_state.setdefault(rule.id, RuleState())
            # Forget handled occurrences that are long gone.
            state.handled = {
                aid: h
                for aid, h in state.handled.items()
                if dt_util.parse_datetime(h["start"]) > now - timedelta(days=1)
            }
            occs = self._occurrences(rule, data)
            done: list[RuleStatus] = []
            pending: list[RuleStatus] = []
            for occ in occs:
                st = self._status_for(rule, state, occ, booked, now)
                (done if st.status in (STATUS_BOOKED, STATUS_WAITLIST) else pending).append(st)
                if st.status == STATUS_PLANNED:
                    wanted[occ.id] = (rule.id, occ)

            # Show the next occurrence that still needs (or awaits) booking; occurrences the
            # user cancelled or that closed are history. Fall back to a prediction for next week.
            display = next(
                (st for st in pending if st.status not in (STATUS_CANCELLED, STATUS_CLOSED)), None
            )
            if display is None and occs:
                display = RuleStatus(
                    STATUS_PLANNED if state.enabled else STATUS_PAUSED,
                    self._predict_next_week(occs[-1]),
                    predicted=True,
                )
            if display is None:
                display = RuleStatus(STATUS_NO_CLASS)
            display.booked = done
            self.statuses[rule.id] = display

        for activity_id in set(self._timers) - set(wanted):
            self._cancel_timer(activity_id)
        for rule_id, occ in wanted.values():
            self._schedule(rule_id, occ, now)

    def _status_for(
        self,
        rule: Rule,
        state: RuleState,
        occ: GroupActivity,
        booked: dict[str, Booking],
        now: datetime,
    ) -> RuleStatus:
        if booking := booked.get(occ.id):
            # Remember it, so a later cancellation (here or on 3t.no) is never auto-rebooked.
            if state.handled.get(occ.id, {}).get("result") != "seen_booked":
                state.handled[occ.id] = {"start": occ.start.isoformat(), "result": "seen_booked"}
                self._store.async_delay_save(self._state_data, 5)
            status = STATUS_WAITLIST if booking.is_waiting_list else STATUS_BOOKED
            return RuleStatus(status, occ, booking)
        if occ.id in self._in_progress:
            return RuleStatus(STATUS_BOOKING, occ)
        if handled := state.handled.get(occ.id):
            if handled["result"] in ("booked", "waitlist"):
                # Booked by us, not yet visible in the bookings list (refresh pending).
                waitlist = handled["result"] == "waitlist"
                return RuleStatus(STATUS_WAITLIST if waitlist else STATUS_BOOKED, occ)
            if handled["result"] == "seen_booked":
                # Booked earlier but no longer booked: the user cancelled. Don't rebook.
                return RuleStatus(STATUS_CANCELLED, occ)
        if not state.enabled:
            return RuleStatus(STATUS_PAUSED, occ)
        if state.skip_activity_id == occ.id:
            return RuleStatus(STATUS_SKIPPED, occ)
        if handled and handled["result"] == "failed":
            return RuleStatus(STATUS_FAILED, occ, error=handled.get("error"))
        if occ.bookable_latest and now > occ.bookable_latest:
            return RuleStatus(STATUS_CLOSED, occ)
        return RuleStatus(STATUS_PLANNED, occ)

    @callback
    def _schedule(self, rule_id: str, occ: GroupActivity, now: datetime) -> None:
        if occ.id in self._timers or occ.id in self._in_progress:
            return
        opens = occ.bookable_earliest or now
        when = opens + timedelta(seconds=BOOKING_OFFSET_SECONDS)
        if when <= now:
            self._start_booking(rule_id, occ)
            return

        @callback
        def _fire(_now: datetime) -> None:
            self._timers.pop(occ.id, None)
            self._start_booking(rule_id, occ)
            if self.data is not None:
                self._evaluate(self.data)
            self.async_update_listeners()

        _LOGGER.debug("Scheduling %s at %s", occ.id, when)
        self._timers[occ.id] = async_track_point_in_utc_time(self.hass, _fire, when)

    @callback
    def _cancel_timer(self, activity_id: str) -> None:
        if unsub := self._timers.pop(activity_id, None):
            unsub()

    @callback
    def cancel_all_timers(self) -> None:
        for activity_id in list(self._timers):
            self._cancel_timer(activity_id)

    @callback
    def _start_booking(self, rule_id: str, occ: GroupActivity) -> None:
        if occ.id in self._in_progress:
            return
        self._in_progress.add(occ.id)
        self.config_entry.async_create_background_task(
            self.hass, self._async_book(rule_id, occ), f"{DOMAIN}_book_{occ.id}"
        )

    async def _async_book(self, rule_id: str, occ: GroupActivity) -> None:
        rule = next((r for r in self.rules if r.id == rule_id), None)
        label = rule.label if rule else occ.name
        error: str | None = None
        result: dict | None = None
        try:
            for delay in (0, *RETRY_DELAYS):
                if delay:
                    await _sleep(delay)
                try:
                    result = await self.client.book(occ.id)
                    error = None
                    break
                except API_ERRORS as err:
                    error = str(err) or type(err).__name__
                    _LOGGER.warning("Booking %s failed: %s", label, error)
        finally:
            self._in_progress.discard(occ.id)

        state = self.rule_state.setdefault(rule_id, RuleState())
        event_data = {
            "rule": label,
            "activity_id": occ.id,
            "name": occ.name,
            "center": occ.center_name,
            "start": occ.start.isoformat(),
        }
        if error is None and result is not None:
            waitlist = result.get("__typename") == "WaitingListBooking"
            state.handled[occ.id] = {
                "start": occ.start.isoformat(),
                "result": "waitlist" if waitlist else "booked",
            }
            event_data["waiting_list"] = waitlist
            event_data["waiting_list_position"] = result.get("waitingListPosition")
            self.hass.bus.async_fire(EVENT_BOOKED, event_data)
            if self.data is None:
                self.statuses[rule_id] = RuleStatus(
                    STATUS_WAITLIST if waitlist else STATUS_BOOKED, occ
                )
        else:
            state.handled[occ.id] = {
                "start": occ.start.isoformat(),
                "result": "failed",
                "error": error or "unknown",
            }
            self.hass.bus.async_fire(EVENT_BOOKING_FAILED, {**event_data, "error": error})
            if self.data is None:
                self.statuses[rule_id] = RuleStatus(STATUS_FAILED, occ, error=error)

        await self._async_after_state_change()
        await self.async_request_refresh()

    # -- actions used by entities and services -----------------------------

    async def async_set_enabled(self, rule_id: str, enabled: bool) -> None:
        state = self.rule_state.setdefault(rule_id, RuleState())
        state.enabled = enabled
        if enabled:
            # Resuming gives failed occurrences a new chance.
            state.handled = {k: v for k, v in state.handled.items() if v["result"] != "failed"}
        await self._async_after_state_change()

    async def async_set_skip(self, rule_id: str, skip: bool) -> None:
        state = self.rule_state.setdefault(rule_id, RuleState())
        status = self.statuses.get(rule_id)
        occ = status.occurrence if status else None
        state.skip_activity_id = occ.id if skip and occ else None
        await self._async_after_state_change()

    async def _async_after_state_change(self) -> None:
        await self._async_save_state()
        if self.data is not None:
            self._evaluate(self.data)
        self.async_update_listeners()

    async def async_book(self, activity_id: str) -> dict:
        try:
            result = await self.client.book(activity_id)
        except API_ERRORS as err:
            raise HomeAssistantError(f"Booking failed: {err}") from err
        await self.async_request_refresh()
        return result

    async def async_cancel(self, booking_id: str) -> None:
        bookings = self.data.bookings if self.data else []
        booking = next((b for b in bookings if b.id == booking_id), None)
        if booking is None:
            raise HomeAssistantError(f"Unknown booking {booking_id}")
        if not booking.can_cancel:
            raise HomeAssistantError(f"Booking cannot be cancelled ({booking.cancel_code})")
        try:
            await self.client.cancel(booking)
        except API_ERRORS as err:
            raise HomeAssistantError(f"Cancellation failed: {err}") from err
        await self.async_request_refresh()
