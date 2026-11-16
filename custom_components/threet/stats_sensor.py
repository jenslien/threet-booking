"""Training statistics sensors (created by sensor.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .stats_coordinator import ThreeTStatsCoordinator

WORKOUTS = "økter"
WEEKS = "uker"


@dataclass(frozen=True, kw_only=True)
class StatsDescription(SensorEntityDescription):
    attrs: tuple[str, ...] = field(default_factory=tuple)


def _count(
    key, name, icon="mdi:dumbbell", attrs=(), unit=WORKOUTS, state_class=SensorStateClass.MEASUREMENT
) -> StatsDescription:
    return StatsDescription(
        key=key,
        name=name,
        icon=icon,
        native_unit_of_measurement=unit,
        state_class=state_class,
        attrs=attrs,
    )


def _text(key, name, icon, attrs=()) -> StatsDescription:
    return StatsDescription(key=key, name=name, icon=icon, attrs=attrs)


DESCRIPTIONS: tuple[StatsDescription, ...] = (
    # Year to date and comparisons
    _count("ytd", "Økter i år", "mdi:calendar-today", ("ytd_per_year", "forecast_year")),
    _count("ytd_last_year", "Økter i fjor til samme dato", "mdi:calendar-arrow-left"),
    _count("ytd_change_pct", "Endring i år mot i fjor", "mdi:trending-up", unit=PERCENTAGE),
    _count("forecast_year", "Prognose for året", "mdi:crystal-ball"),
    # Rolling 12 months
    _count("last_12m", "Økter siste 12 måneder", "mdi:calendar-range", ("avg_week_12m",)),
    _count("prev_12m", "Økter 12 måneder før det", "mdi:calendar-range-outline"),
    _count("last_12m_change_pct", "Endring siste 12 måneder", "mdi:trending-up", unit=PERCENTAGE),
    # Totals
    _count(
        "total",
        "Økter totalt",
        "mdi:sigma",
        ("per_year", "per_month", "statistic_id"),
        state_class=None,
    ),
    _count("best_year_count", "Beste år", "mdi:trophy", ("best_year",)),
    _count("best_month_count", "Beste måned", "mdi:trophy-outline", ("best_month",)),
    # Weeks and months
    _count("this_week", "Økter denne uka", "mdi:calendar-week", ("goal", "goal_progress_pct")),
    _count("last_week", "Økter forrige uke", "mdi:calendar-week-begin"),
    _count("this_month", "Økter denne måneden", "mdi:calendar-month"),
    _count("last_month", "Økter forrige måned", "mdi:calendar-month-outline"),
    _count("avg_week_12m", "Snitt per uke siste 12 måneder", "mdi:chart-line", unit="økter/uke"),
    _count("avg_week_ytd", "Snitt per uke i år", "mdi:chart-line", unit="økter/uke"),
    # Goal and streaks
    _count("goal", "Treningsmål per uke", "mdi:flag-checkered", unit="økter/uke"),
    _count("goal_progress_pct", "Ukemål", "mdi:progress-check", unit=PERCENTAGE),
    _count("goal_streak", "Uker på rad med nådd mål", "mdi:fire", ("goal_streak_longest",), unit=WEEKS),
    _count("active_streak", "Uker på rad med trening", "mdi:fire-circle", ("active_streak_longest",), unit=WEEKS),
    # Latest
    StatsDescription(
        key="last_workout",
        name="Siste økt",
        icon="mdi:history",
        device_class=SensorDeviceClass.TIMESTAMP,
        attrs=("last_workout_name",),
    ),
    _count("days_since_last", "Dager siden siste økt", "mdi:timer-sand", unit=UnitOfTime.DAYS),
    # Favourites and habits
    _text("favorite_class_ytd", "Favorittime i år", "mdi:heart", ("favorite_class_ytd_top",)),
    _text("favorite_class_12m", "Favorittime siste 12 måneder", "mdi:heart-outline", ("favorite_class_12m_top",)),
    _count("distinct_classes_ytd", "Ulike timer i år", "mdi:shape", unit=None),
    _text("favorite_instructor_ytd", "Favorittinstruktør i år", "mdi:account-star", ("favorite_instructor_ytd_top",)),
    _text("favorite_weekday", "Favorittdag", "mdi:calendar-star", ("weekdays",)),
    _text("favorite_time_of_day", "Favoritt tid på døgnet", "mdi:clock-outline", ("time_of_day",)),
    _text("favorite_center", "Favorittsenter", "mdi:map-marker-star", ("centers",)),
    # No-shows
    _count("no_shows_12m", "No-shows siste 12 måneder", "mdi:account-cancel", ("no_show_fees_12m",), unit=None),
    _count("no_show_fees_12m", "No-show-gebyrer siste 12 måneder", "mdi:cash-remove", unit="kr"),
)

# Large attributes are not written to the recorder on every change.
_BIG_ATTRS = frozenset(
    {"per_month", "per_year", "ytd_per_year", "favorite_class_ytd_top", "favorite_class_12m_top",
     "favorite_instructor_ytd_top", "weekdays", "time_of_day", "centers"}
)


class StatsSensor(CoordinatorEntity[ThreeTStatsCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = _BIG_ATTRS
    entity_description: StatsDescription

    def __init__(self, coordinator: ThreeTStatsCoordinator, description: StatsDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_stats_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.main.device_prefix,
            manufacturer="3T-Treningssenter",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> Any:
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {k: data.get(k) for k in self.entity_description.attrs}
