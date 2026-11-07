"""Config and options flow for Since."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_ALLOWED_USERS, CONF_HIDE_NAMES, CONF_NOTIFY_SERVICE, DOMAIN


async def _user_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    users = await hass.auth.async_get_users()
    return [
        SelectOptionDict(value=user.id, label=user.name or user.id)
        for user in users
        if not user.system_generated and user.is_active
    ]


def _notify_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    services = hass.services.async_services().get("notify", {})
    return [
        SelectOptionDict(value=f"notify.{name}", label=f"notify.{name}")
        for name in sorted(services)
        if name.startswith("mobile_app_")
    ]


async def _schema(hass: HomeAssistant, current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ALLOWED_USERS, default=current.get(CONF_ALLOWED_USERS, [])
            ): SelectSelector(
                SelectSelectorConfig(
                    options=await _user_options(hass),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_NOTIFY_SERVICE,
                description={"suggested_value": current.get(CONF_NOTIFY_SERVICE)},
            ): SelectSelector(
                SelectSelectorConfig(
                    options=_notify_options(hass),
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_HIDE_NAMES, default=current.get(CONF_HIDE_NAMES, False)
            ): BooleanSelector(),
        }
    )


class SinceConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ALLOWED_USERS):
                errors[CONF_ALLOWED_USERS] = "no_users"
            else:
                return self.async_create_entry(title="Since", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=await _schema(self.hass, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SinceOptionsFlow()


class SinceOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ALLOWED_USERS):
                errors[CONF_ALLOWED_USERS] = "no_users"
            else:
                return self.async_create_entry(data=user_input)
        current = dict(self.config_entry.options or self.config_entry.data)
        return self.async_show_form(
            step_id="init",
            data_schema=await _schema(self.hass, user_input or current),
            errors=errors,
        )
