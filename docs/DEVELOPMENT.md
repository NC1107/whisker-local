# Development

## Tests

```bash
cd server && python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest && .venv/bin/ruff check . ../tools

cd app && flutter test && flutter analyze
```

## Trying it without a robot

This is for looking at the app before a robot is connected.
It runs the normal server on a database full of made up history, with a simulator in place of the robot.

Seed the database first, while the server is stopped.
Each `--cat` is a name and a weight in pounds, and a photo path can go on the end.
The photo has to be a path inside the container, so the folder with the pictures gets mounted.

```bash
docker compose stop server
docker compose run --rm --no-deps -v "$PWD/photos:/photos:ro,z" server \
    python -m whisker_local.demo seed --cat Miso:10.1:/photos/miso.jpg --cat Pip:9.0 --days 120
```

It writes a few visits a day for each cat, labelled the way the server would label them, with a few left for you to choose, plus clean cycles, drawer moves, upkeep dates and alert history.
A database that already has anything in it is refused, so this can't touch real data.

Then turn the mode on and start everything with the demo profile.
The simulator is a second container from the same image, `demo-robot`, and it talks to the broker's internal port without TLS.
Make its cats visit at the weights you seeded with `DEMO_WEIGHTS`, and use `DEMO_VISIT_EVERY_SECONDS` to change how often (the default is 600).

```bash
echo DEMO_MODE=1 >> .env
echo DEMO_WEIGHTS=10.1,9.0 >> .env
docker compose --profile demo up -d
```

`/api/status` has `"demo": true` while this is on, and the app shows a label for it.
The server won't start on sample data without `DEMO_MODE=1`, and it won't start with `DEMO_MODE=1` on a database that has real history.
So the two can't get mixed up by accident.

To switch to the real robot:

```bash
scripts/end-demo.sh
```

It stops the simulator and the server, moves the sample data to `data/db-demo-<time>` (the database and the photos), takes `DEMO_MODE` out of `.env` and starts the server again on an empty history.
Nothing is deleted, so remove that folder yourself when you're done with it.
Run it before `scripts/connect-robot.sh`, since the simulator uses the robot's serial and would fight the real one for it.

`tools/fake_robot.py` stands in for a robot over the real TLS connection.
