#!/usr/bin/env bash
# Checks an exported IPA before it goes to App Store Connect: the app carries the expected
# version and build number and its signature verifies. Usage: check-ipa.sh <ipa> <version> <build-number>
set -euo pipefail

ipa=$1
want_version=$2
want_build=$3
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
unzip -q "$ipa" -d "$work"

app=$(find "$work/Payload" -maxdepth 1 -name '*.app' | head -1)
bundles=("$app")

failed=0
for bundle in "${bundles[@]}"; do
  name=${bundle#"$work/Payload/"}
  if [ ! -d "$bundle" ]; then
    echo "::error::$name is missing from the IPA"
    failed=1
    continue
  fi
  plist=$bundle/Info.plist
  id=$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$plist")
  version=$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$plist")
  build=$(/usr/libexec/PlistBuddy -c 'Print CFBundleVersion' "$plist")
  echo "$name: $id $version ($build)"
  if [ "$version" != "$want_version" ] || [ "$build" != "$want_build" ]; then
    echo "::error::$name is $version ($build), expected $want_version ($want_build)"
    failed=1
  fi
  if ! codesign --verify --strict --verbose=1 "$bundle"; then
    echo "::error::$name has an invalid signature"
    failed=1
  fi
  codesign -d --entitlements - --xml "$bundle" 2>/dev/null \
    | plutil -convert json -o - - 2>/dev/null \
    | python3 -c 'import json,sys; e=json.load(sys.stdin); print("  entitlements:", ", ".join(sorted(e)))' \
    || true
done

exit "$failed"
