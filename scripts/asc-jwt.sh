#!/usr/bin/env bash
# Sourced helper: mints App Store Connect JWTs and wraps curl for the ASC API.
# Needs ASC_API_KEY_BASE64 (base64 of the .p8), ASC_KEY_ID, ASC_ISSUER_ID in the env.
# Set ASC_DRY_RUN=1 to print requests instead of sending them.

ASC_API_BASE="${ASC_API_BASE:-https://api.appstoreconnect.apple.com}"

asc_b64url() {
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

# openssl emits ECDSA signatures as DER, but JWS ES256 needs raw r||s, 32 bytes each.
asc_der_to_raw() {
  local der_file=$1 r s
  r=$(openssl asn1parse -inform DER -in "$der_file" | awk -F: '/INTEGER/ {print $NF}' | sed -n 1p)
  s=$(openssl asn1parse -inform DER -in "$der_file" | awk -F: '/INTEGER/ {print $NF}' | sed -n 2p)
  # Integers may carry a 00 sign byte or be shorter than 32 bytes, so strip then left-pad.
  r=${r#"${r%%[!0]*}"}
  s=${s#"${s%%[!0]*}"}
  printf '%064s%064s' "$r" "$s" | tr ' ' 0 | perl -e 'print pack("H*", <STDIN>)'
}

asc_jwt() {
  : "${ASC_API_KEY_BASE64:?ASC_API_KEY_BASE64 is required}"
  : "${ASC_KEY_ID:?ASC_KEY_ID is required}"
  : "${ASC_ISSUER_ID:?ASC_ISSUER_ID is required}"
  local tmp header claims now signing sig_der
  tmp=$(mktemp -d)
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  base64 --decode <<<"$ASC_API_KEY_BASE64" >"$tmp/key.p8"
  now=$(date +%s)
  header=$(printf '{"alg":"ES256","kid":"%s","typ":"JWT"}' "$ASC_KEY_ID" | asc_b64url)
  # Apple rejects tokens valid for more than 20 minutes.
  claims=$(printf '{"iss":"%s","iat":%d,"exp":%d,"aud":"appstoreconnect-v1"}' \
    "$ASC_ISSUER_ID" "$now" $((now + 1100)) | asc_b64url)
  signing="$header.$claims"
  printf '%s' "$signing" | openssl dgst -sha256 -sign "$tmp/key.p8" -out "$tmp/sig.der"
  sig_der=$(asc_der_to_raw "$tmp/sig.der" | asc_b64url)
  printf '%s.%s' "$signing" "$sig_der"
}

# asc_api METHOD PATH [JSON_BODY]
# Leaves the response body in $ASC_BODY and the HTTP status in $ASC_STATUS. Status and a
# one-line summary go to stderr so stdout stays free for machine-readable output.
asc_api() {
  local method=$1 path=$2 body=${3:-} token
  if [ "${ASC_DRY_RUN:-0}" = 1 ]; then
    echo "DRY-RUN $method $ASC_API_BASE$path ${body:+body=$body}" >&2
    ASC_STATUS=000
    ASC_BODY='{"data":[]}'
    return 0
  fi
  token=$(asc_jwt)
  local out
  out=$(mktemp)
  local args=(-sS -o "$out" -w '%{http_code}' -X "$method" -H "Authorization: Bearer $token")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' -d "$body")
  ASC_STATUS=$(curl "${args[@]}" "$ASC_API_BASE$path")
  # shellcheck disable=SC2034 # read by the scripts that source this file
  ASC_BODY=$(cat "$out")
  rm -f "$out"
  echo "$method $path -> HTTP $ASC_STATUS" >&2
}
