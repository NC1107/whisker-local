# Litterbox Local

So this is pretty much a replacement for the whisker cloud if you own a litter-robot 4.
You run it in docker at home, the robot talks to that instead of amazon, and you get your own history on web, ios and android with no subscription.

Built on [whiskerless](https://github.com/SisyphusMD/whiskerless), which did the actual reverse engineering.
Not affiliated with whisker.

## Install

You need docker on something that stays on, with a fixed address on your router.

```bash
./install.sh
```

It asks for the LAN address and the robot's serial, starts everything, then prints the dashboard address and an access key.
The robot isn't touched yet and the whisker app still works.

## Connect the robot

Run this on a computer with bluetooth near the robot.

```bash
scripts/connect-robot.sh
```

Holding the Connect button wipes the robot's wifi, so don't start this if you're about to leave.
If it goes wrong, run it again or use the reconnect flow in the whisker app.
From another computer, copy `data/certs/ca.crt` over and pass `--host`, `--ca` and `--serial`.

## Going back

Run the reconnect flow in the whisker app, the factory certificate and firmware are never touched.
`./uninstall.sh` removes the containers, and `--purge` removes your history too.
The walkthrough is in [docs/going-back.md](docs/going-back.md).

## Limits

- The robot only sends a weight, so cats are told apart by weight and close ones may need picking by hand.
- Firmware updates still come from the whisker cloud.
- The serial has to start with `LR4C`.
- Push notifications are ios only and go through an optional relay.

## Backups

Keep a copy of `data/`, the robot only trusts the CA in there.

## More

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) covers sample data, running behind a proxy, notifications and releases.
- [docs/api.md](docs/api.md) is the server API.

## Development

```bash
cd server && python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest && .venv/bin/ruff check . ../tools

cd app && flutter test && flutter analyze
```

`tools/fake_robot.py` pretends to be a robot, so you can work on this without a real one.
