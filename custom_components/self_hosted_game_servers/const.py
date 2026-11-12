"""Constants for the Self-Hosted Game Servers Status integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "self_hosted_game_servers"

CONF_PUBLIC_ADDRESS: Final = "public_address"
CONF_LOCAL_HOST: Final = "local_host"
CONF_MANUAL_SERVERS: Final = "manual_servers"

SOURCE_STEAM: Final = "steam"
SOURCE_MANUAL: Final = "manual"

STEAM_SERVERS_URL: Final = (
    "https://api.steampowered.com/ISteamApps/GetServersAtAddress/v1/"
)
PUBLIC_IP_URL: Final = "https://api.ipify.org"
HTTP_TIMEOUT: Final = 10

# How often each game server is asked for its live status.
UPDATE_INTERVAL: Final = timedelta(seconds=60)
# Steam is asked which servers are listed at the public address on every
# Nth status poll (so every five minutes).
POLLS_PER_DISCOVERY: Final = 5

# A2S is UDP, so a single lost packet should not mark a server as down.
QUERY_TIMEOUT: Final = 3.0
QUERY_ATTEMPTS: Final = 2

STORAGE_VERSION: Final = 1
