"""Tests for the training statistics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.threet.threet_api.stats import compute, parse_workouts, summary_months

from .test_integration import setup_entry

OSLO = ZoneInfo("Europe/Oslo")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must be set up before hass in this module."""
    yield


def iso(d: datetime) -> str:
    return d.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


def make_summary(dates: list[datetime], names=None) -> dict:
    months: dict[tuple[int, int], int] = {}
    for d in dates:
        months[(d.year, d.month)] = months.get((d.year, d.month), 0) + 1
    years = sorted({y for y, _ in months})
    return {
        "period": {"start": "2023-01-01T00:00:00.000Z", "end": "2026-01-01T00:00:00.000Z"},
        "visits": len(dates),
        "years": [
            {
                "year": f"{y}-01-01T12:00:00.000Z",
                "visits": sum(n for (yy, _), n in months.items() if yy == y),
                "months": [
                    {"month": f"{y}-{m:02d}-01T12:00:00.000Z", "visits": n}
                    for (yy, m), n in sorted(months.items())
                    if yy == y
                ],
                "instructorPopularity": [
                    {"occurences": 5, "instructor": {"page": {"title": "Kari"}}},
                    {"occurences": 2, "instructor": {"page": {"title": "Ola"}}},
                ],
            }
            for y in years
        ],
        "userWorkouts": [
            {
                "workoutDate": iso(d),
                "workoutName": (names or {}).get(d, "Spinn 60 "),
                "center": {"id": "1", "page": {"title": "3T-Rosten"}},
            }
            for d in dates
        ],
    }


def test_compute_core_numbers() -> None:
    today = date(2026, 11, 16)  # Monday
    # 3 per week (Mon/Wed/Fri 19:00) for the last 10 weeks, plus some last year.
    dates = []
    for w in range(10):
        monday = datetime(2026, 11, 16, 19, tzinfo=OSLO) - timedelta(weeks=w)
        for d in (0, 2, 4):
            if monday + timedelta(days=d) <= datetime(2026, 11, 16, 20, tzinfo=OSLO):
                dates.append(monday + timedelta(days=d))
    dates += [datetime(2025, 3, 3, 7, tzinfo=OSLO), datetime(2025, 11, 20, 7, tzinfo=OSLO)]
    summary = make_summary(dates)
    stats = compute(
        today=today,
        workouts=parse_workouts(summary, OSLO),
        monthly={**{"2021-05": 7}, **summary_months(summary, OSLO)},
        goal=3,
        summary=summary,
        old_ytd={2021: 7},
        prev_12m=4,
        no_shows=[],
        tz=OSLO,
    )
    assert stats["this_week"] == 1  # Monday of this week
    assert stats["last_week"] == 3
    assert stats["goal_streak"] == 9  # this week not reached yet -> counts from last week
    assert stats["active_streak"] == 10
    assert stats["ytd"] == 28
    assert stats["ytd_last_year"] == 1  # 3 March 2025 (20 Nov is after the same date)
    assert stats["ytd_per_year"] == {"2021": 7, "2025": 1, "2026": 28}
    assert stats["per_year"]["2021"] == 7
    assert stats["total"] == 7 + 30
    assert stats["last_12m"] == 29  # includes 20 Nov 2025
    assert stats["last_12m_change_pct"] == 625.0
    assert stats["favorite_class_ytd"] == "Spinn 60"
    assert stats["favorite_weekday"] in ("mandag", "onsdag", "fredag")
    assert stats["favorite_time_of_day"] == "kveld"
    assert stats["favorite_instructor_ytd"] == "Kari"
    assert stats["days_since_last"] == 0
    assert stats["best_month"] in ("oktober 2026", "september 2026")


async def test_stats_sensors_and_backfill(hass: HomeAssistant, fake_client, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    freezer.move_to("2026-11-16 12:00:00+01:00")
    now = dt_util.now()
    dates = [now - timedelta(days=d) for d in (1, 3, 8, 30, 400)]
    fake_client.get_workout_summary.return_value = {"goal": 3, "summary": make_summary(dates)}

    async def visits(start: date, end: date) -> int:
        # 2 visits per month in 2021 and 2022, nothing before
        if start.year in (2021, 2022):
            return 24 if (end - start).days > 40 else 2
        return 0

    fake_client.get_visits.side_effect = visits
    await setup_entry(hass, rules=())
    await hass.async_block_till_done(wait_background_tasks=True)

    total = hass.states.get("sensor.3t_okter_totalt")
    assert total.state == str(24 + 24 + len(dates))
    assert total.attributes["per_year"]["2021"] == 24
    assert hass.states.get("sensor.3t_treningsmal_per_uke").state == "3"
    assert hass.states.get("sensor.3t_favorittime_i_ar").state == "Spinn 60"
    # backfill: yearly probes 2022, 2021, 2020, 2019 + 12 months for 2022 and 2021,
    # daily: same-date YTD for 2021 and 2022 (prev 12 months is inside the summary period)
    assert fake_client.get_visits.await_count == 4 + 24 + 2

    # Next refresh the same day: one summary call, nothing else.
    fake_client.get_visits.reset_mock()
    fake_client.get_my_bookings.reset_mock()
    await hass.config_entries.async_reload(hass.config_entries.async_entries("threet")[0].entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fake_client.get_visits.await_count == 2  # backfill is stored, daily values re-fetched once


async def test_long_term_statistics_import(recorder_mock, hass: HomeAssistant, fake_client) -> None:  # noqa: E501
    from homeassistant.components.recorder.statistics import (
        list_statistic_ids,
        statistics_during_period,
    )
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    await hass.config.async_set_time_zone("Europe/Oslo")
    now = dt_util.now()
    dates = [now - timedelta(days=d) for d in (1, 2, 40, 41, 42)]
    fake_client.get_workout_summary.return_value = {"goal": 3, "summary": make_summary(dates)}
    await setup_entry(hass, rules=())
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    ids = await recorder_mock.async_add_executor_job(list_statistic_ids, hass, None, "sum")
    stat_id = "threet:okter_12345"
    assert any(i["statistic_id"] == stat_id for i in ids)
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period, hass, now - timedelta(days=400), None, {stat_id}, "month", None, {"change"}
    )
    assert sum(r["change"] for r in stats[stat_id]) == len(dates)
