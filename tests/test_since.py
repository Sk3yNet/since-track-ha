from datetime import datetime, timedelta
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.since.const import DOMAIN
from custom_components.since.manager import add_interval


async def _setup(hass, allowed, notify=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"allowed_users": allowed, "notify_service": notify, "hide_names": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_owner_full_flow(hass: HomeAssistant, hass_ws_client, hass_admin_user):
    await _setup(hass, [hass_admin_user.id])
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "since/subscribe"})
    assert (await ws.receive_json())["success"]
    assert (await ws.receive_json())["event"] == {"events": []}

    await ws.send_json({"id": 2, "type": "since/event/create", "name": "Water plants",
                        "interval": {"amount": 1, "unit": "weeks"}, "notifies": True, "log_now": True})
    pushed = await ws.receive_json()
    res = await ws.receive_json()
    if "event" in res:
        pushed, res = res, pushed
    assert res["success"]
    ev_id = res["result"]["id"]
    assert pushed["event"]["events"][0]["name"] == "Water plants"
    assert len(pushed["event"]["events"][0]["occurrences"]) == 1

    await ws.send_json({"id": 3, "type": "since/log", "event_id": ev_id,
                        "date": "2024-01-01T10:00:00Z", "note": "old"})
    msgs = [await ws.receive_json(), await ws.receive_json()]
    data = next(m for m in msgs if "event" in m)["event"]["events"][0]
    assert data["occurrences"][-1]["note"] == "old"  # sorted newest first
    occ_id = data["occurrences"][-1]["id"]

    await ws.send_json({"id": 4, "type": "since/occurrence/delete", "event_id": ev_id, "occurrence_id": occ_id})
    msgs = [await ws.receive_json(), await ws.receive_json()]
    assert all(m.get("success", True) for m in msgs)

    await ws.send_json({"id": 5, "type": "since/event/delete", "event_id": "nope"})
    assert (await ws.receive_json())["error"]["code"] == "not_found"

    # Nothing leaks into the state machine.
    assert not [s for s in hass.states.async_all() if "since" in s.entity_id]


async def test_other_users_refused(hass, hass_ws_client, hass_admin_user, hass_read_only_user, hass_read_only_access_token):
    await _setup(hass, ["someone-else"])
    # Admin who isn't on the list is refused.
    ws = await hass_ws_client(hass)
    for i, t in enumerate(["since/subscribe", "since/event/create"], 1):
        msg = {"id": i, "type": t}
        if t.endswith("create"):
            msg["name"] = "x"
        await ws.send_json(msg)
        r = await ws.receive_json()
        assert r["success"] is False and r["error"]["code"] == "unauthorized"
    # Non-admin user too.
    ws2 = await hass_ws_client(hass, hass_read_only_access_token)
    await ws2.send_json({"id": 1, "type": "since/subscribe"})
    r = await ws2.receive_json()
    assert r["error"]["code"] == "unauthorized"


async def test_reminder_and_action(hass, hass_ws_client, hass_admin_user):
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    await _setup(hass, [hass_admin_user.id], notify="notify.mobile_app_phone")
    manager = hass.data[DOMAIN]
    past = (dt_util.utcnow() - timedelta(days=8)).isoformat()
    ev = manager.create_event("Bins", {"amount": 1, "unit": "weeks"}, True, False)
    manager.log(ev["id"], past, "")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["message"] == "Bins is due"
    action = calls[0].data["data"]["actions"][0]["action"]
    # Not repeated for the same due date.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=4))
    await hass.async_block_till_done()
    assert len(calls) == 1

    # Action from another user is ignored, from owner logs it.
    hass.bus.async_fire("mobile_app_notification_action", {"action": action}, context=Context(user_id="intruder"))
    await hass.async_block_till_done()
    assert len(manager._event(ev["id"])["occurrences"]) == 1
    hass.bus.async_fire("mobile_app_notification_action", {"action": action}, context=Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()
    assert len(manager._event(ev["id"])["occurrences"]) == 2


async def test_persistence(hass, hass_admin_user, hass_storage):
    entry = await _setup(hass, [hass_admin_user.id])
    hass.data[DOMAIN].create_event("Haircut", None, False, True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass_storage["since.data"]["data"]["events"][0]["name"] == "Haircut"


async def test_card_served(hass, hass_client, hass_admin_user):
    await _setup(hass, [hass_admin_user.id])
    client = await hass_client()
    r = await client.get("/since_static/since-card.js")
    assert r.status == 200
    assert "since-card" in await r.text()


async def test_config_flow(hass, hass_admin_user):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"allowed_users": []})
    assert r["errors"] == {"allowed_users": "no_users"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"allowed_users": [hass_admin_user.id]})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"allowed_users": [hass_admin_user.id], "notify_service": "notify.mobile_app_x", "hide_names": True})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    m = hass.data[DOMAIN]
    assert m.allowed_users == {hass_admin_user.id} and m.hide_names and m.notify_service == "notify.mobile_app_x"


def test_add_interval_clamps():
    start = datetime(2025, 1, 31, 12, tzinfo=dt_util.UTC)
    assert add_interval(start, 1, "months").day in (28, 1)  # depends on local tz offset
    assert add_interval(start, 2, "hours") == start + timedelta(hours=2)
