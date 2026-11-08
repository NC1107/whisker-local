#!/usr/bin/env bash
# Stops the sample-data demo and sets the sample database aside, so that the real robot starts
# from an empty history. Nothing is deleted: the sample data moves to data/db-demo-<time>.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

die() {
    printf 'end-demo: %s\n' "$*" >&2
    exit 1
}

[[ -f .env ]] || die "there is no .env here, so there is no demo to end"
grep -q '^DEMO_MODE=1$' .env || die "DEMO_MODE=1 is not set in .env, so this is not running the demo"
[[ -d data/db ]] || die "data/db is missing"

docker compose --profile demo rm --stop --force demo-robot
docker compose stop server

aside="data/db-demo-$(date +%Y%m%d-%H%M%S)"
mv data/db "$aside"
mkdir data/db

# The server refuses to start on a sample database without this line, and on a real one with it.
grep -v '^DEMO_MODE=' .env >.env.new || true
cat .env.new >.env
rm .env.new
docker compose up -d

printf 'Done. The sample data is in %s and the server is running with an empty history.\n' "$aside"
printf 'Delete that folder when you no longer want it.\n'
