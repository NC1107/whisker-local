# Deployment

This covers how releases get built and what it takes to put the app on a phone through the stores.
Running the server on your own machine is a separate thing, that's in the main README.

The short version is that the signing secrets are optional.
Web and the unsigned builds work with none of them, so a fresh fork builds fine without touching any settings.
The secrets only matter once you want a signed Android build or something on TestFlight.

## How releases work

Every push to main runs the release workflow, and it pretty much goes in this order.

- It runs the server tests and the Flutter analyze and test steps first, so a broken merge can't ship.
- It reads the commit messages since the last tag and bumps the version from them: a `feat:` is a minor bump, a breaking change is a major bump, anything else is a patch.
- It tags the commit.
- It builds the Flutter web app and the Android APK.
- It builds the iOS app on a GitHub macOS runner, since there's no Mac involved anywhere in this.
- It creates a GitHub Release with the APK attached.

The build number is the run number times 100 plus the run attempt, so it only ever goes up and a re-run never collides with the first attempt.
The version and build number get passed to `flutter build` as `--build-name` and `--build-number`, so nothing in the repo needs editing per release.

Everything in that list runs on the built-in `GITHUB_TOKEN`, so there's nothing to set up for it.

## Secrets

All of these live under repo Settings, Secrets and variables, Actions.
None of them are required.
Here is every one and what happens when it's missing.

### Android

| Secret | What happens when it's missing |
|--------|--------------------------------|
| `ANDROID_KEYSTORE_BASE64` | The release APK is signed with the debug key instead. It installs fine, but you can't upgrade it with a build signed by a real key later, and the Play Store won't take it. |
| `ANDROID_KEY_PROPERTIES` | Same as above. The workflow writes this to `app/android/key.properties` next to the decoded keystore, and the gradle file only uses the release key when that file exists. |

You make the keystore once with `keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload`.
Then `base64 -w0 upload-keystore.jks` for the first secret.
The second one is the four lines of `key.properties`: `storePassword`, `keyPassword`, `keyAlias=upload` and `storeFile=upload-keystore.jks`.
Both `key.properties` and the keystore are git-ignored, so locally you can drop them in `app/android/` and the release build picks them up.

### iOS

| Secret | What happens when it's missing |
|--------|--------------------------------|
| `IOS_CERTIFICATE_BASE64` | No signed iOS build. The workflow only runs the unsigned `flutter build ios --no-codesign` to check the project still compiles, and nothing goes to TestFlight. |
| `IOS_CERTIFICATE_PASSWORD` | Same, the certificate can't be imported without it. |
| `IOS_PROVISIONING_PROFILE_BASE64` | Same, the export has nothing to sign with. |
| `ASC_API_KEY_BASE64` | The signed IPA can be built but the upload and the processing check are skipped. |
| `ASC_KEY_ID` | Same as the key above. |
| `ASC_ISSUER_ID` | Same as the key above. |

The signed path is all or nothing for the first three, and the upload needs the last three on top of that.
If only some of them are set the workflow treats the group as missing instead of failing halfway through.

Where each one comes from:

- `IOS_CERTIFICATE_BASE64` is an Apple Distribution certificate with its private key, exported as a `.p12` and run through `base64 -w0`.
- `IOS_CERTIFICATE_PASSWORD` is whatever password you picked when you exported the `.p12`.
- `IOS_PROVISIONING_PROFILE_BASE64` is the App Store profile named "Whisker Local App Store Distribution", run through `base64 -w0`.
- `ASC_API_KEY_BASE64` is the `.p8` from App Store Connect (Users and Access, Integrations, App Store Connect API), run through `base64 -w0`.
- `ASC_KEY_ID` is shown next to that key on the same page.
- `ASC_ISSUER_ID` is at the top of that page.

## How the iOS build is signed

It's the same setup check-in and new-me use.
The Release configuration of the Runner target is on manual signing, team `76S78SUWVM`, identity "Apple Distribution", profile "Whisker Local App Store Distribution".
`app/ios/ExportOptions.plist` repeats the same values for the export step.
Debug, Profile and the no-codesign build don't touch any of that, so they work without a certificate.

The bundle id is `top.npcserver.whiskerlocal` and the Android application id is the same string.

The workflow imports the certificate into a throwaway keychain, copies the profile into place, builds with `flutter build ipa`, then runs `scripts/check-ipa.sh` to confirm the version, the build number and the signature on the exported IPA.
After the upload, `scripts/asc-wait.sh` polls App Store Connect until the build finishes processing.

It uses CocoaPods and not Swift Package Manager.
Check-in got a white launch screen with SPM, so the workflow turns it off with `flutter config --no-enable-swift-package-manager` before building.

## What only you can do

A few steps can't be done from the repo or from a runner.

- **Create the app record in App Store Connect.** This is only possible in the web UI. Use the bundle id `top.npcserver.whiskerlocal` and pick any name and SKU. Without it the upload has nowhere to go.
- **Create the provisioning profile.** It has to be named "Whisker Local App Store Distribution" or the signing settings won't find it. You can do it in the developer portal, or run `scripts/asc-setup.sh`.
- **Export the distribution certificate.** If you already have one from check-in or new-me, it's the same Apple Distribution certificate and you can reuse that `.p12`. Otherwise `asc-setup.sh --create-cert` can make one from a CSR, but the private key still has to come from your machine.
- **Add the secrets.** Nothing here will set them for you.
- **Create the Play Console app and upload the first build by hand**, if you ever want the Play Store. There's no Play upload step in the workflow right now, the APK just goes on the GitHub Release.

### Using asc-setup.sh

It talks to the App Store Connect API and is safe to run more than once.
It registers the bundle id if it's missing, finds your distribution certificate, and creates the provisioning profile, or reuses it if an active one already exists.
The profile comes out on stdout as base64, so you can pipe it straight into the secret.

```
export ASC_API_KEY_BASE64=... ASC_KEY_ID=... ASC_ISSUER_ID=...
scripts/asc-setup.sh --dry-run
scripts/asc-setup.sh | gh secret set IOS_PROVISIONING_PROFILE_BASE64
```

The dry run only prints the requests it would make.
If you have more than one distribution certificate it stops and asks for `--cert-id`.

## Behind a reverse proxy

`deploy/traefik.override.yaml` puts the dashboard behind Traefik and takes it off the LAN port.
Copy it to `compose.override.yaml` next to `compose.yaml` and change the host name in it.
Compose merges that file into every command, so `install.sh` and `scripts/end-demo.sh` work the same as before.

The broker stays on the LAN address.
The robot has to reach it directly on 8883, so don't put that port behind the proxy or forward it from the router.

The labels expect a network called `traefik_proxy`, a `websecure` entrypoint, a `cloudflare` certificate resolver, a `secureHeaders` middleware and Watchtower in label mode.
Change whichever of those your setup calls something else.

## Push notifications

Push is optional and off by default.
A server with no `PUSH_RELAY_URL` never contacts anyone about it, and the app hides everything about notifications.
Nothing in the release depends on it either.

Push works on iOS only.
Android push needs Firebase, which this project does not use yet, so it is not offered.
The web app has no push.

### Why there is a relay

The published iOS app is signed by the maintainer, so only the maintainer's Apple push key can deliver to it.
Handing that key to every self-hoster is not an option, since it can push to every install of the app.
So the maintainer runs one relay that holds it.
Your server asks the relay for a key once, then sends it the text of an alert and the notification tokens of your own phones.
The relay forwards the message to Apple's push service.
A phone's token can be held by up to three servers at once, the ones that phone paired with, and a fourth pushes out the one that sent least recently.
The relay also limits how fast any one server, token or address can send, and revokes a key that mostly sends to dead tokens.

### Using the maintainer's relay

This works with the published iOS app only, since that is the build the relay's key belongs to.
The maintainer's relay is at `https://litterbox-relay.npc-server.top`.

1. Add the relay address to `.env`, then restart the server.

```bash
echo PUSH_RELAY_URL=https://litterbox-relay.npc-server.top >> .env
docker compose up -d
```

2. Open the app, go to the Robot tab, then Notifications, and turn on "Alerts on this phone".
   The system asks for permission at that point and not before.
3. Press "Send a test notification" to check the whole path.

The address must be https, except for a relay on the same machine, and the server does not follow redirects.
`install.sh` keeps `PUSH_RELAY_URL` when it rewrites `.env`.
The relay is never told your server's address.
The Notifications card only appears in the app once the server reports a relay, so its absence means the URL is not set.

What leaves your network is the one-line text of an alert, such as "Waste drawer is 82% full.", plus the push tokens of your phones.
The relay keeps your key (hashed) and which tokens belong to it.
It does not keep alert text, and it logs counts and error codes only.
Cat names and the robot's serial are not part of any alert.

Which alerts are sent is a setting per alert kind, under Notifications in the app or `PATCH /api/push/kinds`.
Alerts that start are sent and ones that clear are not.
Routine events such as every visit are off until you turn them on.

If the relay is down, nothing changes for the robot.
Alerts wait in a queue of 16 and the oldest are dropped past that, so a long outage loses alerts instead of piling them up.

### Running your own relay

If you build the app yourself under your own bundle id, the maintainer's relay cannot reach it, because its Apple key belongs to the maintainer's app.
You run your own relay instead, and the maintainer's relay repositories are the model.
Review `slim-m-relay` first, since it sends to APNs directly with a `.p8` key, and `check-in-relay`, which is the smaller one.
Both speak the same `POST /v1/register` and `POST /v1/send` shape this server calls, and `litterbox-local-relay` is the one the maintainer runs for this app.

To set it up:

1. Change the bundle id to your own and make your own signing profile, the way the signing sections above describe.
2. Enable the Push Notifications capability for that bundle id, and create an APNs authentication key (`.p8`) in the Apple developer portal.
3. Run the relay with that key, your team id and your bundle id, as its README describes.
4. Point your server's `PUSH_RELAY_URL` at your relay.

The app also needs the `aps-environment` entitlement, in `app/ios/Runner/Runner.entitlements`.
The provisioning profile has to be regenerated after the capability is enabled for the bundle id.
