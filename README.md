# Litterbox Local

Keep your litter-robot 4 data on your own machine, with no 30 day limit and nothing behind a paywall.

![Litterbox Local in a browser and on a phone](docs/images/screens.png)

It runs in docker at home and takes the place of the whisker cloud.
Built on [whiskerless](https://github.com/SisyphusMD/whiskerless), not affiliated with whisker.

## What you get

- Every visit and clean cycle, kept for as long as you run it
- Weight and visit history for each cat
- Robot controls, upkeep reminders and alerts
- The web app, plus android and ios
- Your last month of whisker history, if you import the export

## Install

You need docker on something that stays on, on the same network as the robot, with a fixed address.

```bash
./install.sh
```

It prints the dashboard address and an access key.
The robot isn't touched yet.

## Connect the robot

Run this on a computer with bluetooth, near the robot.

```bash
scripts/connect-robot.sh
```

It wipes the robot's wifi partway through, so only start it if you have a few minutes.
If it fails, run it again or use the reconnect flow in the whisker app.
From a computer other than the server, copy `data/certs/ca.crt` over and pass `--host`, `--ca` and `--serial`.

## Apps

- Web: open the dashboard address.
- Android: the apk is on the [releases page](https://github.com/NC1107/whisker-local/releases).
- iOS: testflight, see below, or use the web app.

### iOS testing

It's not on the app store yet, so testing goes through testflight as an internal tester.
[Open an issue](https://github.com/NC1107/whisker-local/issues/new) asking to be added, and leave your email out of it, I'll ask for your apple id privately.
You accept an invite to the app store connect team, then the app shows up in testflight.

## Notifications

They're off by default, and the server sends nothing anywhere until you turn them on.
To turn them on, add `PUSH_RELAY_URL=https://litterbox-relay.npc-server.top` to `.env` and restart the server, ios only for now.
That's my relay, so the alert text and your phone's push token pass through it on the way to apple.
It doesn't keep the text and isn't told your server's address.
If you'd rather not, leave it off, or run your own relay with your own build of the app, see [the notes](docs/DEPLOYMENT.md#push-notifications).

## Going back

Run the reconnect flow in the whisker app.
The firmware and factory certificate are never touched.
`./uninstall.sh` removes the containers, and `--purge` removes your history too.
More in [docs/going-back.md](docs/going-back.md).

## Good to know

- The robot only sends a weight, so close cats may need picking by hand.
- Firmware updates still come from the whisker cloud.
- The serial has to start with `LR4C`.
- Back up `data/`, the robot only trusts the certificate in there.

## More

- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): tests, and trying it without a robot
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): proxies, notifications, releases
- [docs/api.md](docs/api.md): the server API
