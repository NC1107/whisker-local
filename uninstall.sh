#!/usr/bin/env bash
# Removes the containers and the images they ran from. History, certificates and settings stay
# unless you pass --purge.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

purge=false
case "${1:-}" in
    "") ;;
    --purge) purge=true ;;
    *) printf 'usage: ./uninstall.sh [--purge]\n' >&2; exit 2 ;;
esac

removed_containers=false
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    # Without .env, compose can't read compose.yaml, and these values are not used to remove anything.
    if [[ ! -f .env ]]; then
        export LAN_IP=127.0.0.1 ROBOT_SERIAL=none ACCESS_KEY=none TZ=UTC HOST_UID=0 HOST_GID=0
    fi
    docker compose --profile demo down --remove-orphans
    # By name rather than compose's --rmi all: a run of that took an image tag belonging to a
    # different compose project with it. Removing one name leaves an image others still use alone.
    docker compose config --images | while read -r image; do
        docker image rm "$image" >/dev/null 2>&1 || true
    done
    removed_containers=true
else
    printf 'docker is not available here, so no containers or images were touched.\n' >&2
fi

if $removed_containers; then
    printf 'Containers are removed, and so are their images unless something else still uses them.\n'
fi

if $purge; then
    printf 'This deletes all visit history and the certificate the robot trusts.\n'
    read -rp 'Type purge to confirm: ' answer
    if [[ "$answer" == purge ]]; then
        rm -rf data .env "${XDG_STATE_HOME:-$HOME/.local/state}/whisker-local"
        printf 'Deleted data/, .env and the Bluetooth tools connect-robot.sh installed.\n'
    else
        printf 'Kept data/, .env and the Bluetooth tools.\n'
    fi
else
    printf 'History and certificates are still in data/.\n'
fi

printf '\nStill on this machine, because other things may use them:\n'
printf '  the docker build cache, to clear it: docker builder prune\n'
printf '  the base images the build pulled, to list them: docker images python debian\n'
printf '\nIf the robot still points here, run the reconnect flow for it in the Whisker app.\n'
printf 'See docs/going-back.md.\n'
