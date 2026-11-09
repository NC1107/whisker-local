# Development

## Tests

```bash
cd server && python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest && .venv/bin/ruff check . ../tools

cd app && flutter test && flutter analyze
```

## Trying it without a robot

You run the normal server on a database full of made up history, with a simulator in place of the robot.
It's for looking at the app before a robot is connected.

Seed the database after `./install.sh` and while the server is stopped.
A database that already has anything in it is refused, which includes one a robot has connected to, so it can't touch real data.
Each `--cat` is a name and a weight in pounds, with an optional photo path on the end.
The photo path is inside the container, so the folder with the pictures gets mounted.

```bash
docker compose stop server
docker compose run --rm --no-deps -v "$PWD/photos:/photos:ro,z" server \
    python -m whisker_local.demo seed --cat Miso:10.1:/photos/miso.jpg --cat Pip:9.0 --days 120
```

It writes a few visits a day for each cat, labelled the way the server would label them, with a few left for you to choose, plus clean cycles, drawer moves, upkeep dates and alert history.

Then turn the mode on and start everything with the demo profile.
The simulator is a second container, `demo-robot`, that talks to the broker's internal port without TLS.
`DEMO_WEIGHTS` sets the weights its cats visit with, so match the ones you seeded.
`DEMO_VISIT_EVERY_SECONDS` sets how often (default 600).

```bash
echo DEMO_MODE=1 >> .env
echo DEMO_WEIGHTS=10.1,9.0 >> .env
docker compose --profile demo up -d
```

`/api/status` has `"demo": true` while this is on, and the app shows a label for it.
The server won't start on sample data without `DEMO_MODE=1`, and won't start with `DEMO_MODE=1` on a database with real history, so the two can't get mixed up.

To switch to the real robot:

```bash
scripts/end-demo.sh
```

It stops the simulator and the server, moves the sample database and photos to `data/db-demo-<time>`, takes the demo settings out of `.env`, and starts the server on an empty history.
Nothing is deleted, remove that folder yourself when you're done.
Run it before `scripts/connect-robot.sh`, since the simulator uses the robot's serial and would fight the real robot for it.

## Simulating a robot over TLS

`tools/fake_robot.py` connects the way the real robot does, so the server sees it as online.
It needs the server package installed, and sends nothing to a real robot.

```bash
python tools/fake_robot.py --host 192.168.1.50 --ca data/certs/ca.crt --serial LR4C123456
```
