# Self-Hosted Game Servers Status

A [Home Assistant](https://www.home-assistant.io/) integration that watches the
game servers you host yourself, whatever game they are running.

You give it your address. It asks Steam which game servers are listed there,
and every server it finds becomes a device in Home Assistant with its status,
player count and more. Start hosting a different game and it shows up on its
own within five minutes.

It works with any game whose dedicated server registers with Steam and answers
Steam's standard server query (A2S), which covers most of them: Enshrouded,
Valheim, Palworld, 7 Days to Die, Satisfactory, ARK, Rust, Counter-Strike and
many more. Minecraft is the main exception; Home Assistant has a built-in
integration for that.

## What you get

One device per game server, with:

| Entity | Notes |
| --- | --- |
| **Status** | Running or not running. Use this for alerts. |
| **Players** | Players online right now. |
| **Player limit** | Diagnostic. |
| **Latency** | How quickly the server answered, in ms. Diagnostic. |
| **Server name** | Diagnostic. |
| **Map** | Diagnostic. Empty for games that do not report one. |
| **Version** | Diagnostic. |
| **Listed on Steam** | Whether Steam currently lists the server at your public address, which is what friends outside your home rely on. Diagnostic. |

The detail sensors are unavailable while a server is not running.

## How it works

1. **Discovery, every five minutes.** Home Assistant asks Steam's public
   `GetServersAtAddress` API which servers are listed at your public IPv4
   address. No Steam account or API key is needed.
2. **Status, every minute.** Home Assistant asks each server directly for its
   live status, the same way the in-game server browser does. A single lost
   reply is retried before a server is reported as not running.

Servers found once are remembered, so a server you switch off shows as "not
running" rather than disappearing.

## Installation

### HACS

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/bradwhiteau/ha-self-hosted-game-servers-status`
   with the type **Integration**.
3. Install **Self-Hosted Game Servers Status** and restart Home Assistant.

### Manual

Copy `custom_components/self_hosted_game_servers` into the
`custom_components` folder of your Home Assistant configuration and restart.

Requires Home Assistant 2025.8 or newer.

## Setup

Go to **Settings > Devices & services > Add integration** and search for
**Self-Hosted Game Servers Status**.

| Field | What to enter |
| --- | --- |
| **Public address** | Your public IPv4 address, or a host name that points to it (for example a dynamic DNS name). Leave it empty to detect it automatically, which also keeps working when your provider changes your address. |
| **Server address on your network** | The address of the machine running the servers, for example `192.168.1.50`. Home Assistant uses it for the direct status check. Leave it empty to use the public address instead; many home routers cannot reach their own public address from inside, so filling this in is recommended. |

If your servers run on more than one machine, add the integration once per
machine.

### Servers Steam does not list

A server that is only used on your own network, or that is not registered with
Steam, can be added by hand. Open the integration's **Configure** dialog and
enter one `host:port` per line, using the game's **query port** (often the game
port plus one; check the server's settings).

### Removing a server

Delete its device from the device page. A server Steam still lists will be
found again; one added by hand is removed in **Configure** instead.

## Example: alert when a game server stops

```yaml
automation:
  - alias: "Game server stopped"
    triggers:
      - trigger: state
        entity_id: binary_sensor.enshrouded_status
        to: "off"
        for: "00:02:00"
    actions:
      - action: notify.notify
        data:
          title: "Game server down"
          message: "Enshrouded has stopped responding."
```

## Good to know

- **Steam only lists IPv4 addresses**, and only servers that are reachable from
  the internet and registered with Steam.
- **Query port.** The status check uses the port Steam reports for the server.
  If a server is listed but always shows as not running, check that its query
  port (UDP) is open on the server machine's firewall for Home Assistant.
- **Privacy.** Your public address is sent to Steam to look up your servers.
  With automatic detection, Home Assistant also asks
  [ipify](https://www.ipify.org/) what your public address is. Nothing else
  leaves your network, and diagnostics downloads have addresses and server
  names removed.
- **Device names** come from Steam's short name for the game, which is not
  always pretty. Rename the device to whatever you like.

## Contributing

Issues and pull requests are welcome. To run the checks locally:

```bash
pip install -r requirements_test.txt
ruff check .
pytest --cov
```

## Licence

[MIT](LICENSE). This project is not affiliated with Valve or Steam.
