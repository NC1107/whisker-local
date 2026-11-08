#!/usr/bin/env bash
# Polls App Store Connect until the uploaded build finishes processing.
# Env: ASC_API_KEY_BASE64, ASC_KEY_ID, ASC_ISSUER_ID, BUILD_NUMBER.
# Exit 0 on VALID, 1 on INVALID/FAILED or after the 30 minute timeout.
set -euo pipefail

BUNDLE_ID="${BUNDLE_ID:-top.npcserver.whiskerlocal}"
: "${BUILD_NUMBER:?BUILD_NUMBER is required}"
INTERVAL="${ASC_POLL_INTERVAL:-30}"
DEADLINE=$((SECONDS + ${ASC_POLL_TIMEOUT:-1800}))

# shellcheck source=scripts/asc-jwt.sh
. "$(dirname "${BASH_SOURCE[0]}")/asc-jwt.sh"

asc_api GET "/v1/apps?filter%5BbundleId%5D=$BUNDLE_ID"
[ "$ASC_STATUS" = 200 ] || { echo "app lookup failed: $ASC_BODY" >&2; exit 1; }
app_id=$(jq -r --arg b "$BUNDLE_ID" '.data[] | select(.attributes.bundleId == $b) | .id' <<<"$ASC_BODY" | head -1)
[ -n "$app_id" ] || { echo "no App Store Connect app record for $BUNDLE_ID" >&2; exit 1; }

while [ "$SECONDS" -lt "$DEADLINE" ]; do
  asc_api GET "/v1/builds?filter%5Bapp%5D=$app_id&filter%5Bversion%5D=$BUILD_NUMBER&limit=1"
  if [ "$ASC_STATUS" = 200 ]; then
    state=$(jq -r '.data[0].attributes.processingState // "NOT_FOUND_YET"' <<<"$ASC_BODY")
  else
    state="API_ERROR_$ASC_STATUS"
  fi
  echo "build $BUILD_NUMBER processingState: $state"
  case "$state" in
    VALID) exit 0 ;;
    INVALID | FAILED) exit 1 ;;
  esac
  sleep "$INTERVAL"
done
echo "timed out waiting for build $BUILD_NUMBER to finish processing" >&2
exit 1
