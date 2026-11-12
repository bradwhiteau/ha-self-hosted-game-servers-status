"""Sensors for Self-Hosted Game Servers Status."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import ServerInfo
from .coordinator import GameServersConfigEntry
from .entity import GameServerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GameServerSensorDescription(SensorEntityDescription):
    """Describes a game server sensor."""

    value_fn: Callable[[ServerInfo], StateType]


SENSORS: tuple[GameServerSensorDescription, ...] = (
    GameServerSensorDescription(
        key="players",
        translation_key="players",
        native_unit_of_measurement="players",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda info: info.players,
    ),
    GameServerSensorDescription(
        key="max_players",
        translation_key="max_players",
        native_unit_of_measurement="players",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda info: info.max_players,
    ),
    GameServerSensorDescription(
        key="latency",
        translation_key="latency",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda info: info.latency_ms,
    ),
    GameServerSensorDescription(
        key="server_name",
        translation_key="server_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda info: info.server_name or None,
    ),
    GameServerSensorDescription(
        key="map",
        translation_key="map",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda info: info.map_name,
    ),
    GameServerSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda info: info.version,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GameServersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors, including for servers discovered later."""
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _async_add_new() -> None:
        new_ids = set(coordinator.data) - added
        if not new_ids:
            return
        added.update(new_ids)
        async_add_entities(
            GameServerSensor(coordinator, server_id, description)
            for server_id in sorted(new_ids)
            for description in SENSORS
        )

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class GameServerSensor(GameServerEntity, SensorEntity):
    """A sensor for one game server; unavailable while the server is down."""

    entity_description: GameServerSensorDescription

    @property
    def available(self) -> bool:
        """Return True while the server is answering status queries."""
        return super().available and self.server is not None and self.server.online

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if (server := self.server) is None or server.info is None:
            return None
        return self.entity_description.value_fn(server.info)
