"""Websocket API for the Since card.

Every command checks that the logged-in user is one of the users chosen
in the integration's settings. Anyone else, admins included, gets an
"unauthorized" error and no data.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, UNITS
from .manager import SinceManager

INTERVAL = vol.Any(
    None,
    vol.Schema(
        {
            vol.Required("amount"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
            vol.Required("unit"): vol.In(UNITS),
        }
    ),
)
NAME = vol.All(cv.string, vol.Length(min=1, max=200))
NOTE = vol.All(cv.string, vol.Length(max=2000))


def _allowed(
    func: Callable[[HomeAssistant, websocket_api.ActiveConnection, dict[str, Any], SinceManager], None],
) -> Callable[[HomeAssistant, websocket_api.ActiveConnection, dict[str, Any]], None]:
    """Reject the command unless the caller is an allowed user."""

    @wraps(func)
    def wrapper(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
    ) -> None:
        manager: SinceManager | None = hass.data.get(DOMAIN)
        if manager is None:
            connection.send_error(msg["id"], "not_loaded", "Since is not set up")
            return
        user = connection.user
        if user is None or not manager.is_allowed(user.id):
            connection.send_error(
                msg["id"], websocket_api.ERR_UNAUTHORIZED, "You don't have access to Since"
            )
            return
        try:
            func(hass, connection, msg, manager)
        except KeyError:
            connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Not found")
        except ValueError as err:
            connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))

    return wrapper


@websocket_api.websocket_command({vol.Required("type"): "since/subscribe"})
@callback
@_allowed
def ws_subscribe(hass, connection, msg, manager: SinceManager) -> None:
    user_id = connection.user.id

    @callback
    def forward() -> None:
        current: SinceManager | None = hass.data.get(DOMAIN)
        if current is None or not current.is_allowed(user_id):
            return
        connection.send_message(websocket_api.event_message(msg["id"], current.as_dict()))

    connection.subscriptions[msg["id"]] = manager.async_add_listener(forward)
    connection.send_result(msg["id"])
    forward()


@websocket_api.websocket_command(
    {
        vol.Required("type"): "since/event/create",
        vol.Required("name"): NAME,
        vol.Optional("interval"): INTERVAL,
        vol.Optional("notifies", default=False): bool,
        vol.Optional("log_now", default=False): bool,
    }
)
@callback
@_allowed
def ws_create(hass, connection, msg, manager: SinceManager) -> None:
    event = manager.create_event(
        msg["name"], msg.get("interval"), msg["notifies"], msg["log_now"]
    )
    connection.send_result(msg["id"], {"id": event["id"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "since/event/update",
        vol.Required("event_id"): str,
        vol.Optional("name"): NAME,
        vol.Optional("interval"): INTERVAL,
        vol.Optional("notifies"): bool,
    }
)
@callback
@_allowed
def ws_update(hass, connection, msg, manager: SinceManager) -> None:
    changes = {k: msg[k] for k in ("name", "interval", "notifies") if k in msg}
    manager.update_event(msg["event_id"], changes)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): "since/event/delete", vol.Required("event_id"): str}
)
@callback
@_allowed
def ws_delete(hass, connection, msg, manager: SinceManager) -> None:
    manager.delete_event(msg["event_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "since/log",
        vol.Required("event_id"): str,
        vol.Optional("date"): vol.Any(None, str),
        vol.Optional("note", default=""): NOTE,
    }
)
@callback
@_allowed
def ws_log(hass, connection, msg, manager: SinceManager) -> None:
    occurrence = manager.log(msg["event_id"], msg.get("date"), msg["note"])
    connection.send_result(msg["id"], {"id": occurrence["id"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "since/occurrence/update",
        vol.Required("event_id"): str,
        vol.Required("occurrence_id"): str,
        vol.Optional("date"): str,
        vol.Optional("note"): NOTE,
    }
)
@callback
@_allowed
def ws_update_occurrence(hass, connection, msg, manager: SinceManager) -> None:
    manager.update_occurrence(
        msg["event_id"], msg["occurrence_id"], msg.get("date"), msg.get("note")
    )
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "since/occurrence/delete",
        vol.Required("event_id"): str,
        vol.Required("occurrence_id"): str,
    }
)
@callback
@_allowed
def ws_delete_occurrence(hass, connection, msg, manager: SinceManager) -> None:
    manager.delete_occurrence(msg["event_id"], msg["occurrence_id"])
    connection.send_result(msg["id"])


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in (
        ws_subscribe,
        ws_create,
        ws_update,
        ws_delete,
        ws_log,
        ws_update_occurrence,
        ws_delete_occurrence,
    ):
        websocket_api.async_register_command(hass, command)
