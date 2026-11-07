"""Storage and logic for Since events.

Data lives only in the integration's own private storage file
(.storage/since.data). Nothing is exposed as entities, attributes or
bus events, so other Home Assistant users cannot read it through the
normal state APIs. Access goes through the websocket commands in
websocket.py, which check the caller's user id.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any
import uuid

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import LOG_ACTION_PREFIX, STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 1  # seconds


def _new_id() -> str:
    return uuid.uuid4().hex


def _parse(value: str) -> datetime:
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Invalid date: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(parsed)


def _iso(value: datetime) -> str:
    return dt_util.as_utc(value).isoformat()


def add_interval(start: datetime, amount: int, unit: str) -> datetime:
    """Add a calendar interval, clamping month ends (Jan 31 + 1 month = Feb 28)."""
    local = dt_util.as_local(start)
    if unit == "hours":
        return start + timedelta(hours=amount)
    if unit == "days":
        result = local + timedelta(days=amount)
    elif unit == "weeks":
        result = local + timedelta(weeks=amount)
    else:
        months = amount * (12 if unit == "years" else 1)
        index = local.month - 1 + months
        year, month = local.year + index // 12, index % 12 + 1
        day = min(local.day, calendar.monthrange(year, month)[1])
        result = local.replace(year=year, month=month, day=day)
    # Aware arithmetic with zoneinfo keeps the wall clock time across DST changes.
    return dt_util.as_utc(result)


def last_occurrence(event: dict[str, Any]) -> datetime | None:
    dates = [_parse(o["date"]) for o in event["occurrences"]]
    return max(dates) if dates else None


def next_due(event: dict[str, Any]) -> datetime | None:
    interval = event.get("interval")
    last = last_occurrence(event)
    if not interval or last is None:
        return None
    return add_interval(last, interval["amount"], interval["unit"])


class SinceManager:
    """Owns the Since data and the reminder loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        allowed_users: list[str],
        notify_service: str | None,
        hide_names: bool,
    ) -> None:
        self.hass = hass
        self.allowed_users = set(allowed_users)
        self.notify_service = notify_service or None
        self.hide_names = hide_names
        # private=True writes the file with owner-only permissions.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self._data: dict[str, Any] = {"events": []}
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    # ---- lifecycle -------------------------------------------------------

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self._data = stored
        self._data.setdefault("events", [])
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._check_due, timedelta(minutes=1)
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                "mobile_app_notification_action", self._handle_notification_action
            )
        )
        self._check_due()

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._listeners.clear()
        await self._store.async_save(self._data)

    # ---- access ----------------------------------------------------------

    def is_allowed(self, user_id: str | None) -> bool:
        return user_id is not None and user_id in self.allowed_users

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def as_dict(self) -> dict[str, Any]:
        return {
            "events": [
                {k: v for k, v in event.items() if k != "notified_due"}
                for event in self._data["events"]
            ]
        }

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    # ---- events ----------------------------------------------------------

    def _event(self, event_id: str) -> dict[str, Any]:
        for event in self._data["events"]:
            if event["id"] == event_id:
                return event
        raise KeyError(event_id)

    @callback
    def create_event(
        self,
        name: str,
        interval: dict[str, Any] | None,
        notifies: bool,
        log_now: bool,
    ) -> dict[str, Any]:
        now = dt_util.utcnow()
        event = {
            "id": _new_id(),
            "name": name.strip(),
            "created_at": _iso(now),
            "interval": interval,
            "notifies": notifies,
            "occurrences": [],
        }
        if log_now:
            event["occurrences"].append({"id": _new_id(), "date": _iso(now), "note": ""})
        self._data["events"].append(event)
        self._changed()
        return event

    @callback
    def update_event(self, event_id: str, changes: dict[str, Any]) -> None:
        event = self._event(event_id)
        if "name" in changes:
            event["name"] = changes["name"].strip()
        if "interval" in changes:
            event["interval"] = changes["interval"]
        if "notifies" in changes:
            event["notifies"] = changes["notifies"]
        event.pop("notified_due", None)
        self._changed()

    @callback
    def delete_event(self, event_id: str) -> None:
        event = self._event(event_id)
        self._data["events"].remove(event)
        self._changed()

    # ---- occurrences -----------------------------------------------------

    def _sort(self, event: dict[str, Any]) -> None:
        event["occurrences"].sort(key=lambda o: _parse(o["date"]), reverse=True)

    @callback
    def log(self, event_id: str, date: str | None, note: str) -> dict[str, Any]:
        event = self._event(event_id)
        when = _parse(date) if date else dt_util.utcnow()
        occurrence = {"id": _new_id(), "date": _iso(when), "note": note.strip()}
        event["occurrences"].append(occurrence)
        self._sort(event)
        self._changed()
        return occurrence

    @callback
    def update_occurrence(
        self, event_id: str, occurrence_id: str, date: str | None, note: str | None
    ) -> None:
        event = self._event(event_id)
        for occurrence in event["occurrences"]:
            if occurrence["id"] == occurrence_id:
                if date is not None:
                    occurrence["date"] = _iso(_parse(date))
                if note is not None:
                    occurrence["note"] = note.strip()
                self._sort(event)
                self._changed()
                return
        raise KeyError(occurrence_id)

    @callback
    def delete_occurrence(self, event_id: str, occurrence_id: str) -> None:
        event = self._event(event_id)
        before = len(event["occurrences"])
        event["occurrences"] = [
            o for o in event["occurrences"] if o["id"] != occurrence_id
        ]
        if len(event["occurrences"]) == before:
            raise KeyError(occurrence_id)
        self._changed()

    # ---- reminders -------------------------------------------------------

    @callback
    def _check_due(self, _now: datetime | None = None) -> None:
        if not self.notify_service:
            return
        now = dt_util.utcnow()
        changed = False
        for event in self._data["events"]:
            if not event.get("notifies"):
                continue
            due = next_due(event)
            if due is None or due > now:
                continue
            key = _iso(due)
            if event.get("notified_due") == key:
                continue
            event["notified_due"] = key
            changed = True
            self.hass.async_create_task(self._send_notification(event))
        if changed:
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY)

    async def _send_notification(self, event: dict[str, Any]) -> None:
        assert self.notify_service
        domain, _, service = self.notify_service.partition(".")
        if not service:
            domain, service = "notify", domain
        message = (
            "A reminder is due" if self.hide_names else f"{event['name']} is due"
        )
        data: dict[str, Any] = {
            "title": "Since",
            "message": message,
            "data": {
                "tag": f"since-{event['id']}",
                "actions": [
                    {
                        "action": f"{LOG_ACTION_PREFIX}{event['id']}",
                        "title": "Log it now",
                        "authenticationRequired": True,
                    }
                ],
            },
        }
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Since could not send a reminder via %s", self.notify_service)

    @callback
    def _handle_notification_action(self, ha_event: Any) -> None:
        action = ha_event.data.get("action", "")
        if not action.startswith(LOG_ACTION_PREFIX):
            return
        # The mobile app fires this with the context of the phone's owner.
        if not self.is_allowed(ha_event.context.user_id):
            _LOGGER.warning("Ignored a Since notification action from another user")
            return
        try:
            self.log(action[len(LOG_ACTION_PREFIX) :], None, "")
        except KeyError:
            pass
