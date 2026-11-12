"""Tests for the config and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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


async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The same pair of addresses cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_HOST: LOCAL_HOST}
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


@pytest.mark.usefixtures("mock_steam", "mock_a2s")
async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Manual servers are validated before being saved."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MANUAL_SERVERS: "192.168.1.50"}
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
