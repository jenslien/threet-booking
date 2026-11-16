"""Fixtures: a fake 3T client so tests never touch the real API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.threet.threet_api import Booking, Center, GroupActivity


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_retry_delay():
    with patch("custom_components.threet.coordinator._sleep", AsyncMock()):
        yield


def make_activity(aid: str, start: datetime, *, name="Spinn 60", opens: datetime | None = None,
                  center_id="1", type_id="5574") -> GroupActivity:
    def iso(dt):
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    return GroupActivity.from_api({
        "id": aid,
        "name": name,
        "start": iso(start),
        "end": iso(start + timedelta(hours=1)),
        "bookableEarliest": iso(opens or start - timedelta(days=7)),
        "bookableLatest": iso(start - timedelta(minutes=15)),
        "center": {"id": center_id, "slug": "3t-rosten", "page": {"title": "3T-Rosten"}},
        "instructors": [{"page": {"title": "Instruktør"}}],
        "slot": {"total": 30, "available": 10, "hasWaitingList": True, "inWaitingList": 0},
        "cancel": {"isEnabled": True, "code": ""},
        "groupActivity": {"id": type_id},
    })


def make_booking(bid: str, activity: GroupActivity, kind="GroupActivityBooking") -> Booking:
    return Booking(id=bid, activity_id=activity.id, kind=kind, waiting_list_position=None,
                   can_cancel=True, cancel_code="", activity=activity)


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.user_id = "12345"
    client.login = AsyncMock()
    client.get_my_bookings = AsyncMock(return_value=[])
    client.get_group_activities = AsyncMock(return_value=[])
    client.get_centers = AsyncMock(return_value=[Center("1", "3t-rosten", "3T-Rosten")])
    client.book = AsyncMock(return_value={"__typename": "GroupActivityBooking", "id": "b1"})
    client.cancel = AsyncMock(return_value={})
    with (
        patch("custom_components.threet.ThreeTClient", return_value=client),
        patch("custom_components.threet.config_flow.ThreeTClient", return_value=client),
    ):
        yield client
