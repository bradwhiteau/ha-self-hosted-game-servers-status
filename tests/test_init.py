"""Tests for setup, polling, entities and device handling."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.self_hosted_game_servers import async_remove_config_entry_device
from custom_components.self_hosted_game_servers.const import (
    CONF_LOCAL_HOST,
    CONF_MANUAL_SERVERS,
    CONF_PUBLIC_ADDRESS,
    DOMAIN,
    POLLS_PER_DISCOVERY,
    PUBLIC_IP_URL,
    STEAM_SERVERS_URL,
    UPDATE_INTERVAL,
)
from custom_components.self_hosted_game_servers.coordinator import storage_key
from custom_components.self_hosted_game_servers.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    LOCAL_HOST,
    PUBLIC_IP,
    QUERY_PORT,
    a2s_info,
    steam_payload,
    steam_server,
)

STEAM_ID = f"steam_{QUERY_PORT}"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant, polls: int = 1) -> None:
    """Let the given number of status polls happen."""
    for _ in range(polls):
        async_fire_time_changed(
            hass, dt_util.utcnow() + UPDATE_INTERVAL + timedelta(seconds=1)
        )
        await hass.async_block_till_done(wait_background_tasks=True)


def _state(
    hass: HomeAssistant, entry: MockConfigEntry, server_id: str, key: str
) -> Any:
    """Return an entity's state by its unique id (None if never created)."""
    platform = "binary_sensor" if key in ("status", "listed_on_steam") else "sensor"
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{server_id}_{key}"
    )
    if entity_id is None:
        return None
    state = hass.states.get(entity_id)
    return state.state if state else None


async def test_setup_creates_device_and_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
) -> None:
    """A Steam-listed server becomes a device with the full set of entities."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device(
        {(DOMAIN, f"{config_entry.entry_id}_{STEAM_ID}")}
    )
    assert device is not None
    assert device.name == "Enshrouded"
    assert device.model == "Steam app 1203620"

    assert hass.states.get("binary_sensor.enshrouded_status").state == STATE_ON
    assert _state(hass, config_entry, STEAM_ID, "listed_on_steam") == STATE_ON
    assert _state(hass, config_entry, STEAM_ID, "players") == "2"
    assert _state(hass, config_entry, STEAM_ID, "max_players") == "16"
    assert _state(hass, config_entry, STEAM_ID, "latency") == "12.3"
    assert _state(hass, config_entry, STEAM_ID, "server_name") == "Ecto World"
    assert _state(hass, config_entry, STEAM_ID, "version") == "0.9.0.1"
    assert _state(hass, config_entry, STEAM_ID, "map") == STATE_UNKNOWN

    # The game is checked on the LAN host, at the port Steam reported.
    assert mock_a2s.await_args.args[0] == (LOCAL_HOST, QUERY_PORT)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_without_local_host_queries_public_ip(
    hass: HomeAssistant, mock_steam: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """With no LAN host, the public address is queried."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_PUBLIC_ADDRESS: PUBLIC_IP, CONF_LOCAL_HOST: ""}
    )
    await _setup(hass, entry)
    assert mock_a2s.await_args.args[0] == (PUBLIC_IP, QUERY_PORT)


async def test_server_goes_offline_and_recovers(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
) -> None:
    """Status turns off, detail sensors go unavailable, then both recover."""
    await _setup(hass, config_entry)

    mock_a2s.side_effect = TimeoutError
    await _tick(hass)
    assert _state(hass, config_entry, STEAM_ID, "status") == STATE_OFF
    assert _state(hass, config_entry, STEAM_ID, "players") == STATE_UNAVAILABLE

    mock_a2s.side_effect = None
    mock_a2s.return_value = a2s_info(player_count=3)
    await _tick(hass)
    assert _state(hass, config_entry, STEAM_ID, "status") == STATE_ON
    assert _state(hass, config_entry, STEAM_ID, "players") == "3"


async def test_new_server_is_added_while_running(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
) -> None:
    """A game started later appears at the next discovery, named apart."""
    await _setup(hass, config_entry)

    mock_steam.clear_requests()
    mock_steam.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    mock_steam.get(
        STEAM_SERVERS_URL,
        json=steam_payload(
            steam_server(), steam_server(2457, "valheim"), steam_server(15647)
        ),
    )
    await _tick(hass)
    assert _state(hass, config_entry, "steam_2457", "status") is None

    await _tick(hass, POLLS_PER_DISCOVERY - 1)
    assert _state(hass, config_entry, "steam_2457", "status") == STATE_ON
    assert _state(hass, config_entry, "steam_15647", "status") == STATE_ON

    registry = dr.async_get(hass)
    names = {
        device.name
        for device in dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    }
    assert names == {"Enshrouded", "valheim", "Enshrouded (15647)"}


async def test_delisted_server_is_kept(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
) -> None:
    """A server Steam stops listing stays, flagged as not listed."""
    await _setup(hass, config_entry)

    mock_steam.clear_requests()
    mock_steam.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    mock_steam.get(STEAM_SERVERS_URL, json=steam_payload())
    mock_a2s.side_effect = TimeoutError
    await _tick(hass, POLLS_PER_DISCOVERY)

    assert _state(hass, config_entry, STEAM_ID, "listed_on_steam") == STATE_OFF
    assert _state(hass, config_entry, STEAM_ID, "status") == STATE_OFF


async def test_steam_outage_keeps_polling(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
) -> None:
    """If Steam is unreachable, known servers are still checked directly."""
    await _setup(hass, config_entry)

    mock_steam.clear_requests()
    mock_steam.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    mock_steam.get(STEAM_SERVERS_URL, exc=aiohttp.ClientError)
    await _tick(hass, POLLS_PER_DISCOVERY)

    assert _state(hass, config_entry, STEAM_ID, "status") == STATE_ON
    assert _state(hass, config_entry, STEAM_ID, "listed_on_steam") == STATE_UNKNOWN

    # Discovery is retried on the very next poll rather than in five minutes.
    mock_steam.clear_requests()
    mock_steam.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    mock_steam.get(STEAM_SERVERS_URL, json=steam_payload(steam_server()))
    await _tick(hass)
    assert _state(hass, config_entry, STEAM_ID, "listed_on_steam") == STATE_ON


async def test_setup_retries_when_nothing_is_known(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """First setup with Steam unreachable asks Home Assistant to retry."""
    aioclient_mock.get(PUBLIC_IP_URL, exc=aiohttp.ClientError)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_remembered_servers_survive_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """A switched-off server still loads (as offline) when Steam is down."""
    await _setup(hass, config_entry)
    stored = hass_storage[storage_key(config_entry.entry_id)]["data"]
    assert stored == {
        "public_ip": PUBLIC_IP,
        "servers": [{"port": QUERY_PORT, "name": "Enshrouded", "app_id": 1203620}],
    }

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    mock_steam.clear_requests()
    mock_steam.get(PUBLIC_IP_URL, exc=aiohttp.ClientError)
    mock_a2s.side_effect = TimeoutError
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert _state(hass, config_entry, STEAM_ID, "status") == STATE_OFF
    assert _state(hass, config_entry, STEAM_ID, "listed_on_steam") == STATE_UNKNOWN


async def test_manual_servers(
    hass: HomeAssistant, mock_steam: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """Manual servers get devices; one Steam already covers is not doubled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PUBLIC_ADDRESS: "", CONF_LOCAL_HOST: LOCAL_HOST},
        options={CONF_MANUAL_SERVERS: f"192.168.1.60:27015\n{LOCAL_HOST}:{QUERY_PORT}"},
    )
    await _setup(hass, entry)

    manual_id = "manual_192.168.1.60_27015"
    assert _state(hass, entry, manual_id, "status") == STATE_ON
    assert _state(hass, entry, manual_id, "listed_on_steam") is None
    assert _state(hass, entry, f"manual_{LOCAL_HOST}_{QUERY_PORT}", "status") is None

    registry = dr.async_get(hass)
    assert registry.async_get_device({(DOMAIN, f"{entry.entry_id}_{manual_id}")})

    # Removing it from the options removes its device after the reload.
    hass.config_entries.async_update_entry(entry, options={})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not registry.async_get_device({(DOMAIN, f"{entry.entry_id}_{manual_id}")})
    assert registry.async_get_device({(DOMAIN, f"{entry.entry_id}_{STEAM_ID}")})


async def test_manual_only_works_without_steam(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """With manual servers configured, Steam being down does not block setup."""
    aioclient_mock.get(PUBLIC_IP_URL, exc=aiohttp.ClientError)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PUBLIC_ADDRESS: "", CONF_LOCAL_HOST: ""},
        options={CONF_MANUAL_SERVERS: "192.168.1.60:27015"},
    )
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, entry, "manual_192.168.1.60_27015", "players") == "2"


async def test_device_removal(
    hass: HomeAssistant, mock_steam: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """Steam devices can be deleted (and forgotten); manual ones cannot."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PUBLIC_ADDRESS: "", CONF_LOCAL_HOST: LOCAL_HOST},
        options={CONF_MANUAL_SERVERS: "192.168.1.60:27015"},
    )
    await _setup(hass, entry)
    registry = dr.async_get(hass)
    steam_device = registry.async_get_device({(DOMAIN, f"{entry.entry_id}_{STEAM_ID}")})
    manual_device = registry.async_get_device(
        {(DOMAIN, f"{entry.entry_id}_manual_192.168.1.60_27015")}
    )

    assert not await async_remove_config_entry_device(hass, entry, manual_device)

    # Steam no longer lists the server, so once forgotten it stays gone.
    mock_steam.clear_requests()
    mock_steam.get(PUBLIC_IP_URL, text=PUBLIC_IP)
    mock_steam.get(STEAM_SERVERS_URL, json=steam_payload())
    assert await async_remove_config_entry_device(hass, entry, steam_device)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert STEAM_ID not in entry.runtime_data.data
    assert not registry.async_get_device({(DOMAIN, f"{entry.entry_id}_{STEAM_ID}")})


async def test_remove_entry_clears_storage(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
    hass_storage: dict[str, Any],
) -> None:
    """Deleting the integration deletes its remembered servers."""
    await _setup(hass, config_entry)
    key = storage_key(config_entry.entry_id)
    assert key in hass_storage

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_diagnostics_redacts_addresses(
    hass: HomeAssistant, mock_steam: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """Addresses and server names never appear in diagnostics."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PUBLIC_ADDRESS: PUBLIC_IP, CONF_LOCAL_HOST: LOCAL_HOST},
        options={CONF_MANUAL_SERVERS: "192.168.1.60:27015"},
    )
    await _setup(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diagnostics)
    for secret in (PUBLIC_IP, LOCAL_HOST, "192.168.1.60", "Ecto World"):
        assert secret not in text
    assert len(diagnostics["servers"]) == 2
    assert diagnostics["servers"][0]["port"] == QUERY_PORT
    assert diagnostics["servers"][0]["info"]["players"] == 2
