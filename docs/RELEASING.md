# Releasing

This is for the maintainer.
If you just run the server, you don't need any of it.

## What a push to main does

1. Runs the server tests and the flutter analyze and test steps, so a broken merge can't ship.
2. Picks the version from the commit messages since the last tag: `feat:` is a minor bump, a breaking change is a major bump, anything else is a patch.
3. Tags the commit.
4. Builds the server image and pushes it to `ghcr.io/nc1107/whisker-local` as `latest` and as the version, which is what `install.sh` pulls.
5. Builds the android apk.
6. Builds the iOS app on a macOS runner and uploads it to testflight, only if the iOS secrets exist.
7. Creates a github release with the apk attached.

The build number is the run number times 100 plus the run attempt, so it only goes up and a re-run never collides.
The twice-monthly schedule and manual runs only rebuild the latest tag for testflight, so the newest build doesn't hit the 90 day expiry.
A manual run with `dry_run` builds, signs and validates the IPA from the branch you pick and stops, so it's how a signing change gets tested before main.

## Secrets

They go under repo Settings, Secrets and variables, Actions.
The release works without any of them, you just get a debug-signed apk and no iOS build.

| Secret | Without it |
|--------|------------|
| `ANDROID_KEYSTORE_BASE64` | The apk is signed with the debug key, so a later real-key build can't upgrade over it |
| `ANDROID_KEY_PROPERTIES` | Same, the gradle file only uses the release key when this file exists |
| `IOS_CERTIFICATE_BASE64` | No iOS job at all |
| `IOS_CERTIFICATE_PASSWORD` | The iOS job fails |
| `IOS_PROVISIONING_PROFILE_BASE64` | The iOS job fails |
| `ASC_API_KEY_BASE64` | The iOS job fails |
| `ASC_KEY_ID` | The iOS job fails |
| `ASC_ISSUER_ID` | The iOS job fails |

So the iOS ones are all or nothing, once the certificate is set the other five have to be too.

Where they come from:

- Keystore: `keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload`, then `base64 -w0 upload-keystore.jks`.
- `ANDROID_KEY_PROPERTIES` is four lines: `storePassword`, `keyPassword`, `keyAlias=upload` and `storeFile=upload-keystore.jks`.
  Both files are git-ignored, so locally you can drop them in `app/android/` and the release build picks them up.
- `IOS_CERTIFICATE_BASE64` is an Apple Distribution certificate with its private key, exported as a `.p12` and run through `base64 -w0`.
- `IOS_CERTIFICATE_PASSWORD` is the password you picked for that `.p12`.
- `IOS_PROVISIONING_PROFILE_BASE64` is the App Store profile named "Whisker Local App Store Distribution", through `base64 -w0`.
- `ASC_API_KEY_BASE64` is the `.p8` from App Store Connect (Users and Access, Integrations), through `base64 -w0`.
  `ASC_KEY_ID` is shown next to it and `ASC_ISSUER_ID` is at the top of that page.

## How the iOS build is signed

The Release configuration of the Runner target is on manual signing with the identity "Apple Distribution" and the profile name above.
The team id is in the Xcode project, and `app/ios/ExportOptions.plist` repeats the same values for the export step.
Debug, Profile and the no-codesign build don't need any of it.
The bundle id and the android application id are both `top.npcserver.whiskerlocal`.

The workflow imports the certificate into a throwaway keychain, builds with `flutter build ipa`, then `scripts/check-ipa.sh` checks the version, build number and signature.
After the upload, `scripts/asc-wait.sh` polls App Store Connect until the build finishes processing.
It uses CocoaPods and not Swift Package Manager, because SPM gave a white launch screen, so the workflow turns it off.

## What only you can do

- Create the app record in App Store Connect, it's only possible in the web UI. Use the bundle id above.
- Create the provisioning profile, with exactly that name. Either the developer portal or `scripts/asc-setup.sh`.
- Export the distribution certificate. `asc-setup.sh --create-cert` can make one from a CSR, but the private key has to come from your machine.
- Add the secrets, nothing sets them for you.
- Play Store: there's no upload step, the apk only goes on the github release.

### scripts/asc-setup.sh

It talks to the App Store Connect API and is safe to run more than once.
It registers the bundle id if it's missing, finds your distribution certificate, and creates the profile or reuses an active one.
The profile comes out on stdout as base64, so you can pipe it into the secret.
With more than one distribution certificate it stops and asks for `--cert-id`.

```bash
export ASC_API_KEY_BASE64=... ASC_KEY_ID=... ASC_ISSUER_ID=...
scripts/asc-setup.sh --dry-run
scripts/asc-setup.sh | gh secret set IOS_PROVISIONING_PROFILE_BASE64
```

`--dry-run` only prints the requests it would make.

## Running your own push relay

The maintainer's relay only reaches the published iOS app, because its Apple push key belongs to that app.
If you build the app under your own bundle id you need your own relay.
The server only needs two calls from it, `POST /v1/register` and `POST /v1/send`, see `server/src/whisker_local/push.py` for the exact shape.

1. Change the bundle id and make your own signing profile, as above.
2. Enable the Push Notifications capability for it and create an APNs key (`.p8`) in the Apple developer portal.
3. Run a relay that holds that key and implements the two calls.
4. Point `PUSH_RELAY_URL` at it.

The app also needs the `aps-environment` entitlement in `app/ios/Runner/Runner.entitlements`.
Regenerate the provisioning profile after you enable the capability.
