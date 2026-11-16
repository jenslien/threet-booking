"""Training statistics: fetched a few times a day, with a one-time backfill of old years."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DOMAIN, STATS_INTERVAL
from .coordinator import API_ERRORS, ThreeTCoordinator
from .threet_api import ThreeTAuthError
from .threet_api.client import _parse_dt
from .threet_api.stats import compute, parse_workouts, summary_months

_LOGGER = logging.getLogger(__name__)

BACKFILL_MAX_YEARS = 20
BACKFILL_PAUSE = 0.5  # seconds between backfill calls, to be gentle on the API

_sleep = asyncio.sleep  # patched in tests


class ThreeTStatsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Visits/workout statistics for one account.

    Per refresh: 1 call (workout summary). Once a day: booking history (no-shows) and a
    few ``Visits`` calls for years older than the summary. Once ever: monthly backfill
    of those older years (stored, since the past does not change).
    """

    def __init__(self, hass: HomeAssistant, main: ThreeTCoordinator) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=main.config_entry,
            name=f"{DOMAIN}_stats",
            update_interval=STATS_INTERVAL,
        )
        self.main = main
        self.client = main.client
        self.tz = main.tz
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{main.config_entry.entry_id}.stats")
        self._stored: dict[str, Any] = {}
        self._daily: dict[str, Any] = {}

    @property
    def statistic_id(self) -> str:
        return f"{DOMAIN}:okter_{slugify(self.config_entry.unique_id or self.config_entry.entry_id)}"

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now(self.tz).date()
        try:
            result = await self.client.get_workout_summary()
            summary = result["summary"]
            period_start = _parse_dt((summary.get("period") or {}).get("start"))
            summary_start = period_start.astimezone(self.tz).date() if period_start else today

            if not self._stored:
                self._stored = await self._store.async_load() or {}
            if not self._stored.get("backfill_done"):
                await self._backfill(summary_start)

            if self._daily.get("date") != today.isoformat():
                self._daily = await self._fetch_daily(today, summary_start)
        except ThreeTAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except API_ERRORS as err:
            raise UpdateFailed(f"Error talking to 3T: {err}") from err

        workouts = parse_workouts(summary, self.tz)
        monthly = {**self._stored.get("old_months", {}), **summary_months(summary, self.tz)}
        prev_12m = self._daily["prev_12m"]
        if prev_12m is None:  # window fully covered by the summary: count locally
            start, end = today - timedelta(days=729), today - timedelta(days=364)
            prev_12m = sum(1 for w in workouts if start <= w.start.date() < end)

        stats = compute(
            today=today,
            workouts=workouts,
            monthly=monthly,
            goal=result.get("goal"),
            summary=summary,
            old_ytd={int(k): v for k, v in self._daily["old_ytd"].items()},
            prev_12m=prev_12m,
            no_shows=self._daily["no_shows"],
            tz=self.tz,
        )
        stats["statistic_id"] = self.statistic_id
        self._import_statistics(monthly)
        return stats

    async def _visits(self, start: date, end: date) -> int:
        await _sleep(BACKFILL_PAUSE)
        return await self.client.get_visits(start, end)

    async def _backfill(self, summary_start: date) -> None:
        """Monthly visits for every year before the summary (stops after 2 empty years)."""
        old_months: dict[str, int] = {}
        empty_run = 0
        year = summary_start.year - 1
        while empty_run < 2 and year >= summary_start.year - BACKFILL_MAX_YEARS:
            if await self._visits(date(year, 1, 1), date(year + 1, 1, 1)):
                empty_run = 0
                for month in range(1, 13):
                    nxt = date(year + (month == 12), month % 12 + 1, 1)
                    old_months[f"{year}-{month:02d}"] = await self._visits(date(year, month, 1), nxt)
            else:
                empty_run += 1
            year -= 1
        _LOGGER.debug("Backfilled %d months before %s", len(old_months), summary_start)
        self._stored = {"backfill_done": True, "old_months": old_months}
        await self._store.async_save(self._stored)

    async def _fetch_daily(self, today: date, summary_start: date) -> dict[str, Any]:
        tomorrow = today + timedelta(days=1)
        old_years = sorted(
            {int(k[:4]) for k, v in self._stored.get("old_months", {}).items() if v}
        )
        old_ytd = {}
        for year in old_years:
            try:
                same_day = tomorrow.replace(year=year)
            except ValueError:
                same_day = date(year, 3, 1)
            old_ytd[str(year)] = await self._visits(date(year, 1, 1), same_day)

        prev_start = today - timedelta(days=729)
        prev_12m = (
            await self._visits(prev_start, today - timedelta(days=364))
            if prev_start < summary_start
            else None
        )
        history = await self.client.get_my_bookings(dt_util.utcnow() - timedelta(days=366))
        return {
            "date": today.isoformat(),
            "old_ytd": old_ytd,
            "prev_12m": prev_12m,
            "no_shows": [b for b in history if b.kind == "NoShowBooking"],
        }

    def _import_statistics(self, monthly: dict[str, int]) -> None:
        """Monthly visits as long-term statistics, for statistics graphs with history."""
        if "recorder" not in self.hass.config.components or not monthly:
            return
        from homeassistant.components.recorder.models import (
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import async_add_external_statistics

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{self.main.device_prefix} økter",
            source=DOMAIN,
            statistic_id=self.statistic_id,
            unit_class=None,
            unit_of_measurement="økter",
        )
        total = 0
        rows = []
        for key in sorted(monthly):
            total += monthly[key]
            start = datetime(int(key[:4]), int(key[5:]), 1, tzinfo=self.tz)
            rows.append(StatisticData(start=start, state=monthly[key], sum=total))
        async_add_external_statistics(self.hass, metadata, rows)
