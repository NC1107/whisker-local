#!/usr/bin/env bash
# One-time, idempotent Apple setup for Litterbox Local through the App Store Connect API:
# bundle id, then a distribution certificate, then the App Store provisioning profile.
# It cannot create the App Store Connect app record, that is only possible in the web UI.
#
# Usage: asc-setup.sh [--dry-run] [--cert-id ID] [--create-cert CSR_FILE [--cer-out FILE]]
# stdout: base64 of the provisioning profile (pipe into gh secret set).
# stderr: HTTP status and a one-line summary per step.
# Env: ASC_API_KEY_BASE64, ASC_KEY_ID, ASC_ISSUER_ID.
set -euo pipefail

BUNDLE_ID="top.npcserver.whiskerlocal"
APP_NAME="Litterbox Local"
PROFILE_NAME="Whisker Local App Store Distribution"
CERT_ID="" CSR_FILE="" CER_OUT="whisker-local-distribution.cer"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) export ASC_DRY_RUN=1 ;;
    --cert-id) CERT_ID=$2; shift ;;
    --create-cert) CSR_FILE=$2; shift ;;
    --cer-out) CER_OUT=$2; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# shellcheck source=scripts/asc-jwt.sh
. "$(dirname "${BASH_SOURCE[0]}")/asc-jwt.sh"

say() { echo "  $*" >&2; }
dry() { [ "${ASC_DRY_RUN:-0}" = 1 ]; }
need_ok() { # accepted status codes as args
  dry && return 0
  local c
  for c in "$@"; do [ "$ASC_STATUS" = "$c" ] && return 0; done
  echo "unexpected HTTP $ASC_STATUS: $ASC_BODY" >&2
  exit 1
}

echo "1. bundle id $BUNDLE_ID" >&2
asc_api GET "/v1/bundleIds?filter%5Bidentifier%5D=$BUNDLE_ID&limit=200"
need_ok 200
bundle_rid=$(jq -r --arg b "$BUNDLE_ID" '.data[] | select(.attributes.identifier == $b) | .id' <<<"$ASC_BODY" | head -1)
if [ -z "$bundle_rid" ]; then
  asc_api POST /v1/bundleIds "$(jq -nc --arg i "$BUNDLE_ID" --arg n "$APP_NAME" \
    '{data:{type:"bundleIds",attributes:{identifier:$i,name:$n,platform:"IOS"}}}')"
  need_ok 201
  bundle_rid=$(jq -r '(.data | objects | .id) // "DRY_BUNDLE_ID"' <<<"$ASC_BODY")
  say "registered $BUNDLE_ID as '$APP_NAME' (id $bundle_rid)"
else
  say "already registered (id $bundle_rid)"
fi
bundle_rid=${bundle_rid:-DRY_BUNDLE_ID}

echo "2. distribution certificate" >&2
if [ -n "$CSR_FILE" ]; then
  csr=$(grep -v '^-----' "$CSR_FILE" | tr -d '\n')
  asc_api POST /v1/certificates "$(jq -nc --arg c "$csr" \
    '{data:{type:"certificates",attributes:{certificateType:"DISTRIBUTION",csrContent:$c}}}')"
  need_ok 201
  if ! dry; then
    CERT_ID=$(jq -r '.data.id' <<<"$ASC_BODY")
    jq -r '.data.attributes.certificateContent' <<<"$ASC_BODY" | base64 --decode >"$CER_OUT"
    say "created certificate $CERT_ID, DER written to $CER_OUT (import it next to the CSR's private key)"
  fi
fi
asc_api GET "/v1/certificates?filter%5BcertificateType%5D=DISTRIBUTION&limit=200"
need_ok 200
jq -r '.data[]? | "  \(.id)  \(.attributes.name)  expires \(.attributes.expirationDate)"' <<<"$ASC_BODY" >&2
if [ -z "$CERT_ID" ]; then
  count=$(jq '.data | length' <<<"$ASC_BODY")
  if [ "$count" = 1 ]; then
    CERT_ID=$(jq -r '.data[0].id' <<<"$ASC_BODY")
    say "using the only distribution certificate: $CERT_ID"
  elif dry; then
    CERT_ID=DRY_CERT_ID
  else
    echo "$count distribution certificates found; pick one with --cert-id" >&2
    exit 1
  fi
fi

echo "3. provisioning profile '$PROFILE_NAME'" >&2
asc_api GET "/v1/profiles?filter%5Bname%5D=$(jq -rn --arg n "$PROFILE_NAME" '$n|@uri')&include=certificates,bundleId&limit=50"
need_ok 200
# A profile is reusable only if it is active and signed by the chosen certificate.
profile=$(jq -c --arg c "$CERT_ID" --arg b "$bundle_rid" --arg n "$PROFILE_NAME" '
  [.data[]? | select(.attributes.name == $n and .attributes.profileState == "ACTIVE"
    and (.relationships.certificates.data | map(.id) | index($c))
    and (.relationships.bundleId.data.id == $b))] | .[0] // empty' <<<"$ASC_BODY")
if [ -n "$profile" ]; then
  say "reusing active profile $(jq -r .id <<<"$profile")"
else
  # Names are unique per account, so a stale profile must go before its replacement.
  for stale in $(jq -r --arg n "$PROFILE_NAME" '.data[]? | select(.attributes.name == $n) | .id' <<<"$ASC_BODY"); do
    asc_api DELETE "/v1/profiles/$stale"
    need_ok 204
    say "deleted stale profile $stale"
  done
  asc_api POST /v1/profiles "$(jq -nc --arg n "$PROFILE_NAME" --arg b "$bundle_rid" --arg c "$CERT_ID" \
    '{data:{type:"profiles",attributes:{name:$n,profileType:"IOS_APP_STORE"},
      relationships:{bundleId:{data:{type:"bundleIds",id:$b}},
        certificates:{data:[{type:"certificates",id:$c}]}}}}')"
  need_ok 201
  profile=$(jq -c '(.data | objects) // {}' <<<"$ASC_BODY")
  say "created profile $(jq -r '.id // "DRY"' <<<"$profile")"
fi

if dry; then
  say "dry run: no profile emitted"
  exit 0
fi
jq -r '.attributes.profileContent' <<<"$profile" | tr -d '\n'
echo >&2
say "profile written to stdout as base64"
