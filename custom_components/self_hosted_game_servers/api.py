"""Network access for the Self-Hosted Game Servers Status integration.

Three things live here:

* looking up which game servers Steam lists at a public IPv4 address,
* working out what that public address is, and
* asking a single game server for its live status (Valve's A2S query).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import socket
from typing import Any

import a2s
import aiohttp

from .const import (
    HTTP_TIMEOUT,
    PUBLIC_IP_URL,
    QUERY_ATTEMPTS,
    QUERY_TIMEOUT,
    STEAM_SERVERS_URL,
)

MAX_PORT = 65535


class GameServersError(Exception):
    """Base error for this integration."""


class CannotConnect(GameServersError):
    """A remote service could not be reached or gave an unusable answer."""


class InvalidAddress(GameServersError):
    """The address is not an IPv4 address or a resolvable host name."""


@dataclass(frozen=True, slots=True)
class SteamServer:
    """A game server as listed by Steam."""

    address: str
    query_port: int
    game_port: int | None
    app_id: int | None
    game_dir: str
    lan: bool


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Live status reported by a game server."""

    server_name: str
    game: str
    map_name: str | None
    players: int
    max_players: int
    version: str | None
    latency_ms: float
    password_protected: bool


def parse_manual_servers(value: str | None) -> list[tuple[str, int]]:
    """Parse ``host:port`` entries, one per line or comma separated.

    Raises ValueError when an entry is not a valid ``host:port`` pair.
    """
    servers: list[tuple[str, int]] = []
    for raw in (value or "").replace(",", "\n").splitlines():
        entry = raw.strip()
        if not entry:
            continue
        host, sep, port_text = entry.rpartition(":")
        host = host.strip()
        if not sep or not host or " " in host or not port_text.strip().isdecimal():
            raise ValueError(f"Not a host:port pair: {entry!r}")
        port = int(port_text)
        if not 0 < port <= MAX_PORT:
            raise ValueError(f"Port out of range: {entry!r}")
        if (host, port) not in servers:
            servers.append((host, port))
    return servers


async def async_detect_public_ip(session: aiohttp.ClientSession) -> str:
    """Return this network's current public IPv4 address."""
    try:
        async with asyncio.timeout(HTTP_TIMEOUT):
            response = await session.get(PUBLIC_IP_URL)
            response.raise_for_status()
            text = (await response.text()).strip()
        return str(ipaddress.IPv4Address(text))
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise CannotConnect("Could not detect the public IP address") from err


async def async_resolve_public_ip(session: aiohttp.ClientSession, address: str) -> str:
    """Turn the configured public address into an IPv4 address.

    An empty address means "detect it"; a host name (for example a dynamic
    DNS name) is resolved; an IPv4 address is returned as is.
    """
    address = address.strip()
    if not address:
        return await async_detect_public_ip(session)
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        pass
    else:
        if parsed.version != 4:
            raise InvalidAddress("Steam only lists IPv4 addresses")
        return str(parsed)
    try:
        results = await asyncio.get_running_loop().getaddrinfo(
            address, None, family=socket.AF_INET, type=socket.SOCK_DGRAM
        )
    except OSError as err:
        raise InvalidAddress(f"Could not resolve {address}") from err
    if not results:
        raise InvalidAddress(f"Could not resolve {address}")
    return str(results[0][4][0])


def _parse_steam_server(raw: dict[str, Any]) -> SteamServer | None:
    """Convert one entry of Steam's response, or None if it is unusable."""
    address, sep, port_text = str(raw.get("addr", "")).rpartition(":")
    if not sep or not port_text.isdecimal() or not 0 < int(port_text) <= MAX_PORT:
        return None
    game_port = raw.get("gameport")
    app_id = raw.get("appid")
    return SteamServer(
        address=address,
        query_port=int(port_text),
        game_port=game_port if isinstance(game_port, int) else None,
        app_id=app_id if isinstance(app_id, int) else None,
        game_dir=str(raw.get("gamedir") or "").strip(),
        lan=bool(raw.get("lan")),
    )


async def async_get_steam_servers(
    session: aiohttp.ClientSession, ip_address: str
) -> list[SteamServer]:
    """Return the game servers Steam lists at an IPv4 address."""
    try:
        async with asyncio.timeout(HTTP_TIMEOUT):
            response = await session.get(STEAM_SERVERS_URL, params={"addr": ip_address})
            response.raise_for_status()
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise CannotConnect("Could not reach the Steam server list") from err

    body = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(body, dict) or not body.get("success"):
        raise CannotConnect("Unexpected answer from the Steam server list")
    raw_servers = body.get("servers") or []
    if not isinstance(raw_servers, list):
        raise CannotConnect("Unexpected answer from the Steam server list")

    servers: list[SteamServer] = []
    for raw in raw_servers:
        if isinstance(raw, dict) and (server := _parse_steam_server(raw)):
            servers.append(server)
    return servers


async def async_query_server(host: str, port: int) -> ServerInfo | None:
    """Ask a game server for its live status; None means it did not answer."""
    for _ in range(QUERY_ATTEMPTS):
        try:
            info = await a2s.ainfo((host, port), timeout=QUERY_TIMEOUT)
        except (
            TimeoutError,
            OSError,
            a2s.BrokenMessageError,
            a2s.BufferExhaustedError,
        ):
            continue
        return ServerInfo(
            server_name=str(info.server_name),
            game=str(info.game),
            map_name=str(info.map_name) or None,
            players=int(info.player_count),
            max_players=int(info.max_players),
            version=str(getattr(info, "version", "") or "") or None,
            latency_ms=round(float(info.ping) * 1000, 1),
            password_protected=bool(info.password_protected),
        )
    return None
