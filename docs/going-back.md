# Going back to the whisker cloud

So the idea is you can undo this whole thing with the whisker app, the same way you set the robot up the first time.

## What changed on the robot

The connect step only rewrites a few saved settings over bluetooth: which CA it trusts, which broker address it connects to, its topics, and the wifi.
It's the same bluetooth channel the whisker app uses.

The firmware isn't touched.
The factory certificate isn't touched either, because `connect-robot.sh` runs whiskerless with `--auth anonymous` on purpose.
Which means going back doesn't depend on anything being restored for you, the robot still has the identity it shipped with.

## Going back

1. Open the whisker app and run the set up or reconnect flow for this robot.
2. If the app asks for pairing mode, hold the Connect button for about 3 seconds until the light blinks yellow. Just don't tap it, a short press turns the wifi off.
3. The app writes amazon's CA and the cloud address back, and the robot should reconnect to the cloud on its own.

The whiskerless project describes this same round trip in their [recovery doc](https://github.com/SisyphusMD/whiskerless/blob/main/docs/recovery.md), and from what they write it was done on one robot.
That's one observation, so it doesn't say much about other robots or firmware versions.
This setup hasn't done that trip yet either, so treat it as probably fine rather than proven.

Then if you want the containers gone too:

```bash
./uninstall.sh          # removes containers and images, keeps your history
./uninstall.sh --purge  # also deletes history, certificates and the bluetooth tools
```

The order doesn't really matter.
If you remove the server first the robot just has nothing to connect to until you reconnect it, it should keep cleaning from its own sensors in the meantime.

## Firmware updates

Those only come through the whisker cloud, so you'd go back to the cloud, let it update, then run `scripts/connect-robot.sh` again.
Your history stays on the server the whole time.
A newer firmware could behave differently from what whiskerless has tested, so check their compatibility notes first.

## If something goes wrong halfway

If the connect step gets interrupted, or it finishes and the robot never shows up on the server, the robot can end up with no wifi saved.
You can fix that either way, hold Connect until it blinks yellow, then finish `scripts/connect-robot.sh` or run the whisker app flow.
Pairing mode doesn't time out, so the robot just waits for you.
If the script says the robot never connected, the usual reason is that its wifi can't reach port 8883 on the server.

Don't send commands you find in the protocol docs by hand.
Some of them erase flash or reset the boards, the server only exposes the safe ones and everything goes through the whiskerless safety guard.

## Bringing your old history over

The whisker app can export the litter box's activity as a CSV file, usually named like `litter_box_activity_<date>.csv`.
Look for the export on the robot's activity history in that app, since the exact menu depends on its version.
The app only keeps about a month, so export before you go back to the cloud if you want that month.
The file has the columns `Activity`, `Timestamp` and `Value`, newest first.

In this app, open the Robot tab and choose Import Whisker history.
Pick the file, check the counts it shows, and confirm.
Weights become visits and finished clean cycles become clean cycles, and the rest is counted but not kept.
The file has dates but no year, so the app takes the export date from the file name, and falls back to today if the name has none.
Keep the name the whisker app gave it, or give the server the export date yourself (`reference_date` in [the API](api.md)).
Importing the same file again adds nothing, and Undo last import takes the whole import out again.
Visits from before the robot first connected are all it brings in, so it can't double count anything the robot recorded.
