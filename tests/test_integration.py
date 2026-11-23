"""Tests for config flow, options flow and the booking scheduler."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.threet.const import CONF_RULES, DOMAIN
from custom_components.threet.threet_api import ThreeTAuthError

from .conftest import make_activity, make_booking

RULE = {
    "id": "r1",
    "name": "Spinn 60",
    "center_id": "1",
    "center_name": "3T-Rosten",
    "weekday": 0,
    "time": "19:00",
    "activity_type_id": "5574",
}


def next_monday_19(now):
    local = dt_util.as_local(now)
    days = (0 - local.weekday()) % 7 or 7
    return (local + timedelta(days=days)).replace(hour=19, minute=0, second=0, microsecond=0)


async def setup_entry(hass, rules=(RULE,)) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        title="a@b.no",
        data={"username": "a@b.no", "password": "x"},
        options={CONF_RULES: list(rules)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_user_flow(hass: HomeAssistant, fake_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "a@b.no", "password": "x"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "12345"


async def test_user_flow_invalid_auth(hass: HomeAssistant, fake_client) -> None:
    fake_client.login.side_effect = ThreeTAuthError("nope")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "a@b.no", "password": "x"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_books_when_booking_opens_and_never_rebooks(
    hass: HomeAssistant, fake_client, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    start = next_monday_19(dt_util.utcnow())
    opens = dt_util.utcnow() + timedelta(hours=1)
    occ = make_activity("a1", start, opens=opens)
    fake_client.get_group_activities.return_value = [occ]

    await setup_entry(hass)
    status = hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status")
    assert status.state == "planlagt"
    assert hass.states.get("switch.3t_fast_spinn_60_3t_rosten_mandag_19_00_aktiv").state == "on"
    fake_client.book.assert_not_called()

    # Booking opens -> exactly one booking call; afterwards 3T lists the booking.
    async def _book(activity_id):
        fake_client.get_my_bookings.return_value = [make_booking("b1", occ)]
        return {"__typename": "GroupActivityBooking", "id": "b1"}

    fake_client.book.side_effect = _book
    freezer.move_to(opens + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    fake_client.book.assert_awaited_once_with("a1")
    status = hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status")
    # This week's is booked; the status now looks ahead to next week's (predicted).
    assert status.state == "planlagt"
    assert len(status.attributes["booked"]) == 1
    assert status.attributes["predicted"] is True
    assert hass.states.get("sensor.3t_booking_1").attributes["booking_id"] == "b1"

    # User cancels via the button -> status "avbestilt", no new booking.
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.3t_avbestill_booking_1"}, blocking=True
    )
    fake_client.cancel.assert_awaited_once()
    fake_client.get_my_bookings.return_value = []
    freezer.tick(timedelta(minutes=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    status = hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status")
    assert status.attributes["booked"] == []
    assert fake_client.book.await_count == 1


async def test_pause_and_skip(hass: HomeAssistant, fake_client, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    start = next_monday_19(dt_util.utcnow())
    opens = dt_util.utcnow() + timedelta(hours=1)
    fake_client.get_group_activities.return_value = [make_activity("a1", start, opens=opens)]
    await setup_entry(hass)
    status_id = "sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status"

    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.3t_fast_spinn_60_3t_rosten_mandag_19_00_aktiv"}, blocking=True,
    )
    assert hass.states.get(status_id).state == "pauset"
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.3t_fast_spinn_60_3t_rosten_mandag_19_00_aktiv"}, blocking=True,
    )
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.3t_fast_spinn_60_3t_rosten_mandag_19_00_hopp_over_neste"},
        blocking=True,
    )
    assert hass.states.get(status_id).state == "hoppet_over"

    freezer.move_to(opens + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    fake_client.book.assert_not_called()


async def test_failed_booking_retries_then_fails(hass: HomeAssistant, fake_client, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    start = next_monday_19(dt_util.utcnow())
    # Already open -> booked right away on setup.
    fake_client.get_group_activities.return_value = [
        make_activity("a1", start, opens=dt_util.utcnow() - timedelta(hours=1))
    ]
    from custom_components.threet.threet_api import ThreeTError

    fake_client.book.side_effect = ThreeTError("Maks antall bookinger")
    events = []
    hass.bus.async_listen("threet_booking_failed", events.append)
    await setup_entry(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fake_client.book.await_count == 4
    state = hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status")
    assert state.state == "feilet"
    assert state.attributes["error"] == "Maks antall bookinger"
    assert len(events) == 1


async def test_options_suggestions(hass: HomeAssistant, fake_client) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    entry = await setup_entry(hass, rules=())
    now = dt_util.utcnow()
    hist = [
        make_booking(f"h{i}", make_activity(f"x{i}", next_monday_19(now) - timedelta(weeks=i + 1)))
        for i in range(3)
    ]
    fake_client.get_my_bookings.return_value = hist

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "suggestions"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"slots": ["0"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    rules = entry.options[CONF_RULES]
    assert len(rules) == 1
    assert (rules[0]["name"], rules[0]["weekday"], rules[0]["time"]) == ("Spinn 60", 0, "19:00")


async def test_options_pick_from_timetable(hass: HomeAssistant, fake_client) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    entry = await setup_entry(hass, rules=())
    fake_client.get_group_activities.return_value = [
        make_activity("a9", next_monday_19(dt_util.utcnow()))
    ]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_center"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"center": "1", "weekday": "0"}
    )
    assert result["step_id"] == "add_class"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"classes": ["a9"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_RULES][0]["time"] == "19:00"


async def test_calendar_events_api(hass: HomeAssistant, fake_client, hass_client) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    start = next_monday_19(dt_util.utcnow())
    booked = make_activity("a1", start)
    planned = make_activity("a2", start + timedelta(days=1), opens=dt_util.utcnow() + timedelta(days=1))
    fake_client.get_my_bookings.return_value = [make_booking("b1", booked)]
    fake_client.get_group_activities.return_value = [booked, planned]
    await setup_entry(hass, rules=(RULE, {**RULE, "id": "r2", "weekday": 1}))

    client = await hass_client()
    s = (dt_util.utcnow() - timedelta(days=1)).isoformat()
    e = (dt_util.utcnow() + timedelta(days=14)).isoformat()
    resp = await client.get("/api/calendars/calendar.3t_bookinger", params={"start": s, "end": e})
    assert resp.status == 200, await resp.text()
    events = await resp.json()
    # booked Monday, planned Tuesday, predicted next Monday
    assert [ev["summary"] for ev in events] == ["Spinn 60", "Spinn 60 (planlagt)", "Spinn 60 (planlagt)"]


async def test_cancel_button_name_and_too_late(hass: HomeAssistant, fake_client) -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    await hass.config.async_set_time_zone("Europe/Oslo")
    a = make_activity("a1", next_monday_19(dt_util.utcnow()))
    b = make_booking("b1", a)
    late = make_booking("b2", make_activity("a2", next_monday_19(dt_util.utcnow()) + timedelta(days=1)))
    late = type(late)(**{**late.__dict__, "can_cancel": False, "cancel_code": "TOO_LATE_TO_CANCEL"})
    fake_client.get_my_bookings.return_value = [b, late]
    await setup_entry(hass, rules=())

    s1 = hass.states.get("button.3t_avbestill_booking_1")
    assert s1.attributes["friendly_name"].startswith("man. ")
    assert s1.attributes["friendly_name"].endswith("kl. 19:00  Spinn 60")
    assert s1.attributes["details"] == "3T-Rosten, Instruktør, 10 av 30 ledige"
    s2 = hass.states.get("button.3t_avbestill_booking_2")
    assert s2.attributes["details"].endswith("kan ikke avbestilles")
    assert hass.states.get("button.3t_avbestill_booking_3").state == "unavailable"

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.3t_avbestill_booking_2"}, blocking=True
        )
    fake_client.cancel.assert_not_called()


async def test_books_next_week_while_this_week_is_booked(
    hass: HomeAssistant, fake_client, freezer: FrozenDateTimeFactory
) -> None:
    """Next week's booking opens at 08:00 on the day of this week's class."""
    await hass.config.async_set_time_zone("Europe/Oslo")
    this_week = next_monday_19(dt_util.utcnow())
    opens = dt_util.utcnow() + timedelta(hours=1)
    a1 = make_activity("a1", this_week)
    a2 = make_activity("a2", this_week + timedelta(weeks=1), opens=opens)
    fake_client.get_my_bookings.return_value = [make_booking("b1", a1)]
    fake_client.get_group_activities.return_value = [a1, a2]
    await setup_entry(hass)

    status = hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status")
    assert status.state == "planlagt"
    assert status.attributes["activity_id"] == "a2"
    assert len(status.attributes["booked"]) == 1

    freezer.move_to(opens + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    fake_client.book.assert_awaited_once_with("a2")


async def test_api_traffic_per_day(hass: HomeAssistant, fake_client, freezer) -> None:
    """Keep the load low: bookings hourly, timetable every 6 hours, nothing else."""
    await hass.config.async_set_time_zone("Europe/Oslo")
    await setup_entry(hass)  # one rule, one center
    await hass.async_block_till_done(wait_background_tasks=True)
    fake_client.get_my_bookings.reset_mock()
    fake_client.get_workout_summary.reset_mock()
    fake_client.get_group_activities.reset_mock()
    for _ in range(24 * 6):  # one day in 10-minute steps
        freezer.tick(timedelta(minutes=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    # 24 hourly + 1 daily booking-history fetch for the statistics
    assert fake_client.get_my_bookings.await_count == 25
    assert fake_client.get_group_activities.await_count == 4
    assert fake_client.get_workout_summary.await_count == 4
    fake_client.book.assert_not_called()


async def test_no_timetable_calls_without_rules(hass: HomeAssistant, fake_client) -> None:
    await setup_entry(hass, rules=())
    fake_client.get_group_activities.assert_not_called()


def _waitlisted(start):
    from custom_components.threet.threet_api import Booking

    return Booking(id="w1", activity_id="aw", kind="WaitingListBooking", waiting_list_position=3,
                   can_cancel=True, cancel_code="", activity=make_activity("aw", start))


async def test_waitlist_polling_tiers(hass: HomeAssistant, fake_client) -> None:
    from custom_components.threet.coordinator import ThreeTCoordinator, ThreeTData

    now = dt_util.utcnow()
    poll = ThreeTCoordinator._poll_interval
    def interval(hours_left):
        return poll(ThreeTData(bookings=[_waitlisted(now + timedelta(hours=hours_left))]), now)

    assert interval(48) == timedelta(hours=1)
    assert interval(24.5) == timedelta(minutes=30)  # wakes exactly when the 15-min tier starts
    assert interval(10) == timedelta(minutes=15)
    assert interval(4) == timedelta(minutes=5)
    assert interval(2.5) == timedelta(minutes=1)
    assert interval(1.5) == timedelta(hours=1)  # waiting list closed (2 h before)
    assert poll(ThreeTData(bookings=[]), now) == timedelta(hours=1)


async def test_waitlist_polling_traffic(hass: HomeAssistant, fake_client, freezer) -> None:
    """Total bookings calls from 30 h before a waitlisted class until it starts."""
    await hass.config.async_set_time_zone("Europe/Oslo")
    start = dt_util.utcnow() + timedelta(hours=30)
    fake_client.get_my_bookings.return_value = [_waitlisted(start)]
    await setup_entry(hass, rules=())
    fake_client.get_my_bookings.reset_mock()
    while dt_util.utcnow() < start:
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    calls = fake_client.get_my_bookings.await_count
    # 6 h hourly + 19 h at 15 min + 2 h at 5 min + 1 h at 1 min + 2 h hourly ≈ 6+76+24+60+2
    assert 160 <= calls <= 175, calls


async def test_refresh_button(hass: HomeAssistant, fake_client) -> None:
    await setup_entry(hass, rules=())
    fake_client.get_my_bookings.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.3t_oppdater_na"}, blocking=True
    )
    await hass.async_block_till_done()
    assert fake_client.get_my_bookings.await_count == 1
    fake_client.get_group_activities.assert_not_called()


async def test_two_accounts(hass: HomeAssistant, fake_client) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    a = make_activity("a1", next_monday_19(dt_util.utcnow()))
    fake_client.get_my_bookings.return_value = [make_booking("b1", a)]
    await setup_entry(hass)  # unnamed: entity ids keep the plain "3t_" prefix
    kona = MockConfigEntry(
        domain=DOMAIN,
        unique_id="67890",
        title="Kona",
        data={"username": "kona@example.no", "password": "x", "name": "Kona"},
        options={CONF_RULES: [RULE]},
    )
    kona.add_to_hass(hass)
    assert await hass.config_entries.async_setup(kona.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("button.3t_avbestill_booking_1")
    assert hass.states.get("button.3t_kona_avbestill_booking_1")
    assert hass.states.get("sensor.3t_kona_kommende_bookinger")
    status = hass.states.get("sensor.3t_kona_fast_spinn_60_3t_rosten_mandag_19_00_status")
    assert status.attributes["account"] == "Kona"
    assert hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status").attributes["account"] is None

    import pytest
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("threet", "book", {"activity_id": "a1"}, blocking=True)
    await hass.services.async_call(
        "threet", "book", {"activity_id": "a1", "config_entry_id": kona.entry_id}, blocking=True
    )
    await hass.services.async_call("threet", "cancel", {"booking_id": "b1"}, blocking=True)
    fake_client.cancel.assert_awaited_once()


async def test_user_flow_with_name(hass: HomeAssistant, fake_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Kona", "username": "k@b.no", "password": "x"}
    )
    assert result["title"] == "Kona"
    assert result["data"]["name"] == "Kona"


def test_activity_icon() -> None:
    from custom_components.threet.models import activity_icon

    assert activity_icon("Spinn 60") == "mdi:bike"
    assert activity_icon("Indoor Cycling 45") == "mdi:bike"
    assert activity_icon("Styrke Total 45") == "mdi:dumbbell"
    assert activity_icon("Yin Yoga") == "mdi:yoga"
    assert activity_icon("Yoga Styrke") == "mdi:yoga"
    assert activity_icon("Zumba") == "mdi:dance-ballroom"
    assert activity_icon("Grunntrening") == "mdi:calendar-check"
    assert activity_icon(None) == "mdi:calendar-check"


async def test_icons_follow_class(hass: HomeAssistant, fake_client) -> None:
    await hass.config.async_set_time_zone("Europe/Oslo")
    a = make_activity("a1", next_monday_19(dt_util.utcnow()), name="Yin Yoga")
    fake_client.get_my_bookings.return_value = [make_booking("b1", a)]
    await setup_entry(hass)

    assert hass.states.get("sensor.3t_booking_1").attributes["icon"] == "mdi:yoga"
    assert hass.states.get("sensor.3t_booking_2").attributes["icon"] == "mdi:calendar-check"
    rule = "sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00"
    assert hass.states.get(f"{rule}_status").attributes["icon"] == "mdi:bike"
    assert hass.states.get(f"{rule}_neste_time").attributes["icon"] == "mdi:bike"
