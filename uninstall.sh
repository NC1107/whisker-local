#!/usr/bin/env bash
# Removes the containers. History, certificates and settings stay unless you pass --purge.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

purge=false
case "${1:-}" in
    "") ;;
    --purge) purge=true ;;
    *) printf 'usage: ./uninstall.sh [--purge]\n' >&2; exit 2 ;;
esac

if [[ -f .env ]]; then
    docker compose down --rmi local --remove-orphans
fi

if $purge; then
    printf 'This deletes all visit history and the certificate the robot trusts.\n'
    read -rp 'Type purge to confirm: ' answer
    if [[ "$answer" == purge ]]; then
        rm -rf data .env
        printf 'Deleted data/ and .env.\n'
    else
        printf 'Kept data/ and .env.\n'
    fi
else
    printf 'Containers removed. History and certificates are still in data/.\n'
fi

printf '\nIf the robot still points here, run the reconnect flow for it in the Whisker app.\n'
printf 'See docs/going-back.md.\n'
