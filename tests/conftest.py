"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.self_hosted_game_servers.const import (
    CONF_LOCAL_HOST,
    CONF_PUBLIC_ADDRESS,
    DOMAIN,
    PUBLIC_IP_URL,
    STEAM_SERVERS_URL,
)

PUBLIC_IP = "64.94.100.204"
LOCAL_HOST = "192.168.1.50"
QUERY_PORT = 15637


def steam_payload(*servers: dict[str, Any]) -> dict[str, Any]:
    """Build a Steam GetServersAtAddress response."""
    return {"response": {"success": True, "servers": list(servers)}}


def steam_server(
    port: int = QUERY_PORT, game_dir: str = "Enshrouded"
) -> dict[str, Any]:
    """Build one server entry of a Steam response."""
    return {
        "addr": f"{PUBLIC_IP}:{port}",
        "steamid": "90297309447661573",
        "appid": 1203620,
        "gamedir": game_dir,
        "region": -1,
        "secure": False,
        "lan": False,
        "gameport": port,
        "specport": 0,
    }


def a2s_info(**overrides: Any) -> SimpleNamespace:
    """Build what a2s.ainfo returns for a running server."""
    values: dict[str, Any] = {
        "server_name": "Ecto World",
        "game": "Enshrouded",
        "map_name": "",
        "player_count": 2,
        "max_players": 16,
        "version": "0.9.0.1",
        "ping": 0.0123,
        "password_protected": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of the integration under test."""


@pytest.fixture
def mock_a2s() -> Generator[AsyncMock]:
    """Mock the A2S status query; servers answer by default."""
    with patch(
        "custom_components.self_hosted_game_servers.api.a2s.ainfo",
        new_callable=AsyncMock,
        return_value=a2s_info(),
    ) as mock:
        yield mock


@pytest.fixture
def mock_steam(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Mock Steam listing one Enshrouded server, and the public IP lookup."""
    aioclient_mock.get(STEAM_SERVERS_URL, json=steam_payload(steam_server()))
    aioclient_mock.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry using auto-detection and a LAN host."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=LOCAL_HOST,
        unique_id=PUBLIC_IP,
        data={CONF_PUBLIC_ADDRESS: "", CONF_LOCAL_HOST: LOCAL_HOST},
    )
