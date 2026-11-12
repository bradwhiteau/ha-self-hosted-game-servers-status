"""Binary sensors for Self-Hosted Game Servers Status."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SOURCE_STEAM
from .coordinator import GameServer, GameServersConfigEntry
from .entity import GameServerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GameServerBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a game server binary sensor."""

    is_on_fn: Callable[[GameServer], bool | None]
    steam_only: bool = False


BINARY_SENSORS: tuple[GameServerBinarySensorDescription, ...] = (
    GameServerBinarySensorDescription(
        key="status",
        translation_key="status",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda server: server.online,
    ),
    GameServerBinarySensorDescription(
        key="listed_on_steam",
        translation_key="listed_on_steam",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda server: server.listed,
        steam_only=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GameServersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors, including for servers discovered later."""
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _async_add_new() -> None:
        new_ids = set(coordinator.data) - added
        if not new_ids:
            return
        added.update(new_ids)
        async_add_entities(
            GameServerBinarySensor(coordinator, server_id, description)
            for server_id in sorted(new_ids)
            for description in BINARY_SENSORS
            if not description.steam_only
            or coordinator.data[server_id].source == SOURCE_STEAM
        )

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class GameServerBinarySensor(GameServerEntity, BinarySensorEntity):
    """A binary sensor for one game server."""

    entity_description: GameServerBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the sensor state."""
        if (server := self.server) is None:
            return None
        return self.entity_description.is_on_fn(server)
