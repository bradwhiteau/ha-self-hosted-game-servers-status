"""Tests for the network layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import a2s
import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.self_hosted_game_servers.api import (
    CannotConnect,
    InvalidAddress,
    async_detect_public_ip,
    async_get_steam_servers,
    async_query_server,
    async_resolve_public_ip,
    parse_manual_servers,
)
from custom_components.self_hosted_game_servers.const import (
    PUBLIC_IP_URL,
    QUERY_ATTEMPTS,
    STEAM_SERVERS_URL,
)

from .conftest import PUBLIC_IP, QUERY_PORT, a2s_info, steam_payload, steam_server


def test_parse_manual_servers() -> None:
    """Entries are split on lines and commas, trimmed and de-duplicated."""
    text = " 192.168.1.50:27015 \n\nhost.example:2456, 192.168.1.50:27015"
    assert parse_manual_servers(text) == [
        ("192.168.1.50", 27015),
        ("host.example", 2456),
    ]
    assert parse_manual_servers(None) == []
    assert parse_manual_servers("") == []


@pytest.mark.parametrize(
    "text", ["192.168.1.50", "host:abc", ":27015", "host:0", "host:70000", "a b:1"]
)
def test_parse_manual_servers_invalid(text: str) -> None:
    """Anything that is not host:port is rejected."""
    with pytest.raises(ValueError, match=r"host:port|Port out of range"):
        parse_manual_servers(text)


async def test_get_steam_servers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Steam response is parsed; unusable entries are skipped."""
    aioclient_mock.get(
        STEAM_SERVERS_URL,
        json=steam_payload(steam_server(), {"addr": "no-port"}, "junk"),
    )
    servers = await async_get_steam_servers(async_get_clientsession(hass), PUBLIC_IP)

    assert len(servers) == 1
    assert servers[0].address == PUBLIC_IP
    assert servers[0].query_port == QUERY_PORT
    assert servers[0].app_id == 1203620
    assert servers[0].game_dir == "Enshrouded"
    assert servers[0].lan is False
    assert aioclient_mock.mock_calls[0][1].query["addr"] == PUBLIC_IP


async def test_get_steam_servers_none_listed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Steam's "no servers" answer is an empty list, not an error."""
    aioclient_mock.get(
        STEAM_SERVERS_URL,
        json={"response": {"success": True, "servers": [], "message": "none"}},
    )
    assert await async_get_steam_servers(async_get_clientsession(hass), PUBLIC_IP) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 500},
        {"exc": aiohttp.ClientError},
        {"exc": TimeoutError},
        {"text": "not json"},
        {"json": []},
        {"json": {"response": {"success": False}}},
        {"json": {"response": {"success": True, "servers": "x"}}},
    ],
)
async def test_get_steam_servers_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict
) -> None:
    """Transport and format problems all surface as CannotConnect."""
    aioclient_mock.get(STEAM_SERVERS_URL, **kwargs)
    with pytest.raises(CannotConnect):
        await async_get_steam_servers(async_get_clientsession(hass), PUBLIC_IP)


async def test_detect_public_ip(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The public IP is read from the lookup service."""
    aioclient_mock.get(PUBLIC_IP_URL, text=f"{PUBLIC_IP}\n")
    assert await async_detect_public_ip(async_get_clientsession(hass)) == PUBLIC_IP


@pytest.mark.parametrize(
    "kwargs", [{"status": 503}, {"exc": aiohttp.ClientError}, {"text": "<html>"}]
)
async def test_detect_public_ip_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict
) -> None:
    """A failed or nonsense lookup is CannotConnect."""
    aioclient_mock.get(PUBLIC_IP_URL, **kwargs)
    with pytest.raises(CannotConnect):
        await async_detect_public_ip(async_get_clientsession(hass))


async def test_resolve_public_ip(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Empty means detect, an IPv4 address passes through, IPv6 is refused."""
    aioclient_mock.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    session = async_get_clientsession(hass)

    assert await async_resolve_public_ip(session, "  ") == PUBLIC_IP
    assert await async_resolve_public_ip(session, " 198.51.100.7 ") == "198.51.100.7"
    with pytest.raises(InvalidAddress):
        await async_resolve_public_ip(session, "2001:db8::1")


async def test_resolve_public_ip_host_name(hass: HomeAssistant) -> None:
    """A host name is resolved to IPv4; a failed lookup is InvalidAddress."""
    session = async_get_clientsession(hass)
    answer = [(2, 2, 17, "", (PUBLIC_IP, 0))]
    with patch.object(hass.loop, "getaddrinfo", AsyncMock(return_value=answer)):
        assert await async_resolve_public_ip(session, "home.example.org") == PUBLIC_IP
    with (
        patch.object(hass.loop, "getaddrinfo", AsyncMock(side_effect=OSError)),
        pytest.raises(InvalidAddress),
    ):
        await async_resolve_public_ip(session, "nope.invalid")
    with (
        patch.object(hass.loop, "getaddrinfo", AsyncMock(return_value=[])),
        pytest.raises(InvalidAddress),
    ):
        await async_resolve_public_ip(session, "empty.invalid")


async def test_query_server(mock_a2s: AsyncMock) -> None:
    """A status answer is converted, with latency in milliseconds."""
    info = await async_query_server("192.168.1.50", QUERY_PORT)

    assert info is not None
    assert info.server_name == "Ecto World"
    assert info.game == "Enshrouded"
    assert info.map_name is None
    assert info.players == 2
    assert info.max_players == 16
    assert info.version == "0.9.0.1"
    assert info.latency_ms == 12.3
    assert info.password_protected is True
    assert mock_a2s.await_args.args[0] == ("192.168.1.50", QUERY_PORT)


async def test_query_server_without_version(mock_a2s: AsyncMock) -> None:
    """Older (GoldSrc) servers report no version."""
    answer = a2s_info(map_name="de_dust2")
    del answer.version
    mock_a2s.return_value = answer

    info = await async_query_server("192.168.1.50", 27015)
    assert info is not None
    assert info.version is None
    assert info.map_name == "de_dust2"


async def test_query_server_retries_once(mock_a2s: AsyncMock) -> None:
    """One lost packet does not count as offline."""
    mock_a2s.side_effect = [TimeoutError, a2s_info()]
    assert await async_query_server("192.168.1.50", QUERY_PORT) is not None
    assert mock_a2s.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError,
        ConnectionRefusedError,
        a2s.BrokenMessageError,
        a2s.BufferExhaustedError,
    ],
)
async def test_query_server_offline(
    mock_a2s: AsyncMock, error: type[Exception]
) -> None:
    """A server that never answers properly is reported as None."""
    mock_a2s.side_effect = error
    assert await async_query_server("192.168.1.50", QUERY_PORT) is None
    assert mock_a2s.await_count == QUERY_ATTEMPTS
