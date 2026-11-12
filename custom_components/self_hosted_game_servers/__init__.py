"""Self-Hosted Game Servers Status.

Monitors the game servers you host yourself: Steam is asked which servers
are listed at your public address, and each one is then polled directly
for its live status.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .coordinator import GameServersConfigEntry, GameServersCoordinator, storage_key

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def device_identifier(entry_id: str, server_id: str) -> str:
    """Return the device registry identifier for a server."""
    return f"{entry_id}_{server_id}"


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
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GameServersConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow a server's device to be deleted from the UI.

    A manually added server is removed through the options instead. A
    Steam-discovered server is forgotten; it comes back if Steam still
    lists it.
    """
    coordinator = entry.runtime_data
    prefix = f"{entry.entry_id}_"
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith(prefix):
            continue
        server_id = identifier.removeprefix(prefix)
        if coordinator.is_manual(server_id):
            return False
        await coordinator.async_forget(server_id)
        # Reload so the server gets fresh entities if it is discovered again.
        hass.config_entries.async_schedule_reload(entry.entry_id)
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
