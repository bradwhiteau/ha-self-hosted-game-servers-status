"""Self-Hosted Game Servers Status.

Monitors the game servers you host yourself: Steam is asked which servers
are listed at your public address, and each one is then polled directly
for its live status.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .api import parse_manual_servers
from .const import CONF_MANUAL_SERVERS, DOMAIN
from .coordinator import (
    GameServersConfigEntry,
    GameServersCoordinator,
    async_forget_stored_server,
    async_remove_store,
    device_identifier,
    manual_server_id,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GameServersConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = GameServersCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _async_remove_stale_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GameServersConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: GameServersConfigEntry
) -> None:
    """Delete the remembered servers when the entry is removed."""
    await async_remove_store(hass, entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GameServersConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow a server's device to be deleted from the UI.

    A manually added server is removed through the options instead. A
    Steam-discovered server is forgotten; it comes back if Steam still
    lists it. This also works while the entry is not loaded.
    """
    prefix = f"{entry.entry_id}_"
    manual_ids = {
        manual_server_id(host, port)
        for host, port in parse_manual_servers(entry.options.get(CONF_MANUAL_SERVERS))
    }
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith(prefix):
            continue
        server_id = identifier.removeprefix(prefix)
        if server_id in manual_ids:
            return False
        if entry.state is ConfigEntryState.LOADED:
            await entry.runtime_data.async_forget(server_id)
            # Reload so the server gets fresh entities if it is found again.
            hass.config_entries.async_schedule_reload(entry.entry_id)
        else:
            await async_forget_stored_server(hass, entry.entry_id, server_id)
    return True


@callback
def _async_remove_stale_devices(
    hass: HomeAssistant,
    entry: GameServersConfigEntry,
    coordinator: GameServersCoordinator,
) -> None:
    """Remove devices for servers that are no longer monitored."""
    current = {
        (DOMAIN, device_identifier(entry.entry_id, server_id))
        for server_id in coordinator.data
    }
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not device.identifiers & current:
            device_registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
