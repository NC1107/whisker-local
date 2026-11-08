#!/usr/bin/env bash
# Starts the broker and dashboard. Safe to run again: existing answers, certificates and history
# are kept. This never touches the robot; scripts/connect-robot.sh is the step that does.
set -euo pipefail

die() {
    printf 'install: %s\n' "$1" >&2
    exit 1
}

usage() {
    cat <<'EOF'
usage: ./install.sh [--ip LAN_ADDRESS] [--serial LR4C123456] [--port 8420]
                    [--timezone Area/City] [--build] [--allow-loopback]

  --build           build the server image here instead of pulling the published one
  --allow-loopback  accept a 127.x address, for trying this on one machine (a robot can't reach it)
EOF
}

allow_loopback=false

local_addresses() {
    if command -v ip >/dev/null; then
        ip -4 -o addr show 2>/dev/null | awk '{ sub(/\/.*/, "", $4); print $4 }'
    elif command -v ifconfig >/dev/null; then
        ifconfig 2>/dev/null | awk '$1 == "inet" { print $2 }'
    fi
}

# Prints the reason when the address is unusable. The robot is told this exact address, so one
# it can't reach leaves it with a broker it will never find.
lan_ip_problem() {
    local address="$1" octets octet
    [[ "$address" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "not an IPv4 address"; return; }
    IFS=. read -ra octets <<<"$address"
    for octet in "${octets[@]}"; do
        ((10#$octet <= 255)) || { echo "$octet is not a valid octet"; return; }
    done
    local a=$((10#${octets[0]})) b=$((10#${octets[1]}))
    if ((a == 127)); then
        $allow_loopback || echo "that is this machine only, the robot can't reach it"
        return
    fi
    if ((a == 0)); then echo "that is not an address on your network"; return; fi
    if ((a == 169 && b == 254)); then echo "that is a link-local address"; return; fi
    if ((a == 100 && b >= 64 && b <= 127)); then
        echo "that is a Tailscale or carrier-grade NAT address, the robot is not on that network"
        return
    fi
    if ((a == 172 && b == 17)); then echo "that is docker's own bridge network"; return; fi
    if ! ((a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168))); then
        echo "that is not a private LAN address"
        return
    fi
    if command -v ip >/dev/null || command -v ifconfig >/dev/null; then
        local_addresses | grep -qxF "$address" || echo "this machine doesn't have that address"
    fi
}

detect_lan_ip() {
    local routed candidates
    routed="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    candidates="$(local_addresses | while read -r address; do
        [[ -z "$(lan_ip_problem "$address")" ]] && echo "$address"
    done)"
    if [[ -n "$routed" ]] && grep -qxF "$routed" <<<"$candidates"; then
        echo "$routed"
    else
        head -n1 <<<"$candidates"
    fi
}

detect_timezone() {
    local zone=""
    if command -v timedatectl >/dev/null; then
        zone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    fi
    if [[ -z "$zone" && -L /etc/localtime ]]; then
        zone="$(readlink /etc/localtime | sed -n 's|.*/zoneinfo/||p')"
    fi
    if [[ -z "$zone" && -r /etc/timezone ]]; then
        zone="$(head -n1 /etc/timezone)"
    fi
    echo "$zone"
}

timezone_problem() {
    local zone="$1"
    [[ "$zone" =~ ^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$ ]] || { echo "not a timezone name"; return; }
    if [[ -d /usr/share/zoneinfo && ! -e "/usr/share/zoneinfo/$zone" ]]; then
        echo "no such timezone"
    fi
}

checksum_certs() {
    { cat data/certs/ca.crt data/certs/server.crt data/certs/server.key 2>/dev/null || true; } | cksum
}

# A published image that can't be pulled (not released yet, offline, another architecture) is not
# an error, the same tag is built from this checkout instead.
# A client may only touch this robot's topics, so made-up client ids have nowhere to leave retained
# messages. The broker warns about a world-readable ACL and says future versions will refuse it.
write_broker_acl() {
    local serial="$1"
    cat >data/acl <<ACL
# The robot reads its command topic and writes its state and activity. The server commands the
# robot over the internal listener, which has no ACL, so no LAN client holds write access to the
# command topic.
topic read prod/LR4/$serial/command
topic write prod/LR4/$serial/state
topic write prod/LR4/$serial/activity
ACL
    chmod 600 data/acl
}

prepare_server_image() {
    if ! $force_build && docker compose pull --quiet server 2>/dev/null; then
        printf 'Using the published server image.\n'
        return
    fi
    $force_build || printf 'The published server image could not be pulled, building it here.\n'
    docker compose build server
}

main() {
    cd "$(dirname "${BASH_SOURCE[0]}")"

    local broker_acl_before="" broker_acl_after="" LAN_IP="" ROBOT_SERIAL="" DASHBOARD_PORT="" ACCESS_KEY="" TZ="${TZ:-}" problem
    local force_build=false
    # shellcheck disable=SC1091
    [[ -f .env ]] && source ./.env

    while (($#)); do
        case "$1" in
            --ip) LAN_IP="${2:?--ip needs an address}"; shift 2 ;;
            --serial) ROBOT_SERIAL="${2:?--serial needs a value}"; shift 2 ;;
            --port) DASHBOARD_PORT="${2:?--port needs a value}"; shift 2 ;;
            --timezone) TZ="${2:?--timezone needs a value}"; shift 2 ;;
            --build) force_build=true; shift ;;
            --allow-loopback) allow_loopback=true; shift ;;
            -h | --help) usage; exit 0 ;;
            *) usage >&2; die "unknown option: $1" ;;
        esac
    done

    command -v docker >/dev/null || die "docker is not installed"
    docker compose version >/dev/null 2>&1 || die "docker compose is not available"

    if [[ -z "$LAN_IP" ]]; then
        [[ -t 0 ]] || die "pass --ip with this machine's LAN address"
        local detected
        detected="$(detect_lan_ip)"
        read -rp "LAN address the robot and your phones will reach [$detected]: " LAN_IP
        LAN_IP="${LAN_IP:-$detected}"
    fi
    problem="$(lan_ip_problem "$LAN_IP")"
    [[ -z "$problem" ]] || die "can't use $LAN_IP: $problem"

    if [[ -z "$ROBOT_SERIAL" ]]; then
        [[ -t 0 ]] || die "pass --serial with the serial from the robot's label"
        read -rp "Robot serial from its label (like LR4C123456): " ROBOT_SERIAL
    fi
    [[ "$ROBOT_SERIAL" =~ ^LR4[A-Z][0-9]{6}$ ]] || die "not a Litter-Robot 4 serial: $ROBOT_SERIAL"

    DASHBOARD_PORT="${DASHBOARD_PORT:-8420}"
    if ! [[ "$DASHBOARD_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$DASHBOARD_PORT < 1 || 10#$DASHBOARD_PORT > 65535)); then
        die "not a port number: $DASHBOARD_PORT"
    fi

    # Daily totals are cut at midnight, so a server container on UTC would split a day wrongly.
    [[ -n "$TZ" ]] || TZ="$(detect_timezone)"
    if [[ -z "$TZ" ]]; then
        printf 'install: could not detect this machine'"'"'s timezone, using UTC. Rerun with --timezone to change it.\n' >&2
        TZ=UTC
    fi
    problem="$(timezone_problem "$TZ")"
    [[ -z "$problem" ]] || die "can't use timezone $TZ: $problem"

    [[ -n "$ACCESS_KEY" ]] || ACCESS_KEY="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"

    umask 077
    cat >.env <<EOF
LAN_IP=$LAN_IP
DASHBOARD_PORT=$DASHBOARD_PORT
ROBOT_SERIAL=$ROBOT_SERIAL
ACCESS_KEY=$ACCESS_KEY
TZ=$TZ
HOST_UID=$(id -u)
HOST_GID=$(id -g)
EOF

    mkdir -p data/certs data/db
    chmod 700 data
    broker_acl_before="$(cksum data/acl 2>/dev/null || true)"
    write_broker_acl "$ROBOT_SERIAL"
    broker_acl_after="$(cksum data/acl)"

    printf 'The robot will be told to connect to %s:8883 and daily totals use %s.\n' "$LAN_IP" "$TZ"

    prepare_server_image
    local certs_before certs_after broker_was_running
    broker_was_running="$(docker compose ps --status running --quiet broker 2>/dev/null || true)"
    certs_before="$(checksum_certs)"
    docker compose run --rm --no-deps -T \
        --volume "$PWD/data/certs:/certs:z" \
        server python -m whisker_local.certs --host "$LAN_IP" --out /certs
    certs_after="$(checksum_certs)"
    docker compose up -d
    # Mosquitto only reads its certificate at startup, and a single-file bind mount follows the old
    # file if it was replaced, so a reissued certificate or a new ACL needs the container recreated.
    if [[ -n "$broker_was_running" && ("$certs_before" != "$certs_after" || "$broker_acl_before" != "$broker_acl_after") ]]; then
        docker compose up -d --force-recreate broker
    fi

    printf '\nRunning.\n'
    printf '  Dashboard:   http://%s:%s\n' "$LAN_IP" "$DASHBOARD_PORT"
    printf '  Access key:  %s\n' "$ACCESS_KEY"
    printf '\nThe robot is still on the Whisker cloud. To move it here, stand next to it and run:\n'
    printf '  scripts/connect-robot.sh\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
