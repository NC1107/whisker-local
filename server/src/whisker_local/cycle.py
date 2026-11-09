"""Where a clean cycle is, and how often a cat interrupted it.

The robot has no globe position to report. It says which of four stages it is in and, apart from
that, whether a cat has paused it, so progress here is a stage plus an estimate from how long this
robot's recent cycles took.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from whiskerless.devices.litter_robot_4 import LitterRobot4State

# robotCycleStatus 2, 3, 4 and 5. The firmware's name for the second is the drawer measurement.
STAGES = ("Dump", "Measure", "Level", "Home")
_STAGE_OF_STATUS = {2: 0, 3: 1, 4: 2, 5: 3}

PAUSE_CAT = "Paused, looks like a cat"
PAUSE_SENSOR = "Paused, cat sensor"

# Statuses a cat's visit can show in the middle of a cycle on firmware that leaves clean_cycle
# for it. They neither end the cycle nor mean it was abandoned.
_INTERRUPTION_STATUSES = frozenset({"cat_detected", "cat_sensor_timing"})

# Fewer cycles than this say too little about what is typical, and a figure drawn from them
# would look like knowledge.
MIN_CYCLES_FOR_ESTIMATE = 3
CYCLES_CONSIDERED = 10

# The robot is asked for its state every two minutes, so a longer silence in the middle of a
# cycle means whatever happened in it was not seen.
MAX_BLIND_GAP = timedelta(minutes=5)


def stage_of(robot: LitterRobot4State) -> int | None:
    value = robot.raw.get("robotCycleStatus")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return _STAGE_OF_STATUS.get(value)


def pause_label(robot: LitterRobot4State) -> str | None:
    if robot.robot_cycle_state != "cat_interrupt":
        return None
    return PAUSE_CAT if robot.cat_detected and robot.scale_loaded else PAUSE_SENSOR


@dataclass(frozen=True, slots=True)
class FinishedCycle:
    duration_s: int
    pauses: int
    paused_seconds: int

    def detail(self) -> dict[str, int]:
        return {
            "duration_s": self.duration_s,
            "pauses": self.pauses,
            "paused_seconds": self.paused_seconds,
        }


class CycleWatch:
    """Follows one cycle at a time from the robot's state documents."""

    def __init__(self) -> None:
        self._clear()

    def _clear(self) -> None:
        self._started: datetime | None = None
        self._last_seen: datetime | None = None
        self._witnessed_start = False
        self._pauses = 0
        self._paused_s = 0.0
        self._paused_since: datetime | None = None
        self.stage: int | None = None
        self.label: str | None = None

    @property
    def running(self) -> bool:
        return self._started is not None

    def observe(self, robot: LitterRobot4State, now: datetime) -> FinishedCycle | None:
        """Take one state document. A cycle seen from its start to its end comes back."""
        status = robot.robot_status
        if status == "clean_cycle":
            self._track(robot, now)
            return None
        if self._started is None or status in _INTERRUPTION_STATUSES:
            return None
        finished = self._finish(now) if status == "ready" else None
        self._clear()
        return finished

    def _track(self, robot: LitterRobot4State, now: datetime) -> None:
        if self._started is None:
            self._started = now
            # Joined at a later stage, the pauses before it were missed, and a count of zero
            # would be a guess presented as a measurement.
            self._witnessed_start = robot.raw.get("robotCycleStatus") in (1, 2)
        elif self._last_seen is not None and now - self._last_seen > MAX_BLIND_GAP:
            self._witnessed_start = False
        self._last_seen = now
        self.stage = stage_of(robot)
        self.label = pause_label(robot)
        if self.label is not None and self._paused_since is None:
            self._paused_since = now
            self._pauses += 1
        elif self.label is None and self._paused_since is not None:
            self._close_pause(now)

    def _close_pause(self, now: datetime) -> None:
        if self._paused_since is not None:
            self._paused_s += (now - self._paused_since).total_seconds()
            self._paused_since = None

    def _finish(self, now: datetime) -> FinishedCycle | None:
        if self._started is None or not self._witnessed_start:
            return None
        self._close_pause(now)
        return FinishedCycle(
            duration_s=round((now - self._started).total_seconds()),
            pauses=self._pauses,
            paused_seconds=round(self._paused_s),
        )

    def view(self, now: datetime, typical_s: float | None) -> dict[str, Any] | None:
        if self._started is None:
            return None
        paused_s = self._paused_s
        if self._paused_since is not None:
            paused_s += (now - self._paused_since).total_seconds()
        active_s = max(0.0, (now - self._started).total_seconds() - paused_s)
        return {
            "stages": list(STAGES),
            "stage": self.stage,
            "paused": self.label is not None,
            "pause_label": self.label,
            "progress": progress(self.stage, active_s, typical_s),
            "progress_is_estimate": True,
        }


def progress(stage: int | None, active_s: float, typical_s: float | None) -> float | None:
    """A share between 0 and 1, kept inside the stage the robot says it is in.

    Without a typical length, or without knowing the stage, any number would be invented.
    """
    if stage is None or typical_s is None or typical_s <= 0:
        return None
    low = stage / len(STAGES)
    high = (stage + 1) / len(STAGES)
    # The top of a stage is only reached by the robot moving on to the next.
    return round(min(max(active_s / typical_s, low), high - 0.01), 3)


def typical_active_seconds(events: Sequence[dict[str, Any]]) -> float | None:
    """The median time this robot spent working in its recent cycles, pauses left out."""
    lengths = []
    for event in events[:CYCLES_CONSIDERED]:
        detail = event["detail"]
        duration, paused = detail.get("duration_s"), detail.get("paused_seconds", 0)
        if isinstance(duration, int) and isinstance(paused, int) and duration > paused:
            lengths.append(duration - paused)
    if len(lengths) < MIN_CYCLES_FOR_ESTIMATE:
        return None
    return statistics.median(lengths)
