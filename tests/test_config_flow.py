"""Tests for the config and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.self_hosted_game_servers.const import (
    CONF_LOCAL_HOST,
    CONF_MANUAL_SERVERS,
    CONF_PUBLIC_ADDRESS,
    DOMAIN,
    PUBLIC_IP_URL,
    STEAM_SERVERS_URL,
)
from custom_components.self_hosted_game_servers.coordinator import storage_key

from .conftest import LOCAL_HOST, PUBLIC_IP, steam_payload


async def test_user_flow_auto_detect(
    hass: HomeAssistant, mock_steam: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """Leaving the public address empty detects it and creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_HOST: f" {LOCAL_HOST} "}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == LOCAL_HOST
    assert result["data"] == {CONF_PUBLIC_ADDRESS: "", CONF_LOCAL_HOST: LOCAL_HOST}
    assert result["result"].unique_id == PUBLIC_IP


async def test_user_flow_public_address_only(
    hass: HomeAssistant, mock_steam: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """A fixed public address is used as given and becomes the title."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: PUBLIC_IP}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == PUBLIC_IP
    assert result["data"] == {CONF_PUBLIC_ADDRESS: PUBLIC_IP, CONF_LOCAL_HOST: ""}


async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_a2s: AsyncMock
) -> None:
    """Each failure shows its own error and the form can be resubmitted."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: "2001:db8::1"}
    )
    assert result["errors"] == {CONF_PUBLIC_ADDRESS: "invalid_address"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: "home..example.com"}
    )
    assert result["errors"] == {CONF_PUBLIC_ADDRESS: "invalid_address"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: "192.168.1.1"}
    )
    assert result["errors"] == {CONF_PUBLIC_ADDRESS: "not_public"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_HOST: "my..server"}
    )
    assert result["errors"] == {CONF_LOCAL_HOST: "invalid_host"}

    aioclient_mock.get(PUBLIC_IP_URL, exc=aiohttp.ClientError)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "cannot_detect_ip"}

    aioclient_mock.get(STEAM_SERVERS_URL, status=500)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: PUBLIC_IP}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(STEAM_SERVERS_URL, json=steam_payload())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: PUBLIC_IP}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "user_input",
    [
        {CONF_LOCAL_HOST: LOCAL_HOST},
        {CONF_PUBLIC_ADDRESS: PUBLIC_IP},
        {CONF_PUBLIC_ADDRESS: PUBLIC_IP, CONF_LOCAL_HOST: "192.168.1.60"},
    ],
)
@pytest.mark.usefixtures("mock_steam")
async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, user_input: dict
) -> None:
    """A public address can only be set up once, however it is entered."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
) -> None:
    """Addresses can be changed, with the same validation as setup."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: "::1"}
    )
    assert result["errors"] == {CONF_PUBLIC_ADDRESS: "invalid_address"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PUBLIC_ADDRESS: PUBLIC_IP, CONF_LOCAL_HOST: "192.168.1.60"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {
        CONF_PUBLIC_ADDRESS: PUBLIC_IP,
        CONF_LOCAL_HOST: "192.168.1.60",
    }
    assert config_entry.title == "192.168.1.60"
    assert config_entry.unique_id == PUBLIC_IP


async def test_reconfigure_new_network_clears_servers(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_steam: AiohttpClientMocker,
    mock_a2s: AsyncMock,
    hass_storage: dict,
) -> None:
    """Moving to another public address forgets the old network's servers."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    key = storage_key(config_entry.entry_id)
    assert hass_storage[key]["data"]["servers"]

    mock_steam.clear_requests()
    mock_steam.get(STEAM_SERVERS_URL, json=steam_payload())
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: "64.94.100.99"}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == "64.94.100.99"
    assert hass_storage[key]["data"]["servers"] == []
    assert not dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )


@pytest.mark.usefixtures("mock_steam", "mock_a2s")
async def test_reconfigure_onto_another_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Reconfigure cannot move onto an address another entry already has."""
    config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="64.94.100.99",
        data={CONF_PUBLIC_ADDRESS: "64.94.100.99", CONF_LOCAL_HOST: ""},
    )
    other.add_to_hass(hass)

    result = await other.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PUBLIC_ADDRESS: PUBLIC_IP}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert other.unique_id == "64.94.100.99"


@pytest.mark.usefixtures("mock_steam", "mock_a2s")
async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Manual servers are validated before being saved."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    for bad in ("192.168.1.50", "my..server:27015"):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MANUAL_SERVERS: bad}
        )
        assert result["errors"] == {CONF_MANUAL_SERVERS: "invalid_servers"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MANUAL_SERVERS: " 192.168.1.60:27015 "}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_MANUAL_SERVERS: "192.168.1.60:27015"}

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert config_entry.options == {CONF_MANUAL_SERVERS: ""}
