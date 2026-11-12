"""Diagnostics for Self-Hosted Game Servers Status."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_LOCAL_HOST, CONF_MANUAL_SERVERS, CONF_PUBLIC_ADDRESS
from .coordinator import GameServersConfigEntry

# Addresses and server names can identify a household, so they never leave
# Home Assistant in a diagnostics download.
TO_REDACT = {
    CONF_PUBLIC_ADDRESS,
    CONF_LOCAL_HOST,
    CONF_MANUAL_SERVERS,
    "host",
    "name",
    "server_id",
    "server_name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GameServersConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "servers": [
            async_redact_data(asdict(server), TO_REDACT)
            for server in coordinator.data.values()
        ],
    }
