"""Rule model: a recurring weekly class to book automatically."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import tzinfo
from typing import Any

from .threet_api import Booking, GroupActivity
from .threet_api.suggest import WEEKDAYS, SlotKey


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    center_id: str
    center_name: str
    weekday: int  # 0 = Monday
    time: str  # "HH:MM" local time
    activity_type_id: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} – {self.center_name}, {WEEKDAYS[self.weekday]} {self.time}"

    def matches(self, activity: GroupActivity, tz: tzinfo) -> bool:
        if activity.center_id != self.center_id:
            return False
        local = activity.start.astimezone(tz)
        if local.weekday() != self.weekday or local.strftime("%H:%M") != self.time:
            return False
        if self.activity_type_id and activity.activity_type_id:
            return activity.activity_type_id == self.activity_type_id
        return activity.name == self.name

    def same_slot(self, other: Rule) -> bool:
        return (self.name, self.center_id, self.weekday, self.time) == (
            other.name,
            other.center_id,
            other.weekday,
            other.time,
        )

    @classmethod
    def from_slot(cls, slot: SlotKey) -> Rule:
        return cls(
            id=uuid.uuid4().hex,
            name=slot.activity_name,
            center_id=slot.center_id,
            center_name=slot.center_name,
            weekday=slot.weekday,
            time=slot.time,
            activity_type_id=slot.activity_type_id,
        )

    @classmethod
    def from_activity(cls, activity: GroupActivity, tz: tzinfo) -> Rule:
        local = activity.start.astimezone(tz)
        return cls(
            id=uuid.uuid4().hex,
            name=activity.name,
            center_id=activity.center_id,
            center_name=activity.center_name,
            weekday=local.weekday(),
            time=local.strftime("%H:%M"),
            activity_type_id=activity.activity_type_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(**data)


@dataclass
class RuleState:
    """Persistent per-rule state (stored in HA's .storage, not in options)."""

    enabled: bool = True
    skip_activity_id: str | None = None
    # activity_id -> {"start": iso, "result": "booked"|"waitlist"|"failed", "error": str}
    handled: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class RuleStatus:
    """Computed status of a rule after the latest refresh."""

    status: str
    occurrence: GroupActivity | None = None
    booking: Booking | None = None
    error: str | None = None
