# whisker-local

So this is pretty much a replacement for the whisker cloud if you own a litter-robot 4.
You run it in docker on something at home, the robot talks to that instead of amazon, and you get a dashboard on the web, android and ios with your own history and no subscription.
It only listens on your local network.

It's built on [whiskerless](https://github.com/SisyphusMD/whiskerless), which did the actual reverse engineering, this just puts a server and an app on top of it.
Not affiliated with whisker.

## How it works

You pretty much have three pieces.

1. The robot connects to mosquitto, which is a small message server, on port 8883 over TLS. It's the same connection it used to make to aws, just pointed at your house.
2. The server container listens to that, saves every message to a sqlite file, and works out visits, weights, clean cycles and all that.
3. The app talks to the server. It's one flutter codebase, and the web version is served by the server container itself.

## Install

You need docker on something that stays on. Give that machine a fixed address on your router, since the robot gets told that exact address.

```bash
./install.sh
```

It asks for the LAN address and the serial on the robot's label, builds the image, generates the certificates and starts everything.
Then it prints the dashboard address and an access key.
At this point the robot hasn't been touched and the whisker app still works.

## Connect the robot

You do this once, on any computer with bluetooth within a few meters of the robot.

```bash
scripts/connect-robot.sh
```

It checks the broker is actually reachable first, then tells you to hold the Connect button for about 3 seconds until the light blinks yellow.
Holding that button wipes the robot's wifi and there's no way out except finishing, so don't start this if you're about to leave.

If you're doing this from a laptop instead of the server, copy `data/certs/ca.crt` over and pass the details:

```bash
scripts/connect-robot.sh --host 10.0.0.100 --ca ./ca.crt --serial LR4C123456
```

You can add `--dry-run` to walk through it without writing anything to the robot.

## Phones

- web, including iphone: open the dashboard address in a browser.
- android: `cd app && flutter build apk`, then install the file from `build/app/outputs/flutter-apk/`.
- ios: the project is there, but building it needs a mac with xcode. The web version works fine in safari until then.

## Undoing it

Everything here is meant to be reversible, and this is what actually changes.

| What | Where | How you undo it |
|---|---|---|
| Trusted CA, broker address, wifi | Robot settings | Run the reconnect flow in the whisker app |
| Factory certificate | Not touched | Nothing to undo |
| Firmware | Not touched | Nothing to undo |
| Containers and images | Your server | `./uninstall.sh` |
| History and certificates | `data/` on your server | Kept unless you run `./uninstall.sh --purge` |

The full walkthrough is in [docs/going-back.md](docs/going-back.md).

## What it can't do

- It can't tell your cats apart. The robot just sends a weight, the cat names in the whisker app get worked out in their cloud. Every raw reading is saved, so this could probably be added later.
- No firmware updates, those come from the whisker cloud. You'd go back to the cloud for a bit, update, then reconnect.
- No push notifications when you're away, since it's local only. Alerts show up when you open the app.
- The replace filter wizard has to be done at the robot.
- Your robot's serial has to start with `LR4C`. The newer `LR4S` boards haven't been tested by anyone upstream as far as I can tell.

## Backing up

The history is one file, `data/db/whisker-local.db`.
Keep a copy of `data/certs` too, since the robot only trusts that CA and replacing it means re-pairing the robot.

## Development

```bash
# server
cd server && python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest && .venv/bin/ruff check . ../tools

# app
cd app && flutter test && flutter analyze
```

`tools/fake_robot.py` pretends to be a robot over the real TLS connection, so you can work on this without touching the real one.
