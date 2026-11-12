"""Base entity for Self-Hosted Game Servers Status."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    GameServer,
    GameServersCoordinator,
    device_identifier,
    device_model,
    device_url,
)


class GameServerEntity(CoordinatorEntity[GameServersCoordinator]):
    """An entity that belongs to one game server."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GameServersCoordinator,
        server_id: str,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._server_id = server_id
        entry_id = coordinator.config_entry.entry_id
        server = coordinator.data[server_id]
        self._attr_unique_id = f"{entry_id}_{server_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier(entry_id, server_id))},
            name=server.name,
            model=device_model(server.app_id),
            configuration_url=device_url(server.app_id),
        )

    @property
    def server(self) -> GameServer | None:
        """Return the latest data for this entity's server."""
        return self.coordinator.data.get(self._server_id)

    @property
    def available(self) -> bool:
        """Return True while the server is still being monitored."""
        return super().available and self.server is not None
