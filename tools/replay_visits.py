#!/usr/bin/env python3
"""Replays the raw robot messages kept in a database through the visit logic.

Usage: replay_visits.py DATABASE

The database is only read. Visits and clean cycles are saved to a throwaway database, so this
shows what the current logic would have recorded from the same messages, for example after a
change to how visits are detected or to check a real visit against its raw trace.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from whiskerless.devices.litter_robot_4 import parse_message

from whisker_local.bridge import RobotBridge
from whisker_local.config import Config
from whisker_local.store import Store

SERIAL = "REPLAY"


def replay(source: Path) -> tuple[list[dict], int]:
    with tempfile.TemporaryDirectory() as folder:
        scratch = Path(folder) / "replay.db"
        store = Store(scratch)
        bridge = RobotBridge(Config(SERIAL, "key", "broker", 1883, scratch, Path(folder)), store)
        database = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        last = None
        for at, kind, body in database.execute("SELECT at, kind, body FROM messages ORDER BY id"):
            last = datetime.fromisoformat(at)
            message = parse_message(f"prod/LR4/{SERIAL}/{kind}", body)
            if message is not None:
                bridge.ingest(message, last)
        if last is not None:
            bridge._settle_visits(last + timedelta(minutes=5))
        visits = store.events(limit=500, kinds=["visit"])[::-1]
        cycles = len(store.events(limit=500, kinds=["clean_cycle"]))
        store.close()
        return visits, cycles


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    visits, cycles = replay(Path(sys.argv[1]))
    for visit in visits:
        detail = visit["detail"]
        print(visit["at"], detail.get("weight_lb"), detail.get("duration_s"), detail.get("source"))
    print(f"{len(visits)} visits, {cycles} clean cycles")


if __name__ == "__main__":
    main()
