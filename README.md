# whisker-local

So this is pretty much a replacement for the whisker cloud if you own a litter-robot 4.
You run it in docker on something at home, the robot talks to that instead of amazon, and you get a dashboard on the web, android and ios with your own history and no subscription.
It only listens on your local network.

It's built on [whiskerless](https://github.com/SisyphusMD/whiskerless), which did the actual reverse engineering, this just puts a server and an app on top of it.
Not affiliated with whisker.

## How it works

You pretty much have three pieces.

1. The robot connects to mosquitto, which is a small message server, on port 8883 over TLS. It's the same connection it used to make to aws, just pointed at your house.
2. The server container listens to that, saves the messages to a sqlite file, and works out visits, weights, clean cycles and all that.
3. The app talks to the server. It's one flutter codebase, and the web version is served by the server container itself.

## Install

You need docker on something that stays on. Give that machine a fixed address on your router, since the robot gets told that exact address.

```bash
./install.sh
```

It asks for the LAN address and the serial on the robot's label, gets the server image, generates the certificates and starts everything.
Then it prints the dashboard address and an access key.
At this point the robot hasn't been touched and the whisker app still works.

A few things it does that are worth knowing:

- The LAN address has to be a private one that's actually on the machine, so it turns down things like a tailscale or docker address, since the robot couldn't reach those.
- It uses the published image from `ghcr.io/nc1107/whisker-local` when it can pull it, and builds the same thing from this folder when it can't. `./install.sh --build` forces the local build.
- It writes the machine's timezone to `.env`, because the per day numbers are cut at midnight. If it guesses wrong, run it again with `--timezone Area/City`.
- Running it again is fine. If the certificate got reissued it recreates the broker so the new one is the one the robot sees.

## Connect the robot

You do this once, on any computer with bluetooth within a few meters of the robot.

```bash
scripts/connect-robot.sh
```

It checks the broker answers with the right certificate first, then asks you to confirm before you touch the robot.
After that it tells you to hold the Connect button for about 3 seconds until the light blinks yellow.
Holding that button wipes the robot's wifi, so once you've done it the robot stays offline until you either finish the script or run the reconnect flow in the whisker app.
Don't start this if you're about to leave.

When it's done it waits up to a few minutes for the robot to show up on the server.
If it doesn't, it says so and tells you how to get the robot back, which is the same two options.
Usually that means the robot's wifi can't reach port 8883 on the server, like a guest network or a firewall in between.

If you're doing this from a laptop instead of the server, copy `data/certs/ca.crt` over and pass the details:

```bash
scripts/connect-robot.sh --host 10.0.0.100 --ca ./ca.crt --serial LR4C123456
```

There's no `--dry-run`, and the script refuses it.
The library has one, but it still scans and connects over bluetooth for real, which means the robot has to be in pairing mode, and pairing mode is the part that wipes the wifi.
So a dry run puts the robot in the same spot as the real thing.
If you want to rehearse, run `tools/fake_robot.py`, which pretends to be a robot over the same TLS connection.

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
| Containers and images | Your server | `./uninstall.sh` removes the containers and the server and broker images. The build cache and base images it pulled stay, it prints how to clear them |
| History and certificates | `data/` on your server | Kept unless you run `./uninstall.sh --purge`, which also removes `.env` and the bluetooth tools the connect script installed |

The full walkthrough is in [docs/going-back.md](docs/going-back.md).

## What it can't do

- It can't see which cat is in the box, since the robot only sends a weight.
  So it tells your cats apart by weight, the way the whisker app does.
  You add each cat with a rough weight, and a visit goes to a cat when the weight is close to that cat's usual one and clearly closer to it than to any other cat.
  Otherwise the visit stays unassigned and the app asks you to choose.
  Two cats with close weights are the case where that happens a lot, and then you may need to pick by hand for some visits.
  Your choices are never changed by the server, and they count as evidence for the next visits.
- No firmware updates, those come from the whisker cloud. You'd go back to the cloud for a bit, update, then reconnect.
- No push notifications when you're away, since it's local only. Alerts show up when you open the app.
- The replace filter wizard has to be done at the robot.
- Your robot's serial has to start with `LR4C`. The newer `LR4S` boards haven't been tested by anyone upstream as far as I can tell.

## Trying it without a robot

There's a sample data mode, so you can look at the app before the robot is connected.
It fills a separate database with a few months of made up history for cats you name, and a simulator plays the robot.
The app says it's sample data, and the server won't mix it with a real history.
The commands are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), and `scripts/end-demo.sh` puts the sample data aside so the real robot starts from an empty history.

## Backing up

The history is one file, `data/db/whisker-local.db`, and the cat photos are in `data/db/cat-photos`.
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
The simulator itself lives in the server package as `whisker_local.simulator`, and the script is a thin wrapper around it.
