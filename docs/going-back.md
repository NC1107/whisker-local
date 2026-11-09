# Going back to the whisker cloud

You undo this with the whisker app's setup flow, the same way you set the robot up originally.

## How sure this is

Pretty sure, not proven.
The robot keeps its firmware and its factory certificate, so the app has everything it needs to put the cloud settings back.
The [whiskerless recovery doc](https://github.com/SisyphusMD/whiskerless/blob/main/docs/recovery.md) describes the same round trip on one robot.
This setup hasn't done it yet.

## What changes on the robot

The connect step rewrites a few saved settings over bluetooth, the same channel the whisker app uses.
Those are the certificate authority it trusts, the broker address, its topics and the wifi.
`connect-robot.sh` runs whiskerless with `--auth anonymous` so the factory certificate stays.
While it's connected here the whisker app doesn't work for this robot.

## Going back

1. In the whisker app, run the set up or reconnect flow for this robot.
2. If it asks for pairing mode, hold the Connect button for about 3 seconds until the light blinks yellow. A short tap turns the wifi off instead.
3. The app writes amazon's certificate and the cloud address back, and the robot reconnects on its own.

Then remove the server if you want:

```bash
./uninstall.sh          # removes the containers and images, keeps data/ and .env
./uninstall.sh --purge  # also deletes data/ (history and certificates), .env and the bluetooth tools
```

The order doesn't matter.
Without the server the robot keeps cleaning on its own sensors, it just has nothing to report to.

## Firmware updates

They only come through the whisker cloud.
So go back to the cloud, let it update, then run `scripts/connect-robot.sh` again.
Your history stays on the server.
A newer firmware might behave differently from what whiskerless has tested, so check their compatibility notes first.

## If the connect step stops halfway

The robot can be left with no wifi saved.
Hold Connect until it blinks yellow, then run `scripts/connect-robot.sh` again or the whisker app flow.
Pairing mode doesn't time out.
If the script says the robot never connected, its wifi usually can't reach port 8883 on the server (guest networks and firewalls do this).

Don't send commands from the protocol docs by hand, some erase flash.
The server only exposes the safe ones.

## Bringing your old history over

The whisker app can export the litter box activity as a CSV, named like `litter_box_activity_<date>.csv`.
It only keeps about a month, so export before you go back to the cloud.
Where the export is depends on the app version, look on the robot's activity history.

In this app open Settings, Import Whisker history, pick the file, check the counts it shows, and confirm.
Weights become visits, finished clean cycles become clean cycles, and the rest is counted but not kept.
The file has dates without a year, so the year comes from the export date in the file name.
Keep the name the whisker app gave it, or the date falls back to today.

Importing the same file twice adds nothing, and "Undo last import" removes the whole import.
It only takes in activity from before the robot first connected here, so nothing gets counted twice.
