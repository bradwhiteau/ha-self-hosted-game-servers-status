"""Config flow for Self-Hosted Game Servers Status."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig
import voluptuous as vol

from .api import (
    CannotConnect,
    InvalidAddress,
    NotPublicAddress,
    async_get_steam_servers,
    async_resolve_public_ip,
    parse_manual_servers,
    validate_host,
)
from .const import CONF_LOCAL_HOST, CONF_MANUAL_SERVERS, CONF_PUBLIC_ADDRESS, DOMAIN
from .coordinator import GameServersConfigEntry, async_remove_store

DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PUBLIC_ADDRESS): TextSelector(),
        vol.Optional(CONF_LOCAL_HOST): TextSelector(),
    }
)
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MANUAL_SERVERS): TextSelector(
            TextSelectorConfig(multiline=True)
        ),
    }
)


def _normalise(user_input: dict[str, Any]) -> dict[str, str]:
    return {
        CONF_PUBLIC_ADDRESS: (user_input.get(CONF_PUBLIC_ADDRESS) or "").strip(),
        CONF_LOCAL_HOST: (user_input.get(CONF_LOCAL_HOST) or "").strip(),
    }


def _title(data: dict[str, str]) -> str:
    return data[CONF_LOCAL_HOST] or data[CONF_PUBLIC_ADDRESS] or "Game servers"


async def _async_validate(
    hass: HomeAssistant, data: dict[str, str]
) -> tuple[str | None, dict[str, str]]:
    """Check the addresses; return the public IP and any form errors."""
    if data[CONF_LOCAL_HOST]:
        try:
            data[CONF_LOCAL_HOST] = validate_host(data[CONF_LOCAL_HOST])
        except InvalidAddress:
            return None, {CONF_LOCAL_HOST: "invalid_host"}
    session = async_get_clientsession(hass)
    try:
        public_ip = await async_resolve_public_ip(session, data[CONF_PUBLIC_ADDRESS])
    except NotPublicAddress:
        return None, {CONF_PUBLIC_ADDRESS: "not_public"}
    except InvalidAddress:
        return None, {CONF_PUBLIC_ADDRESS: "invalid_address"}
    except CannotConnect:
        return None, {"base": "cannot_detect_ip"}
    try:
        await async_get_steam_servers(session, public_ip)
    except CannotConnect:
        return None, {"base": "cannot_connect"}
    return public_ip, {}


class GameServersConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow.

    One entry per public address: Steam lists every server at that address,
    so a second entry for it would only duplicate the same devices.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: GameServersConfigEntry,
    ) -> GameServersOptionsFlow:
        """Return the options flow."""
        return GameServersOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup started by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise(user_input)
            public_ip, errors = await _async_validate(self.hass, data)
            if public_ip is not None:
                await self.async_set_unique_id(public_ip)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=_title(data), data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the addresses of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise(user_input)
            public_ip, errors = await _async_validate(self.hass, data)
            if public_ip is not None:
                other = self.hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, public_ip
                )
                if other is not None and other.entry_id != entry.entry_id:
                    return self.async_abort(reason="already_configured")
                if public_ip != entry.unique_id:
                    # A different network: the servers remembered for the old
                    # one would only show as permanently not running.
                    await async_remove_store(self.hass, entry.entry_id)
                return self.async_update_reload_and_abort(
                    entry, unique_id=public_ip, title=_title(data), data=data
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                DATA_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )


class GameServersOptionsFlow(OptionsFlowWithReload):
    """Manage servers that Steam does not list."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            text = (user_input.get(CONF_MANUAL_SERVERS) or "").strip()
            try:
                parse_manual_servers(text)
            except ValueError:
                errors[CONF_MANUAL_SERVERS] = "invalid_servers"
            else:
                return self.async_create_entry(data={CONF_MANUAL_SERVERS: text})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )
