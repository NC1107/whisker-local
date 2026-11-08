#!/usr/bin/env bash
# Points the robot at this server over Bluetooth. Run it on any computer within a few meters of
# the robot. Extra arguments go to `whiskerless provision`.
#
# From a computer other than the server, copy data/certs/ca.crt over and pass the details:
#   scripts/connect-robot.sh --host 192.168.1.50 --ca ./ca.crt --serial LR4C123456
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
    printf 'connect-robot: %s\n' "$1" >&2
    exit 1
}

print_rescue() {
    cat >&2 <<'EOF'

The robot may be in pairing mode with its Wi-Fi cleared, which leaves it off both the Whisker
cloud and this server. To get it back, either:
  - run scripts/connect-robot.sh again, or
  - run the set up or reconnect flow for it in the Whisker app (docs/going-back.md).
Pairing mode doesn't time out, so it will wait for you.
EOF
}

# Prints "online" once the server reports the robot as connected.
robot_is_online() {
    python3 -I - "$1" "$2" <<'PY'
import json, sys, urllib.request
url, key = sys.argv[1:3]
request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        online = json.load(response).get("online") is True
except (OSError, ValueError):
    online = False
sys.exit(0 if online else 1)
PY
}

wait_for_robot() {
    local status_url="$1" key="$2" seconds="$3" interval="${POLL_SECONDS:-5}" waited=0
    while ((waited < seconds)); do
        robot_is_online "$status_url" "$key" && return 0
        sleep "$interval"
        waited=$((waited + interval))
    done
    return 1
}

main() {
    local host="" ca="" serial="" dashboard="" key="" passthrough
    passthrough=()
    if [[ -f "$repo/.env" ]]; then
        # shellcheck disable=SC1091
        source "$repo/.env"
        host="$LAN_IP" serial="$ROBOT_SERIAL"
        ca="$repo/data/certs/ca.crt"
        dashboard="http://$LAN_IP:${DASHBOARD_PORT:-8420}"
        key="$ACCESS_KEY"
    fi

    while (($#)); do
        case "$1" in
            --host) host="${2:?}"; shift 2 ;;
            --ca) ca="${2:?}"; shift 2 ;;
            --serial) serial="${2:?}"; shift 2 ;;
            --dashboard) dashboard="${2:?}"; shift 2 ;;
            --key) key="${2:?}"; shift 2 ;;
            --dry-run)
                die "--dry-run is not supported: even a dry run needs the robot in pairing mode, which clears its Wi-Fi. Rehearse with tools/fake_robot.py instead."
                ;;
            *) passthrough+=("$1"); shift ;;
        esac
    done

    [[ -n "$host" ]] || die "pass --host with the server's LAN address"
    [[ -n "$serial" ]] || die "pass --serial with the serial from the robot's label"
    [[ -f "$ca" ]] || die "CA certificate not found: ${ca:-pass --ca}"
    command -v python3 >/dev/null || die "python3 is not installed"

    local version state venv
    version="$(sed -n 's/.*"whiskerless==\([^"]*\)".*/\1/p' "$repo/server/pyproject.toml")"
    state="${XDG_STATE_HOME:-$HOME/.local/state}/whisker-local"
    venv="$state/robot-tools-$version"
    if [[ ! -x "$venv/bin/whiskerless" ]]; then
        printf 'Installing whiskerless %s...\n' "$version"
        python3 -m venv "$venv"
        "$venv/bin/pip" install --quiet "whiskerless[ble]==$version"
    fi
    "$venv/bin/whiskerless" provision --help >/dev/null || die "whiskerless does not run on this computer"

    # This only proves the certificate matches the address, from this computer. Whether the robot
    # can reach the broker across the network is checked after provisioning.
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
  3. Next you will hold the robot's Connect button, which wipes its saved Wi-Fi. If you stop
     after that, the robot stays offline until you finish here or use the Whisker app.

EOF
    read -rp "Press Enter to continue, or Ctrl-C to stop. Nothing on the robot has changed yet: " _
    printf '\nHOLD the Connect button for about 3 seconds until the light blinks yellow. Do not just tap it.\n'
    read -rp "Press Enter once the light is blinking yellow: " _

    if ! "$venv/bin/whiskerless" provision --serial "$serial" ${passthrough[@]+"${passthrough[@]}"}; then
        print_rescue
        exit 1
    fi

    if [[ -z "$dashboard" || -z "$key" ]]; then
        printf '\nProvisioning finished. Open the dashboard to confirm the robot shows as online.\n'
        printf 'If it never does, see docs/going-back.md.\n'
        return
    fi
    printf '\nWaiting for the robot to show up on the server (up to %s seconds)...\n' "${WAIT_SECONDS:-180}"
    if wait_for_robot "$dashboard/api/status" "$key" "${WAIT_SECONDS:-180}"; then
        printf 'The robot is online. Done.\n'
    else
        printf 'The robot has not connected to the server.\n' >&2
        printf 'Check that %s:8883 is reachable from the robot'"'"'s Wi-Fi network.\n' "$host" >&2
        printf 'A guest network or a firewall between the two will block it.\n' >&2
        print_rescue
        exit 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
