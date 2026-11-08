#!/usr/bin/env bash
# Points the robot at this server over Bluetooth. Run it on any computer within a few meters of
# the robot. Extra arguments go to `whiskerless provision`, for example --dry-run.
#
# From a computer other than the server, copy data/certs/ca.crt over and pass the details:
#   scripts/connect-robot.sh --host 10.0.0.100 --ca ./ca.crt --serial LR4C123456
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
    printf 'connect-robot: %s\n' "$1" >&2
    exit 1
}

host="" ca="" serial=""
if [[ -f "$repo/.env" ]]; then
    # shellcheck disable=SC1091
    source "$repo/.env"
    host="$LAN_IP" serial="$ROBOT_SERIAL"
    ca="$repo/data/certs/ca.crt"
fi

passthrough=()
while (($#)); do
    case "$1" in
        --host) host="${2:?}"; shift 2 ;;
        --ca) ca="${2:?}"; shift 2 ;;
        --serial) serial="${2:?}"; shift 2 ;;
        *) passthrough+=("$1"); shift ;;
    esac
done

[[ -n "$host" ]] || die "pass --host with the server's LAN address"
[[ -n "$serial" ]] || die "pass --serial with the serial from the robot's label"
[[ -f "$ca" ]] || die "CA certificate not found: ${ca:-pass --ca}"
command -v python3 >/dev/null || die "python3 is not installed"

version="$(sed -n 's/.*"whiskerless==\([^"]*\)".*/\1/p' "$repo/server/pyproject.toml")"
state="${XDG_STATE_HOME:-$HOME/.local/state}/whisker-local"
venv="$state/robot-tools-$version"
if [[ ! -x "$venv/bin/whiskerless" ]]; then
    printf 'Installing whiskerless %s...\n' "$version"
    python3 -m venv "$venv"
    "$venv/bin/pip" install --quiet "whiskerless[ble]==$version"
fi

# Pairing mode wipes the robot's saved Wi-Fi and there is no way out except finishing, so the
# broker has to be proven reachable and trustworthy before anyone holds that button.
printf 'Checking the broker at %s:8883...\n' "$host"
"$venv/bin/python" -I - "$host" "$ca" <<'PY' || die "the broker is not ready. Run ./install.sh on the server first."
import socket, ssl, sys
host, ca = sys.argv[1:3]
context = ssl.create_default_context(cafile=ca)
# The robot's TLS stack accepts this CA. Python's strict mode is pickier than the robot is.
context.verify_flags &= ~ssl.VERIFY_X509_STRICT
try:
    with socket.create_connection((host, 8883), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=host):
            pass
except OSError as error:
    print(f"  {error}", file=sys.stderr)
    sys.exit(1)
PY
printf '  OK\n'

export WHISKERLESS_HOME="$state/whiskerless" WHISKERLESS_NO_UPDATE_CHECK=1
# --auth anonymous is the reversible choice: the robot keeps the certificate it shipped with.
"$venv/bin/whiskerless" setup --host "$host" --auth anonymous --ca "$ca"

cat <<'EOF'

Before you continue:
  1. The Whisker app stops working for this robot until you go back (docs/going-back.md).
  2. Have the name and password of a 2.4 GHz Wi-Fi network ready.
  3. HOLD the robot's Connect button for about 3 seconds until the light blinks yellow.
     Do not just tap it. Holding it wipes the robot's Wi-Fi, so only do this if you will finish.

EOF
read -rp "Press Enter once the light is blinking yellow, or Ctrl-C to stop: " _

exec "$venv/bin/whiskerless" provision --serial "$serial" "${passthrough[@]}"
