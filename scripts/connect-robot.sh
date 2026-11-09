#!/usr/bin/env bash
# Points the robot at this server over Bluetooth. Extra arguments go to `whiskerless provision`.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'EOF'
usage: scripts/connect-robot.sh [--host ADDRESS --ca FILE --serial LR4C123456]
                                [--key ACCESS_KEY [--dashboard URL]] [--forget-old-server]

Run it within a few meters of the robot, on a computer with Bluetooth and python3. The first run
downloads the Bluetooth tools with pip, so it needs internet access.

On the server it reads everything from .env. From another computer, copy data/certs/ca.crt over
and pass the server's details:
  scripts/connect-robot.sh --host 192.168.1.50 --ca ./ca.crt --serial LR4C123456 --key KEY

  --key        the access key from .env, so the script can wait until the robot shows up
  --dashboard  the dashboard address for that wait, default http://HOST:8420
  --forget-old-server
               for a computer that was set up for an earlier install of the server: it holds that
               install's certificate and refuses the new one, so this moves the old files aside
               (nothing is deleted) and starts fresh
EOF
}

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
# Cloudflare refuses Python's default user agent, which would look like an offline robot.
headers = {"Authorization": f"Bearer {key}", "User-Agent": "whisker-local"}
request = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        online = json.load(response).get("online") is True
except (OSError, ValueError):
    online = False
sys.exit(0 if online else 1)
PY
}

# whiskerless refuses a CA that differs from the one it holds, because replacing it strands robots
# that trust the old one. A reinstalled server looks exactly like that, and this run provisions
# the robot with the new one anyway.
handle_old_server() {
    local state="$1" ca="$2" forget="$3" remembered="$1/whiskerless/ca/ca.crt"
    [[ -f "$remembered" ]] || return 0
    [[ "$(<"$remembered")" != "$(<"$ca")" ]] || return 0
    if [[ "$forget" != true ]]; then
        die "this computer remembers a different server certificate, probably from before a reinstall.
  A robot set up for the old one won't trust this server until it is connected again, which is what
  this run does. To go ahead, run it again with --forget-old-server. Nothing is deleted, the old
  files are moved to $state/whiskerless.old-<time>."
    fi
    local aside
    aside="$state/whiskerless.old-$(date +%Y%m%d-%H%M%S)"
    mv "$state/whiskerless" "$aside"
    printf '  Moved the old server certificate to %s\n' "$aside"
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
    local host="" ca="" serial="" dashboard="" key="" forget_old=false passthrough
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
            --forget-old-server) forget_old=true; shift ;;
            -h | --help) usage; exit 0 ;;
            --dry-run)
                die "--dry-run is not supported: even a dry run needs the robot in pairing mode, which clears its Wi-Fi. Rehearse with tools/fake_robot.py instead."
                ;;
            *) passthrough+=("$1"); shift ;;
        esac
    done

    [[ -n "$host" ]] || die "pass --host with the server's LAN address"
    [[ -n "$serial" ]] || die "pass --serial with the serial from the robot's label"
    [[ -f "$ca" ]] || die "CA certificate not found: ${ca:-pass --ca}"
    command -v python3 >/dev/null || die "python3 is not installed, it is needed to run the Bluetooth tools"
    [[ -n "$dashboard" ]] || dashboard="http://$host:8420"

    local version state venv
    version="$(sed -n 's/.*"whiskerless==\([^"]*\)".*/\1/p' "$repo/server/pyproject.toml")"
    state="${XDG_STATE_HOME:-$HOME/.local/state}/whisker-local"
    venv="$state/robot-tools-$version"
    if [[ ! -x "$venv/bin/whiskerless" ]]; then
        printf 'Installing whiskerless %s (needs internet access, once)...\n' "$version"
        python3 -m venv "$venv"
        "$venv/bin/pip" install --quiet --disable-pip-version-check "whiskerless[ble]==$version" ||
            die "could not install whiskerless, check the internet connection"
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
    handle_old_server "$state" "$ca" "$forget_old"
    # Its closing advice is to install the broker certificate, which install.sh already did, so
    # only a failure is shown.
    # --auth anonymous is the reversible choice: the robot keeps the certificate it shipped with.
    local setup_output
    if ! setup_output="$("$venv/bin/whiskerless" setup --host "$host" --auth anonymous --ca "$ca" 2>&1)"; then
        printf '%s\n' "$setup_output" >&2
        die "whiskerless could not prepare this computer"
    fi
    printf '  This computer is set up for the broker at %s:8883.\n' "$host"

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
