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

    # Booking opens -> exactly one booking call.
    fake_client.get_my_bookings.return_value = [make_booking("b1", occ)]
    freezer.move_to(opens + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    fake_client.book.assert_awaited_once_with("a1")
    assert hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status").state == "booket"
    assert hass.states.get("sensor.3t_booking_1").attributes["booking_id"] == "b1"

    # User cancels via the button -> status "avbestilt", no new booking.
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.3t_avbestill_booking_1"}, blocking=True
    )
    fake_client.cancel.assert_awaited_once()
    fake_client.get_my_bookings.return_value = []
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.3t_fast_spinn_60_3t_rosten_mandag_19_00_status").state == "avbestilt"
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
