"""Data update coordinator for Self-Hosted Game Servers Status."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
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
    # server was added manually.
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


def device_identifier(entry_id: str, server_id: str) -> str:
    """Return the device registry identifier for a server."""
    return f"{entry_id}_{server_id}"


def device_model(app_id: int | None) -> str | None:
    """Return the device model shown for a Steam app."""
    return f"Steam app {app_id}" if app_id else None


def device_url(app_id: int | None) -> str | None:
    """Return the Steam store page for a Steam app."""
    return f"https://store.steampowered.com/app/{app_id}" if app_id else None


def storage_key(entry_id: str) -> str:
    """Return the storage key holding an entry's discovered servers."""
    return f"{DOMAIN}.{entry_id}"


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, storage_key(entry_id))


async def async_forget_stored_server(
    hass: HomeAssistant, entry_id: str, server_id: str
) -> None:
    """Forget a Steam-discovered server while its entry is not loaded."""
    store = _store(hass, entry_id)
    stored = await store.async_load()
    if not stored:
        return
    servers = [
        record
        for record in stored.get("servers", [])
        if steam_server_id(int(record["port"])) != server_id
    ]
    if len(servers) != len(stored.get("servers", [])):
        await store.async_save({**stored, "servers": servers})


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    """Delete an entry's remembered servers."""
    await _store(hass, entry_id).async_remove()


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
        self._store = _store(hass, entry.entry_id)
        self._public_address: str = entry.data.get(CONF_PUBLIC_ADDRESS) or ""
        self._local_host: str | None = entry.data.get(CONF_LOCAL_HOST) or None
        self._manual = parse_manual_servers(entry.options.get(CONF_MANUAL_SERVERS))
        # Servers Steam has listed at some point, by query port. They are
        # remembered so a server that is switched off still shows as not
        # running (rather than vanishing) after Home Assistant restarts.
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
        """Pick a device name, adding the port if another server has it."""
        name = server.game_dir or f"Game server {server.query_port}"
        if any(
            record["name"] == name
            for port, record in self._known.items()
            if port != server.query_port
        ):
            name = f"{name} ({server.query_port})"
        return name

    def _update_device(self, port: int) -> None:
        """Bring a server's device in line with the game now on its port.

        A name the user gave the device is kept: Home Assistant stores it
        separately and shows it in place of this one.
        """
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        identifier = (DOMAIN, device_identifier(entry_id, steam_server_id(port)))
        # Searched within this entry's devices: the registry's lookup by
        # identifier changed in Home Assistant 2026.10.
        for device in dr.async_entries_for_config_entry(registry, entry_id):
            if identifier in device.identifiers:
                record = self._known[port]
                registry.async_update_device(
                    device.id,
                    name=record["name"],
                    model=device_model(record["app_id"]),
                    configuration_url=device_url(record["app_id"]),
                )

    def _track_public_ip(self, public_ip: str) -> None:
        """Keep the entry's unique id equal to the current public address."""
        entry = self.config_entry
        if entry.unique_id == public_ip:
            return
        other = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, public_ip
        )
        if other is None:
            self.hass.config_entries.async_update_entry(entry, unique_id=public_ip)

    async def _async_discover(self) -> None:
        """Ask Steam which servers are listed at the public address."""
        public_ip = await async_resolve_public_ip(self._session, self._public_address)
        steam_servers = await async_get_steam_servers(self._session, public_ip)

        changed = public_ip != self._public_ip
        self._public_ip = public_ip
        self._track_public_ip(public_ip)
        for server in steam_servers:
            record = self._known.get(server.query_port)
            if record is not None and (
                server.app_id is None or server.app_id == record["app_id"]
            ):
                continue
            # A new server, or a different game now using a known port.
            self._known[server.query_port] = {
                "name": self._name_for(server),
                "app_id": server.app_id,
            }
            if record is not None:
                self._update_device(server.query_port)
            changed = True
        self._listed = {server.query_port for server in steam_servers}
        if changed:
            await self._async_save()

    def _targets(self) -> list[GameServer]:
        """Return every server to poll, without status yet."""
        steam_host = self._local_host or self._public_ip
        manual = set(self._manual)
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
            # A server that was also added manually is monitored as the
            # manual one, so it never shows up twice.
            if (steam_host, port) not in manual
        ]
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
        results = await asyncio.gather(
            *(self._async_query(target) for target in targets),
            return_exceptions=True,
        )
        data: dict[str, GameServer] = {}
        for target, result in zip(targets, results, strict=True):
            info: ServerInfo | None
            if isinstance(result, Exception):
                # One server must never take the others down with it.
                _LOGGER.debug(
                    "Status query for %s failed: %r", target.server_id, result
                )
                info = None
            elif isinstance(result, BaseException):
                raise result
            else:
                info = result
            data[target.server_id] = GameServer(
                server_id=target.server_id,
                host=target.host,
                port=target.port,
                source=target.source,
                name=target.name,
                app_id=target.app_id,
                listed=target.listed,
                info=info,
            )
        return data

    @staticmethod
    async def _async_query(target: GameServer) -> ServerInfo | None:
        if target.host is None:
            return None
        return await async_query_server(target.host, target.port)
