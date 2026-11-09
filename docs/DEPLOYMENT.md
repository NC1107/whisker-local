# Running it

Everything here is about the machine the server runs on.
Building and signing releases is in [RELEASING.md](RELEASING.md).

## Settings

`install.sh` writes them to `.env`, and running it again with flags changes them.

| Setting | Flag | Notes |
|---------|------|-------|
| `LAN_IP` | `--ip` | The address the robot is told to connect to. It's given to the robot, so keep it fixed |
| `ROBOT_SERIAL` | `--serial` | On the robot's label, starts with `LR4` |
| `DASHBOARD_PORT` | `--port` | Default 8420 |
| `TZ` | `--timezone` | Daily totals are cut at midnight in this zone |
| `ACCESS_KEY` | | Generated once, and it's what the dashboard and apps log in with |

Port 8883 (the robot) and the dashboard port have to be free on `LAN_IP`.
Both are published on that address only, so nothing listens on other interfaces.

To upgrade, `git pull` and run `./install.sh` again, it keeps your key, history and certificates.

## Backups

Everything lives in `data/`:

- `data/db/whisker-local.db` is the history, and `data/db/cat-photos/` the photos.
- `data/db/backups/` holds copies the server makes itself before an upgrade changes the database and before an import is reprocessed. It never deletes them.
- `data/certs/` is the certificate the robot trusts.
- `data/acl` is the broker's rules, which `install.sh` rewrites.

`data/certs/ca.key` and `server.key` are private keys, so keep every copy of `data/` private too.
Anyone holding `ca.key` can pose as your server to the robot.

Copying `data/db` while the server runs isn't safe, the database is in WAL mode and a copy can miss or tear recent writes.
Stop it first:

```bash
docker compose stop
cp -a data /somewhere/private/data-backup
docker compose start
```

To copy only the history while it's running, ask the server for a consistent copy with your access key from `.env`.
It leaves out the photos and certificates.

```bash
curl -H "Authorization: Bearer $ACCESS_KEY" http://192.168.1.50:8420/api/export/database -o whisker-local.db
```

To restore, stop the server, put `data/` back, and run `./install.sh` again with the same address.
Without the certificates the robot can't trust the server, so you'd run `scripts/connect-robot.sh` again.
`.env` isn't in `data/`, and a new one means a new access key.

## Behind a reverse proxy

Proxy to the dashboard port, `LAN_IP:8420` or `server:8080` from inside the compose network.
Use https, because the access key travels with every request.
It's plain http and there are no websockets, so nothing special needs turning on.
Port 8883 can't go through a proxy, the robot has to reach it directly.

`deploy/traefik.override.yaml` is an example for traefik.
Copy it to `compose.override.yaml` next to `compose.yaml` and change the host name.
It takes the dashboard off the LAN port, and compose merges it into every command so `install.sh` needs no flags.
The labels expect a network called `traefik_proxy`, a `websecure` entrypoint, a `cloudflare` certificate resolver, a `secureHeaders` middleware and watchtower in label mode, so change what your setup calls differently.

## Push notifications

They're off by default and nothing is sent anywhere until you set `PUSH_RELAY_URL`.
iOS only, android push would need firebase and the web app has none.

Apple only lets the app's own push key deliver to it, and that key can't be handed to every self-hoster.
So the maintainer runs a relay that holds it.
Your server sends it the one-line text of an alert (like "Waste drawer is 82% full.") and your phones' push tokens, and the relay passes them to apple.
It keeps your server's key (hashed) and which tokens belong to it, not the text.
It isn't told your server's address, and cat names and the serial are never in an alert.

This only works with the published iOS app, since the relay's key belongs to that build.

1. Add the relay and restart the server.

```bash
echo PUSH_RELAY_URL=https://litterbox-relay.npc-server.top >> .env
docker compose up -d
```

2. In the app open Settings, Notifications, and turn on "Notifications on this phone". It asks for permission at that point.
3. Press "Send a test notification".

The address has to be https unless the relay is on the same machine, and redirects aren't followed.
`install.sh` keeps `PUSH_RELAY_URL` when it rewrites `.env`.
The Notifications card only shows once the server has a relay, so if it's missing the URL isn't set.

Which alerts are sent is a setting per kind in the app, or `PATCH /api/push/kinds`.
Alerts that start are sent and ones that clear aren't, and routine events like every visit are off until you turn them on.
If the relay is down the robot isn't affected, alerts wait in a queue of 16 and the oldest are dropped past that.
To run your own relay for your own build of the app, see [RELEASING.md](RELEASING.md#running-your-own-push-relay).
