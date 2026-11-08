#!/usr/bin/env bash
# Starts the broker and dashboard. Safe to run again: existing answers, certificates and history
# are kept. This never touches the robot; scripts/connect-robot.sh is the step that does.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

die() {
    printf 'install: %s\n' "$1" >&2
    exit 1
}

usage() {
    printf 'usage: ./install.sh [--ip LAN_ADDRESS] [--serial LR4C123456] [--port 8420]\n'
}

LAN_IP="" ROBOT_SERIAL="" DASHBOARD_PORT="" ACCESS_KEY=""
[[ -f .env ]] && source ./.env

while (($#)); do
    case "$1" in
        --ip) LAN_IP="${2:?--ip needs an address}"; shift 2 ;;
        --serial) ROBOT_SERIAL="${2:?--serial needs a value}"; shift 2 ;;
        --port) DASHBOARD_PORT="${2:?--port needs a value}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose is not available"

if [[ -z "$LAN_IP" ]]; then
    detected="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    [[ -t 0 ]] || die "pass --ip with this machine's LAN address"
    read -rp "LAN address the robot and your phones will reach [$detected]: " LAN_IP
    LAN_IP="${LAN_IP:-$detected}"
fi
[[ "$LAN_IP" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || die "not an IPv4 address: $LAN_IP"

if [[ -z "$ROBOT_SERIAL" ]]; then
    [[ -t 0 ]] || die "pass --serial with the serial from the robot's label"
    read -rp "Robot serial from its label (like LR4C123456): " ROBOT_SERIAL
fi
[[ "$ROBOT_SERIAL" =~ ^LR4[A-Z][0-9]{6}$ ]] || die "not a Litter-Robot 4 serial: $ROBOT_SERIAL"

DASHBOARD_PORT="${DASHBOARD_PORT:-8420}"
[[ -n "$ACCESS_KEY" ]] || ACCESS_KEY="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"

umask 077
cat >.env <<EOF
LAN_IP=$LAN_IP
DASHBOARD_PORT=$DASHBOARD_PORT
ROBOT_SERIAL=$ROBOT_SERIAL
ACCESS_KEY=$ACCESS_KEY
HOST_UID=$(id -u)
HOST_GID=$(id -g)
EOF

mkdir -p data/certs data/db
chmod 700 data
# The broker warns about a world-readable ACL and says future versions will refuse to load it.
chmod 600 broker/acl

docker compose build server
docker compose run --rm --no-deps -T \
    --volume "$PWD/data/certs:/certs:z" \
    server python -m whisker_local.certs --host "$LAN_IP" --out /certs
docker compose up -d

printf '\nRunning.\n'
printf '  Dashboard:   http://%s:%s\n' "$LAN_IP" "$DASHBOARD_PORT"
printf '  Access key:  %s\n' "$ACCESS_KEY"
printf '\nThe robot is still on the Whisker cloud. To move it here, stand next to it and run:\n'
printf '  scripts/connect-robot.sh\n'
