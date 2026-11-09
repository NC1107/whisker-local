"""Turns the robot's messages into cat visits, one visit per time a cat was inside.

The robot says a cat is inside in two ways: its own status (25, "cat inside") in a state
document, and a weight in the activity stream. Documents only arrive when asked for, so either
can be missing; a visit is recorded when at least one of them proves a body, never from the
``catDetect`` field alone, because the globe raises that bit in every cycle.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from whiskerless.devices.litter_robot_4 import ActivityMessage, StateMessage
from whiskerless.devices.litter_robot_4.const import CAT_WEIGHT_DIVISOR, Register

# How long after a visit ends its weight can still arrive and be part of the same save. Over 30
# days on two robots, 118 of 130 weights landed within 7 s of the end.
WEIGHT_GRACE = timedelta(seconds=15)

# A weight later than the grace still belongs to the visit before it when nothing else has
# happened since, and it is added to the saved visit instead of making a second one.
LATE_WEIGHT_WINDOW = timedelta(minutes=10)

# State documents trail the activity that prompted them by seconds, so a beam break just before
# the first "cat inside" document is when the cat really came in.
BEAM_LOOKBACK = timedelta(seconds=60)

# Two readings of one cat scatter by a few tenths of a pound, while the household's cats are
# more than a pound apart. Readings this far apart in one stretch are two animals.
SAME_CAT_LB = 1.2

# A cat does not sit in the box this long. A longer stretch is a cat asleep or something left
# on the scale, and the length is not a duration worth showing.
MAX_VISIT_S = 20 * 60

# The scale reads cats; anything outside this is not a weight worth keeping.
WEIGHT_RANGE_LB = (0.5, 60.0)

# Settled-weight seconds above this are a button press artefact, not a visit.
_SETTLED_MAX_S = 300
_REPORTED_DURATION = 0x6F

_CURTAIN_DETECTED = 0x11


@dataclass(slots=True)
class Episode:
    """One stretch with a cat inside, collecting what the robot says about it."""

    started: datetime
    ended: datetime | None = None
    weights: list[tuple[datetime, int]] = field(default_factory=list)
    reported_s: int | None = None
    settled_s: int | None = None
    beam_seen: bool = False
    source: str = "status"

    def as_dict(self) -> dict[str, Any]:
        return {
            "started": self.started.isoformat(),
            "ended": None if self.ended is None else self.ended.isoformat(),
            "weights": [[at.isoformat(), raw] for at, raw in self.weights],
            "reported_s": self.reported_s,
            "settled_s": self.settled_s,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Episode:
        ended = raw.get("ended")
        return cls(
            started=datetime.fromisoformat(raw["started"]),
            ended=None if ended is None else datetime.fromisoformat(ended),
            weights=[(datetime.fromisoformat(at), int(w)) for at, w in raw.get("weights", [])],
            reported_s=raw.get("reported_s"),
            settled_s=raw.get("settled_s"),
            beam_seen=True,
            source=raw.get("source", "status"),
        )


@dataclass(frozen=True, slots=True)
class Visit:
    """A finished visit, ready to be saved."""

    started: datetime
    ended: datetime
    weight_raw: int | None
    duration_s: int | None
    settled_s: int | None
    source: str
    readings: int = 0
    long_stay: bool = False

    @property
    def weight_lb(self) -> float | None:
        return None if self.weight_raw is None else round(self.weight_raw / CAT_WEIGHT_DIVISOR, 2)


@dataclass(frozen=True, slots=True)
class WeightUpdate:
    """A weight that arrived after its visit was saved."""

    event_id: int
    weight_raw: int


@dataclass(slots=True)
class _Saved:
    event_id: int
    ended: datetime
    weight_raw: int | None


def _groups(weights: list[tuple[datetime, int]]) -> list[list[tuple[datetime, int]]]:
    groups: list[list[tuple[datetime, int]]] = []
    for at, raw in sorted(weights):
        centre = statistics.median(w for _, w in groups[-1]) if groups else None
        if centre is not None and abs(raw - centre) < SAME_CAT_LB * CAT_WEIGHT_DIVISOR:
            groups[-1].append((at, raw))
        else:
            groups.append([(at, raw)])
    return groups


def _plausible(raw: int) -> bool:
    low, high = WEIGHT_RANGE_LB
    return low <= raw / CAT_WEIGHT_DIVISOR <= high


class VisitRecorder:
    """Follows the message stream and hands back visits once their weight has had time to land."""

    def __init__(self, *, waiting: list[Episode] | None = None) -> None:
        self.waiting: list[Episode] = waiting or []
        self._open: Episode | None = None
        self._beam_at: datetime | None = None
        self._in_cycle = False
        self._saved: _Saved | None = None

    def observe(self, message: StateMessage | ActivityMessage, now: datetime) -> list[WeightUpdate]:
        if isinstance(message, StateMessage):
            self._state(message, now)
            return []
        return self._activity(message, now)

    def saved(self, event_id: int, visit: Visit) -> None:
        self._saved = _Saved(event_id, visit.ended, visit.weight_raw)

    def remember(self, event_id: int, ended: datetime, weight_raw: int | None) -> None:
        """Tell the recorder about the newest visit already stored, e.g. after a restart."""
        self._saved = _Saved(event_id, ended, weight_raw)

    def _state(self, message: StateMessage, now: datetime) -> None:
        robot = message.state
        self._in_cycle = bool(robot.is_cleaning)
        if robot.robot_status == "cat_detected":
            if self._open is None:
                self._open = Episode(started=self._entered(now))
            if robot.cat_detected:
                self._open.beam_seen = True
        elif self._open is not None and robot.robot_status is not None:
            self._close(now)

    def _entered(self, now: datetime) -> datetime:
        if (
            self._beam_at is not None
            and not self._in_cycle
            and now - self._beam_at <= BEAM_LOOKBACK
        ):
            return self._beam_at
        return now

    def _close(self, now: datetime) -> None:
        episode, self._open = self._open, None
        assert episode is not None  # noqa: S101
        # A body breaks the sight line. A load alone is a hand or something put on the pan.
        beam_before = self._beam_at is not None and self._beam_at >= episode.started - BEAM_LOOKBACK
        if not (episode.beam_seen or beam_before):
            return
        episode.ended = now
        self.waiting.append(episode)

    def _activity(self, message: ActivityMessage, now: datetime) -> list[WeightUpdate]:
        updates: list[WeightUpdate] = []
        for reading in message.readings:
            if reading.register == Register.CAT_DETECT:
                if reading.value == _CURTAIN_DETECTED and not self._in_cycle:
                    self._beam_at = now
                    if self._open is not None:
                        self._open.beam_seen = True
            elif reading.register == _REPORTED_DURATION:
                if episode := self._latest(now):
                    episode.reported_s = reading.value
            elif reading.register == Register.CAT_VISIT_DURATION:
                if reading.value <= _SETTLED_MAX_S and (episode := self._latest(now)):
                    episode.settled_s = reading.value
            elif reading.register == Register.CAT_WEIGHT:
                if reading.value > 0 and _plausible(reading.value):
                    update = self._weight(reading.value, now)
                    if update is not None:
                        updates.append(update)
        return updates

    def _latest(self, now: datetime) -> Episode | None:
        if self._open is not None:
            return self._open
        if self.waiting and self.waiting[-1].ended is not None:
            if now - self.waiting[-1].ended <= LATE_WEIGHT_WINDOW:
                return self.waiting[-1]
        return None

    def _weight(self, raw: int, now: datetime) -> WeightUpdate | None:
        episode = self._latest(now)
        if episode is not None:
            episode.weights.append((now, raw))
            return None
        saved = self._saved
        if saved is not None and now - saved.ended <= LATE_WEIGHT_WINDOW and not self.waiting:
            if saved.weight_raw is None:
                saved.weight_raw = raw
                return WeightUpdate(saved.event_id, raw)
            if abs(raw - saved.weight_raw) < SAME_CAT_LB * CAT_WEIGHT_DIVISOR:
                return None
        # The state document that would have shown the cat inside never came; the weight proves
        # it. Only a beam break outside a cycle says when the cat arrived.
        started = self._entered(now)
        self.waiting.append(
            Episode(
                started=started,
                ended=now,
                weights=[(now, raw)],
                beam_seen=True,
                source="activity" if started != now else "weight",
            )
        )
        return None

    def ready(self, now: datetime) -> list[Visit]:
        """Visits whose weight has had its chance, oldest first."""
        done: list[Visit] = []
        still: list[Episode] = []
        for episode in self.waiting:
            assert episode.ended is not None  # noqa: S101
            if now - episode.ended < WEIGHT_GRACE:
                still.append(episode)
            else:
                done.extend(self._visits(episode))
        self.waiting = still
        return done

    def _visits(self, episode: Episode) -> list[Visit]:
        assert episode.ended is not None  # noqa: S101
        groups = _groups(episode.weights)
        if len(groups) <= 1:
            return [self._single(episode, groups[0] if groups else [])]
        visits: list[Visit] = []
        began = episode.started
        previous_end: datetime | None = None
        for index, group in enumerate(groups):
            last = index == len(groups) - 1
            ended = max(episode.ended, group[-1][0]) if last else group[-1][0]
            if previous_end is not None and ended <= previous_end:
                ended = previous_end + timedelta(milliseconds=1)
            visits.append(
                Visit(
                    started=began,
                    ended=ended,
                    weight_raw=round(statistics.median(w for _, w in group)),
                    duration_s=None,
                    settled_s=episode.settled_s if last else None,
                    source=episode.source,
                    readings=len(group),
                )
            )
            began = previous_end = ended
        return visits

    @staticmethod
    def _single(episode: Episode, group: list[tuple[datetime, int]]) -> Visit:
        assert episode.ended is not None  # noqa: S101
        span = round((episode.ended - episode.started).total_seconds())
        reported = episode.reported_s
        duration: int | None = reported if reported is not None and reported > 0 else span
        # A weight with no beam break to date the arrival says nothing about how long it took.
        if episode.source == "weight":
            duration = None
        long_stay = duration is not None and duration > MAX_VISIT_S
        return Visit(
            started=episode.started,
            ended=episode.ended,
            weight_raw=round(statistics.median(w for _, w in group)) if group else None,
            duration_s=None if long_stay else duration,
            settled_s=episode.settled_s,
            source=episode.source,
            readings=len(group),
            long_stay=long_stay,
        )
