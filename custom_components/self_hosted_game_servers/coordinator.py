"""Data update coordinator for Self-Hosted Game Servers Status."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GameServersError,
    ServerInfo,
    SteamServer,
    async_get_steam_servers,
    async_query_server,
    async_resolve_public_ip,
    parse_manual_servers,
)
from .const import (
    CONF_LOCAL_HOST,
    CONF_MANUAL_SERVERS,
    CONF_PUBLIC_ADDRESS,
    DOMAIN,
    POLLS_PER_DISCOVERY,
    SOURCE_MANUAL,
    SOURCE_STEAM,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type GameServersConfigEntry = ConfigEntry[GameServersCoordinator]


@dataclass(frozen=True, slots=True)
class GameServer:
    """One monitored game server and its latest status."""

    server_id: str
    host: str | None
    port: int
    source: str
    name: str
    app_id: int | None
    # True/False once Steam has answered; None when that is unknown or the
    # server was added by hand.
    listed: bool | None
    info: ServerInfo | None = None

    @property
    def online(self) -> bool:
        """Return True when the server answered its last status query."""
        return self.info is not None


def steam_server_id(port: int) -> str:
    """Return the id of a Steam-discovered server."""
    return f"{SOURCE_STEAM}_{port}"


def manual_server_id(host: str, port: int) -> str:
    """Return the id of a manually added server."""
    return f"{SOURCE_MANUAL}_{host}_{port}"


def storage_key(entry_id: str) -> str:
    """Return the storage key holding an entry's discovered servers."""
    return f"{DOMAIN}.{entry_id}"


class GameServersCoordinator(DataUpdateCoordinator[dict[str, GameServer]]):
    """Discover game servers through Steam and poll each one for status."""

    config_entry: GameServersConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GameServersConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._session = async_get_clientsession(hass)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        self._public_address: str = entry.data.get(CONF_PUBLIC_ADDRESS) or ""
        self._local_host: str | None = entry.data.get(CONF_LOCAL_HOST) or None
        self._manual = parse_manual_servers(entry.options.get(CONF_MANUAL_SERVERS))
        # Servers Steam has listed at some point, by query port. They are
        # remembered so a server that is switched off still shows as offline
        # (rather than vanishing) after Home Assistant restarts.
        self._known: dict[int, dict[str, Any]] = {}
        self._listed: set[int] | None = None
        self._public_ip: str | None = None
        self._polls_until_discovery = 0

    async def _async_setup(self) -> None:
        """Load the servers remembered from earlier runs."""
        stored = await self._store.async_load() or {}
        self._public_ip = stored.get("public_ip")
        for record in stored.get("servers", []):
            self._known[int(record["port"])] = {
                "name": record["name"],
                "app_id": record.get("app_id"),
            }

    def is_manual(self, server_id: str) -> bool:
        """Return True if the id belongs to a configured manual server."""
        return any(
            manual_server_id(host, port) == server_id for host, port in self._manual
        )

    async def async_forget(self, server_id: str) -> None:
        """Forget a Steam-discovered server (its device was deleted)."""
        for port in list(self._known):
            if steam_server_id(port) == server_id:
                del self._known[port]
                await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "public_ip": self._public_ip,
                "servers": [
                    {"port": port, **record} for port, record in self._known.items()
                ],
            }
        )

    def _name_for(self, server: SteamServer) -> str:
        """Pick a device name, adding the port if the game is already present."""
        name = server.game_dir or f"Game server {server.query_port}"
        if any(record["name"] == name for record in self._known.values()):
            name = f"{name} ({server.query_port})"
        return name

    async def _async_discover(self) -> None:
        """Ask Steam which servers are listed at the public address."""
        public_ip = await async_resolve_public_ip(self._session, self._public_address)
        steam_servers = await async_get_steam_servers(self._session, public_ip)

        changed = public_ip != self._public_ip
        self._public_ip = public_ip
        for server in steam_servers:
            if server.query_port not in self._known:
                self._known[server.query_port] = {
                    "name": self._name_for(server),
                    "app_id": server.app_id,
                }
                changed = True
        self._listed = {server.query_port for server in steam_servers}
        if changed:
            await self._async_save()

    def _targets(self) -> list[GameServer]:
        """Return every server to poll, without status yet."""
        steam_host = self._local_host or self._public_ip
        targets = [
            GameServer(
                server_id=steam_server_id(port),
                host=steam_host,
                port=port,
                source=SOURCE_STEAM,
                name=record["name"],
                app_id=record.get("app_id"),
                listed=None if self._listed is None else port in self._listed,
            )
            for port, record in self._known.items()
        ]
        polled = {(target.host, target.port) for target in targets}
        targets.extend(
            GameServer(
                server_id=manual_server_id(host, port),
                host=host,
                port=port,
                source=SOURCE_MANUAL,
                name=f"{host}:{port}",
                app_id=None,
                listed=None,
            )
            for host, port in self._manual
            if (host, port) not in polled
        )
        return targets

    async def _async_update_data(self) -> dict[str, GameServer]:
        """Refresh the server list when due, then poll every server."""
        if self._polls_until_discovery <= 0:
            try:
                await self._async_discover()
            except GameServersError as err:
                # Retried on the next poll. Servers already known keep being
                # polled directly; only their "listed on Steam" state is lost.
                self._listed = None
                if not self._known and not self._manual:
                    raise UpdateFailed(str(err)) from err
                _LOGGER.debug("Steam discovery failed: %s", err)
            else:
                self._polls_until_discovery = POLLS_PER_DISCOVERY
        self._polls_until_discovery -= 1

        targets = self._targets()
        infos = await asyncio.gather(*(self._async_query(target) for target in targets))
        return {
            target.server_id: GameServer(
                server_id=target.server_id,
                host=target.host,
                port=target.port,
                source=target.source,
                name=target.name,
                app_id=target.app_id,
                listed=target.listed,
                info=info,
            )
            for target, info in zip(targets, infos, strict=True)
        }

    @staticmethod
    async def _async_query(target: GameServer) -> ServerInfo | None:
        if target.host is None:
            return None
        return await async_query_server(target.host, target.port)
