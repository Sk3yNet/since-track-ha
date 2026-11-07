"""Since: private "time since" and reminder tracker for Home Assistant."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import websocket
from .const import (
    CARD_URL,
    CONF_ALLOWED_USERS,
    CONF_HIDE_NAMES,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
    VERSION,
)
from .manager import SinceManager

_REGISTERED = f"{DOMAIN}_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not hass.data.get(_REGISTERED):
        # The card's code is public like any frontend file; it holds no data.
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    CARD_URL,
                    str(Path(__file__).parent / "frontend" / "since-card.js"),
                    cache_headers=False,
                )
            ]
        )
        add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
        websocket.async_register(hass)
        hass.data[_REGISTERED] = True

    options = dict(entry.options or entry.data)
    manager = SinceManager(
        hass,
        options.get(CONF_ALLOWED_USERS, []),
        options.get(CONF_NOTIFY_SERVICE),
        options.get(CONF_HIDE_NAMES, False),
    )
    await manager.async_load()
    hass.data[DOMAIN] = manager
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager: SinceManager | None = hass.data.pop(DOMAIN, None)
    if manager is not None:
        await manager.async_unload()
    return True
