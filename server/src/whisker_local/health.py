"""Early-warning notes for the cats, and the data behind the charts that explain them.

Nothing here diagnoses anything. Each note says what the numbers did and since when, and the
checks stay quiet until there is enough history to tell a change from the ordinary scatter of a
litter box scale. A note that cannot be written yet is reported as a wait with how much is left.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import health_checks as checks
from . import stats
from .cats import CatTracker
from .health_history import History, is_disturbed, visit_status
from .store import Store

log = logging.getLogger(__name__)

FIRED_KEY = "health_fired"

# Visits arrive a few an hour and a note needs days to form, so half an hour is prompt enough.
CHECK_EVERY = 30 * 60

# A request is answered from the last reading of the history for this long. Visits arrive a few
# an hour, and building the history is the one expensive step.
CACHE_SECONDS = 60

# Remembering what has been announced for this long is longer than any note lasts.
FIRED_KEEP = timedelta(days=180)

# A reading this far from the line is drawn hollow, whatever the scatter says: the scale is off
# by 3 to 4 percent on a good reading, so a few percent more is not yet a stray.
OFF_CLUSTER_SHARE = 0.08

# The longest stretch of days one chart request may cover.
MAX_SPAN_DAYS = 3660

# The actogram is a row per day, so a long period shows only its newest rows.
ACTOGRAM_DAYS = 90

MALE = ("male", "neutered_male")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def on_irregularity(cat: dict[str, Any] | None, kind: str, title: str, body: str) -> None:
    """Called once for every check that fires. It only logs; notifications are wired to it."""
    log.info("irregularity %s cat=%s: %s", kind, None if cat is None else cat["id"], title)


async def run_monitor(service: HealthService, every: float = CHECK_EVERY) -> None:
    """Look for new irregularities now and then, which is as often as a note could change."""
    while True:
        try:
            service.check()
        except Exception:
            log.exception("health check failed")
        await asyncio.sleep(every)


def _name(day: date, today: date) -> str:
    text = f"{_MONTHS[day.month - 1]} {day.day}"
    return text if day.year == today.year else f"{text}, {day.year}"


def _wait(days: int) -> str:
    if days <= 14:
        return f"about {max(days, 1)} more {'day' if days == 1 else 'days'}"
    return f"about {math.ceil(days / 7)} more weeks"


def _block_label(block: int) -> str:
    return f"{block * checks.BLOCK_HOURS:02d}:00 to {(block + 1) * checks.BLOCK_HOURS:02d}:00"


def _hours(delta: timedelta) -> int:
    return round(delta.total_seconds() / 3600)


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    cat_id: int | None
    key: str
    since: date | None
    level: str
    p: float
    detail: Any


class HealthService:
    def __init__(
        self,
        store: Store,
        cats: CatTracker,
        *,
        clock: Callable[[], datetime] | None = None,
        notify: Callable[[dict[str, Any] | None, str, str, str], None] = on_irregularity,
    ) -> None:
        self._store = store
        self._cats = cats
        self._clock = clock or (lambda: datetime.now(UTC))
        self._notify = notify
        self._cached: tuple[datetime, History] | None = None

    def history(self) -> History:
        now = self._clock()
        if self._cached is None or now - self._cached[0] > timedelta(seconds=CACHE_SECONDS):
            self._cached = (now, History(self._store, self._cats))
        return self._cached[1]

    def forget(self) -> None:
        self._cached = None

    # Evaluation ---------------------------------------------------------------------------

    def evaluate_day(self, history: History, i: int) -> list[Finding]:
        """Every day-based check as of the end of day ``i``, after persistence and BH."""
        candidates: list[tuple[float, Finding | None]] = []
        household = history.household
        state = checks.rate_state(
            household, i, checks.HOUSEHOLD_WINDOW, checks.HOUSEHOLD_MIN_HISTORY, 0
        )
        if state.ready:
            held = checks.persistent_rate(
                household, i, checks.HOUSEHOLD_WINDOW, checks.HOUSEHOLD_MIN_HISTORY, 0
            )
            candidates.append((state.p, self._rate_finding("household_rate", None, held, history)))
        for cat_id, series in history.cat_series.items():
            begin = history.began(series)
            state = checks.rate_state(series, i, checks.CAT_WINDOW, checks.CAT_MIN_HISTORY, begin)
            if state.ready:
                held = checks.persistent_rate(
                    series, i, checks.CAT_WINDOW, checks.CAT_MIN_HISTORY, begin
                )
                candidates.append((state.p, self._rate_finding("cat_rate", cat_id, held, history)))
        for cat_id in history.readings:
            weight = checks.weight_state(history, cat_id, i)
            if weight.stage != "change":
                continue
            held_weight = checks.persistent_weight(history, cat_id, i)
            candidates.append((weight.p, self._weight_finding(cat_id, held_weight, history, i)))
        keep = stats.benjamini_hochberg([p for p, _ in candidates])
        found = [f for n, (_, f) in enumerate(candidates) if f is not None and n in keep]
        for cat_id in history.readings:
            slope = self._slope_finding(history, cat_id, i)
            if slope is not None:
                found.append(slope)
        for cat_id in history.by_cat:
            rhythm = checks.rhythm_state(history, cat_id, i)
            found.extend(self._rhythm_findings(history, cat_id, rhythm, i))
        return found

    def _rate_finding(
        self, kind: str, cat_id: int | None, held: checks.Persisted | None, history: History
    ) -> Finding | None:
        if held is None:
            return None
        since = history.date_of(held.since)
        return Finding(
            kind,
            cat_id,
            f"{kind}:{cat_id or 0}:{since.isoformat()}:{held.state.direction}",
            since,
            "look",
            held.state.p,
            held.state,
        )

    def _weight_finding(
        self,
        cat_id: int,
        held: tuple[checks.WeightState, int] | None,
        history: History,
        i: int,
    ) -> Finding | None:
        if held is None:
            return None
        state, start = held
        since = history.date_of(start)
        larger = self._larger(history, cat_id, i, state)
        return Finding(
            "weight_change",
            cat_id,
            f"weight_change:{cat_id}:{since.isoformat()}",
            since,
            "look",
            state.p,
            (state, larger),
        )

    @staticmethod
    def _larger(history: History, cat_id: int, i: int, state: checks.WeightState) -> bool:
        sign = math.copysign(1, state.pct or 0)
        month = checks.longer_change(history, cat_id, i, 30)
        quarter = checks.longer_change(history, cat_id, i, 90)
        return (month is not None and sign * month >= checks.LARGER_MONTH_SHARE) or (
            quarter is not None and sign * quarter >= checks.LARGER_QUARTER_SHARE
        )

    def _slope_finding(self, history: History, cat_id: int, i: int) -> Finding | None:
        today = checks.slope_state(history, cat_id, i)
        if today is None:
            return None
        for back in checks.SLOPE_HELD_AT:
            earlier = checks.slope_state(history, cat_id, i - back)
            if earlier is None or (earlier.per_month_pct > 0) != (today.per_month_pct > 0):
                return None
        direction = "up" if today.per_month_pct > 0 else "down"
        key = f"weight_slope:{cat_id}:{direction}"
        return Finding("weight_slope", cat_id, key, None, "look", 1.0, today)

    def _rhythm_findings(
        self, history: History, cat_id: int, rhythm: checks.Rhythm, i: int
    ) -> list[Finding]:
        return [
            Finding(
                "rhythm",
                cat_id,
                f"rhythm:{cat_id}:{shift.block}:{shift.direction}:{history.date_of(i).strftime('%Y-%m')}",
                None,
                "info",
                shift.p,
                shift,
            )
            for shift in rhythm.shifts
        ]

    def silence_finding(
        self, history: History, watch: checks.SilenceWatch, now: datetime
    ) -> Finding | None:
        state = watch.at(now)
        if not state.fired or state.last is None:
            return None
        local = state.last.astimezone(history.tz).date()
        return Finding(
            "silence", None, f"silence:{state.last.isoformat()}", local, "look", 1.0, state
        )

    def burst_findings(self, history: History, now: datetime, within: timedelta) -> list[Finding]:
        found = []
        for cat_id in history.by_cat:
            for burst in checks.find_bursts(history, cat_id):
                if now - burst.last > within:
                    continue
                level = "urgent" if self._is_male(history, cat_id) else "look"
                local = burst.first.astimezone(history.tz).date()
                found.append(
                    Finding(
                        "burst",
                        cat_id,
                        f"burst:{cat_id}:{burst.first.isoformat()}",
                        local,
                        level,
                        1.0,
                        burst,
                    )
                )
        return found

    @staticmethod
    def _is_male(history: History, cat_id: int) -> bool:
        return history.cats.get(cat_id, {}).get("sex") in MALE

    # Notes ---------------------------------------------------------------------------------

    def findings(self, history: History | None = None) -> list[Finding]:
        history = history or self.history()
        now = self._clock()
        i = history.last_complete()
        if i is None:
            return []
        found = self.evaluate_day(history, i)
        silence = self.silence_finding(
            history, checks.SilenceWatch(history, self._cats.coverage.heard_share()), now
        )
        if silence is not None:
            found.append(silence)
        found.extend(self.burst_findings(history, now, timedelta(hours=24)))
        return found

    def notes(self, cat_id: int | None = None) -> dict[str, Any]:
        history = self.history()
        today = history.today
        found = self.findings(history)
        order = {"urgent": 0, "look": 1, "info": 2}
        found.sort(key=lambda f: (order[f.level], f.since or date.min), reverse=False)
        notes = [
            self._note(history, f, today)
            for f in found
            if cat_id is None or f.cat_id in (cat_id, None)
        ]
        return {
            "timezone": str(history.tz),
            "through": None
            if history.last_complete() is None
            else history.date_of(history.last_complete()).isoformat(),
            "notes": notes,
            "waiting": self._waiting(history, cat_id),
        }

    def _cat(self, history: History, cat_id: int | None) -> dict[str, Any] | None:
        return None if cat_id is None else history.cats.get(cat_id)

    def _note(self, history: History, finding: Finding, today: date) -> dict[str, Any]:
        cat = self._cat(history, finding.cat_id)
        name = "This cat" if cat is None else cat["name"]
        title, body = self._words(finding, name, history, today)
        return {
            "key": finding.key,
            "kind": finding.kind,
            "cat_id": finding.cat_id,
            "level": finding.level,
            "title": title,
            "body": body,
            "since": None if finding.since is None else finding.since.isoformat(),
        }

    def _words(self, f: Finding, name: str, history: History, today: date) -> tuple[str, str]:
        since = "" if f.since is None else _name(f.since, today)
        if f.kind in ("household_rate", "cat_rate"):
            return self._rate_words(f, name, since)
        if f.kind == "weight_change":
            return self._weight_words(f, name, since)
        if f.kind == "weight_slope":
            return self._slope_words(f, name)
        if f.kind == "silence":
            return self._silence_words(f, history)
        if f.kind == "burst":
            return self._burst_words(f, name, history)
        return self._rhythm_words(f, name)

    @staticmethod
    def _rate_words(f: Finding, name: str, since: str) -> tuple[str, str]:
        state: checks.RateState = f.detail
        household = f.kind == "household_rate"
        window = checks.HOUSEHOLD_WINDOW if household else checks.CAT_WINDOW
        more = state.direction == "up"
        subject = "the box" if household else f"{name}"
        title = (
            f"The box is used {'more' if more else 'less'} than usual"
            if household
            else f"{name} has been recorded in the box {'more' if more else 'less'} than usual"
        )
        times = (
            f"about {state.ratio:.1f} times as many"
            if more and state.ratio is not None and math.isfinite(state.ratio)
            else ("a lot more" if more else "about half as many or fewer")
        )
        body = (
            f"Since {since}, {subject} has had {state.observed} visits in the last "
            f"{window} days (over {state.days} days with data), where about "
            f"{state.usual_per_day * state.days:.0f} is usual. That is {times}. "
        )
        body += (
            "This can have many causes, including a change to the litter or the box."
            if household
            else "Visits are matched to cats by weight, so a change in matching can look like "
            "a change in visits."
        )
        return title, body

    @staticmethod
    def _weight_words(f: Finding, name: str, since: str) -> tuple[str, str]:
        state, larger = f.detail
        lower = (state.pct or 0) < 0
        word = "lower" if lower else "higher"
        low, high = sorted((abs(state.pct_low), abs(state.pct_high)))
        body = (
            f"Since {since}, the middle of {name}'s last two weeks of weight readings has been "
            f"about "
            f"{abs(state.pct):.0f} percent {word} than the usual {state.reference:.1f} lb "
            f"(range {low:.0f} to {high:.0f} percent). "
            f"The box scale is off by about {state.noise_pct:.0f} percent on each reading, "
            f"so weighing {name} at home is a good way to check."
        )
        if larger:
            body += " A change of this size is worth mentioning to your vet."
        return f"{name}'s weight readings are {word} than usual", body

    @staticmethod
    def _slope_words(f: Finding, name: str) -> tuple[str, str]:
        state: checks.SlopeState = f.detail
        word = "lower" if state.per_month_pct < 0 else "higher"
        low, high = sorted((abs(state.low_pct), abs(state.high_pct)))
        return (
            f"{name}'s weight readings are drifting {word}",
            f"Over the last {checks.SLOPE_DAYS} days the readings have moved about "
            f"{abs(state.per_month_pct):.1f} percent {word} each month (range {low:.1f} to "
            f"{high:.1f} percent). It is slow, so a routine check-up is a good place to "
            "mention it.",
        )

    @staticmethod
    def _silence_words(f: Finding, history: History) -> tuple[str, str]:
        state: checks.SilenceState = f.detail
        assert state.quiet_for is not None  # noqa: S101
        body = (
            f"No cat has used the box for {_hours(state.quiet_for)} hours "
            "while the robot was online."
        )
        if state.usual_longest:
            body += (
                f" The longest quiet stretch before this was {_hours(state.usual_longest)} hours."
            )
        body += " The cats may be away or using another box."
        return "No visits for a long while", body

    @staticmethod
    def _burst_words(f: Finding, name: str, history: History) -> tuple[str, str]:
        burst: checks.Burst = f.detail
        span = max(1, _hours(burst.last - burst.first) or 1)
        count = f"{burst.visits} times in about {span} {'hour' if span == 1 else 'hours'}"
        title = f"{name} visited the box {burst.visits} times in a short while"
        base = f"{name} has visited the box {count}, each time briefly. "
        ask = f"If {name} is straining, crying, licking, or passing little or no urine, "
        sex = history.cats.get(burst.cat_id, {}).get("sex")
        if sex in MALE:
            body = base + ask + "call a vet now. Male cats can become blocked within a day."
        elif sex == "unknown":
            body = (
                base + ask + "call a vet. If this cat is male, a blocked bladder can become an "
                "emergency within a day."
            )
        else:
            body = base + ask + "call a vet."
        return title, body + " The box cannot see urine, so this is not a diagnosis."

    @staticmethod
    def _rhythm_words(f: Finding, name: str) -> tuple[str, str]:
        shift: checks.RhythmShift = f.detail
        more = shift.direction == "up"
        window = _block_label(shift.block)
        return (
            f"{name} uses the box {'more' if more else 'less'} between {window}",
            f"{shift.recent_share * 100:.0f} percent of {name}'s visits in the last four "
            f"weeks were between {window}, against {shift.usual_share * 100:.0f} percent in "
            "the eight weeks before. "
            "This is a note about timing only.",
        )

    # What is still missing ---------------------------------------------------------------

    def _waiting(self, history: History, cat_id: int | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        i = history.last_complete()

        def add(kind: str, who: int | None, label: str, ready: bool, days: int) -> None:
            if cat_id is not None and who not in (None, cat_id):
                return
            text = f"{label} is ready." if ready else f"{label} needs {_wait(days)} of data."
            out.append({"kind": kind, "cat_id": who, "ready": ready, "text": text})

        if i is None:
            add(
                "household_rate",
                None,
                "Checking for a change in how often the box is used",
                False,
                checks.HOUSEHOLD_MIN_HISTORY,
            )
            return out
        state = checks.rate_state(
            history.household, i, checks.HOUSEHOLD_WINDOW, checks.HOUSEHOLD_MIN_HISTORY, 0
        )
        add(
            "household_rate",
            None,
            "Checking for a change in how often the box is used",
            state.ready,
            state.need_days,
        )
        heard = history.household.window(0, i)[1]
        add(
            "silence",
            None,
            "Checking for a long quiet spell",
            heard >= checks.SILENCE_MIN_DAYS,
            checks.SILENCE_MIN_DAYS - heard,
        )
        for cat_id_, cat in history.cats.items():
            if cat["archived_at"] is not None:
                continue
            name = cat["name"]
            series = history.cat_series.get(cat_id_)
            begin = None if series is None else history.began(series)
            if series is None:
                add("cat_rate", cat_id_, f"Checking {name}'s visits", False, checks.CAT_MIN_HISTORY)
            else:
                rate = checks.rate_state(
                    series, i, checks.CAT_WINDOW, checks.CAT_MIN_HISTORY, begin
                )
                add("cat_rate", cat_id_, f"Checking {name}'s visits", rate.ready, rate.need_days)
            weight = checks.weight_state(history, cat_id_, i)
            add(
                "weight_trend",
                cat_id_,
                f"{name}'s weight line",
                weight.stage != "none",
                weight.need_days,
            )
            ready = weight.stage == "change"
            need = weight.need_days if weight.stage != "none" else checks.CHANGE_MIN_DAYS
            add(
                "weight_change",
                cat_id_,
                f"Comparing {name}'s weight with the usual",
                ready,
                need or 1,
            )
            readings = history.readings.get(cat_id_)
            since_first = 0 if readings is None or not readings.days else i - readings.days[0]
            add(
                "weight_slope",
                cat_id_,
                f"Checking {name}'s slow weight drift",
                since_first >= checks.SLOPE_DAYS,
                checks.SLOPE_DAYS - since_first,
            )
            rhythm = checks.rhythm_state(history, cat_id_, i)
            add(
                "rhythm", cat_id_, f"Checking {name}'s daily rhythm", rhythm.ready, rhythm.need_days
            )
        return out

    # Telling the outside ---------------------------------------------------------------------

    def check(self) -> list[dict[str, Any]]:
        """Announce each note that has not been announced before, through ``on_irregularity``."""
        history = self.history()
        now = self._clock()
        fired: dict[str, str] = self._store.get_json(FIRED_KEY) or {}
        fired = {k: v for k, v in fired.items() if now - datetime.fromisoformat(v) < FIRED_KEEP}
        announced = []
        for finding in self.findings(history):
            if finding.level == "info" or finding.key in fired:
                continue
            note = self._note(history, finding, history.today)
            self._notify(
                self._cat(history, finding.cat_id), finding.kind, note["title"], note["body"]
            )
            fired[finding.key] = now.isoformat()
            announced.append(note)
        self._store.put_json(FIRED_KEY, fired)
        return announced

    # Views -----------------------------------------------------------------------------------

    def quality(self, start: date, end: date) -> dict[str, Any]:
        history = self.history()
        self._require_span(start, end)
        today = history.today
        end = min(end, today)
        days = self._cats.coverage.days(start, end) if start <= end else []
        statuses: dict[date, list[str]] = {}
        named_by_day: dict[date, int] = {}
        for visit in self._store.visits(
            since=self._cats.day_start(start), before=self._cats.day_start(end + timedelta(days=1))
        ):
            if visit["label"] == "not_cat":
                continue
            local = datetime.fromisoformat(visit["at"]).astimezone(history.tz).date()
            statuses.setdefault(local, []).append(visit_status(visit))
            named_by_day[local] = named_by_day.get(local, 0) + (visit["label"] == "cat")
        rows = []
        for day in days:
            found = statuses.get(day.date, [])
            named = named_by_day.get(day.date, 0)
            rows.append(
                {
                    **day.as_dict(),
                    "scale_disturbed": is_disturbed(found),
                    "visits": len(found),
                    "named": named,
                    "unnamed": len(found) - named,
                }
            )
        totals = {
            "days": len(rows),
            "full": sum(1 for r in rows if r["state"] == "full"),
            "partial": sum(1 for r in rows if r["state"] == "partial"),
            "none": sum(1 for r in rows if r["state"] == "none"),
            "unreliable": sum(1 for r in rows if r["unreliable"] is not None),
            "scale_disturbed": sum(1 for r in rows if r["scale_disturbed"]),
            "visits": sum(r["visits"] for r in rows),
            "named": sum(r["named"] for r in rows),
            "unnamed": sum(r["unnamed"] for r in rows),
        }
        return {
            "timezone": str(history.tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "earliest": None if history.first is None else history.first.isoformat(),
            "days": rows,
            "gaps": _runs(rows),
            "summary": {
                **totals,
                "unnamed_share": None
                if not totals["visits"]
                else round(totals["unnamed"] / totals["visits"], 3),
            },
            "unreliable_ranges": self._cats.coverage.unreliable_ranges(),
        }

    @staticmethod
    def _require_span(start: date, end: date) -> None:
        if start > end:
            raise ValueError("start is after end")
        if (end - start).days >= MAX_SPAN_DAYS:
            raise ValueError(f"Ask for at most {MAX_SPAN_DAYS} days at a time")

    def cat_weight(self, cat_id: int, start: date, end: date) -> dict[str, Any]:
        self._cats.cat(cat_id)
        self._require_span(start, end)
        history = self.history()
        i = history.last_complete()
        anchor = history.anchors.get(cat_id)
        out: dict[str, Any] = {
            "cat_id": cat_id,
            "timezone": str(history.tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "anchor": None
            if anchor is None
            else {
                "weight_lb": anchor.weight_lb,
                "spread_lb": anchor.spread_lb,
                "set_at": anchor.set_at.isoformat(timespec="milliseconds"),
                "source": anchor.source,
            },
            "readings": [],
            "line": [],
            "bands": [],
            "latest": None,
        }
        if history.first is None or i is None:
            out["status"] = self._weight_status(None)
            return out
        readings = history.readings.get(cat_id)
        low, high = (
            max(history.index(max(start, history.first)), 0),
            min(history.index(min(end, history.today)), len(history.days) - 1),
        )
        out["bands"] = _bands(history, low, high)
        if readings is None:
            out["status"] = self._weight_status(None)
            return out
        for index in range(low, high + 1):
            state = checks.weight_state(history, cat_id, index)
            if state.stage == "none" or history.days[index].state == "none" or state.median is None:
                continue
            noise = (state.noise_pct or 0) / 100 * state.median
            out["line"].append(
                {
                    "date": history.date_of(index).isoformat(),
                    "median": round(state.median, 2),
                    "low": round(state.median - noise, 2),
                    "high": round(state.median + noise, 2),
                }
            )
        spread = {p["date"]: (p["median"], p["high"] - p["median"]) for p in out["line"]}
        for visit in readings.all:
            if not low <= visit.day <= high:
                continue
            day = history.date_of(visit.day).isoformat()
            why = None
            if not history.reliable[visit.day]:
                why = "unreliable"
            elif day in spread and abs(visit.weight - spread[day][0]) > max(
                3 * spread[day][1], OFF_CLUSTER_SHARE * spread[day][0]
            ):
                why = "far_from_usual"
            out["readings"].append(
                {
                    "date": day,
                    "hour": round(visit.local.hour + visit.local.minute / 60, 2),
                    "lb": visit.weight,
                    "hollow": why is not None,
                    "why": why,
                }
            )
        end_index = min(high, i)
        latest = checks.weight_state(history, cat_id, end_index)
        out["latest"] = {
            "date": history.date_of(end_index).isoformat(),
            "stage": latest.stage,
            "median_lb": None if latest.median is None else round(latest.median, 2),
            "readings": latest.readings,
            "change_pct": None if latest.pct is None else round(latest.pct, 1),
            "range_pct": None
            if latest.pct is None
            else [round(latest.pct_low, 1), round(latest.pct_high, 1)],
            "noise_pct": latest.noise_pct,
        }
        out["status"] = self._weight_status(latest)
        slope = checks.slope_state(history, cat_id, end_index)
        out["slope"] = (
            None
            if slope is None
            else {
                "per_month_pct": slope.per_month_pct,
                "range_pct": [slope.low_pct, slope.high_pct],
                "days": checks.SLOPE_DAYS,
            }
        )
        return out

    @staticmethod
    def _weight_status(state: checks.WeightState | None) -> dict[str, Any]:
        if state is None or state.stage == "none":
            days = checks.TREND_MIN_DAYS if state is None else state.need_days
            return {"line": False, "text": f"The trend line needs {_wait(days)} of readings."}
        if state.stage == "trend":
            return {
                "line": True,
                "text": f"Change from usual needs {_wait(state.need_days)} of readings."
                if state.need_days
                else "Change from usual needs more readings in the last two weeks.",
            }
        return {"line": True, "text": None}

    def rhythm(self, cat_id: int | None, start: date, end: date) -> dict[str, Any]:
        history = self.history()
        self._require_span(start, end)
        if cat_id is not None:
            self._cats.cat(cat_id)
        end = min(end, history.today)
        out: dict[str, Any] = {
            "cat_id": cat_id,
            "timezone": str(history.tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "actogram": [],
            "profile": None,
            "heatmap": None,
        }
        if history.first is None or end < history.first:
            return out
        shown_start = max(start, end - timedelta(days=ACTOGRAM_DAYS - 1), history.first)
        out["actogram_start"] = shown_start.isoformat()
        low, high = history.index(shown_start), history.index(end)
        by_day: dict[int, list[dict[str, Any]]] = {}
        for visit in history.visits:
            if not low <= visit.day <= high:
                continue
            if cat_id is not None and visit.cat_id not in (cat_id, None):
                continue
            by_day.setdefault(visit.day, []).append(
                {
                    "hour": round(visit.started.hour + visit.started.minute / 60, 2),
                    "minutes": None
                    if visit.duration_s is None
                    else round(visit.duration_s / 60, 1),
                    "cat_id": visit.cat_id,
                    "sure": visit.cat_id is not None,
                }
            )
        for index in range(low, high + 1):
            day = history.days[index]
            out["actogram"].append(
                {
                    "date": day.date.isoformat(),
                    "state": day.state,
                    "unreliable": day.unreliable is not None,
                    "visits": by_day.get(index, []),
                }
            )
        out["profile"] = self._profile(history, cat_id, history.index(end))
        out["heatmap"] = self._heatmap(history, cat_id, history.index(end))
        return out

    def _series(self, history: History, cat_id: int | None):
        visits = history.visits if cat_id is None else history.by_cat.get(cat_id, [])
        return visits, (history.household if cat_id is None else history.cat_series.get(cat_id))

    def _profile(self, history: History, cat_id: int | None, i: int) -> dict[str, Any]:
        visits, series = self._series(history, cat_id)
        recent_low = i - checks.RHYTHM_RECENT + 1
        base_low = recent_low - checks.RHYTHM_BASELINE
        recent = [0] * checks.BLOCKS
        usual = [0] * checks.BLOCKS
        for visit in visits:
            block = visit.started.hour // checks.BLOCK_HOURS
            if recent_low <= visit.day <= i:
                recent[block] += 1
            elif base_low <= visit.day < recent_low:
                usual[block] += 1
        base_days = 0 if series is None else series.window(base_low, recent_low - 1)[1]
        has_usual = base_days >= checks.RHYTHM_MIN_COUNTED[1] and sum(usual) > 0
        total, usual_total = sum(recent), sum(usual)
        return {
            "recent_days": checks.RHYTHM_RECENT,
            "recent_visits": total,
            "has_usual": has_usual,
            "blocks": [
                {
                    "label": _block_label(b),
                    "recent": recent[b],
                    "share": round(recent[b] / total, 3) if total else 0.0,
                    "usual_share": round(usual[b] / usual_total, 3) if has_usual else None,
                }
                for b in range(checks.BLOCKS)
            ],
        }

    def _heatmap(self, history: History, cat_id: int | None, i: int) -> dict[str, Any]:
        visits, series = self._series(history, cat_id)
        if not visits or series is None:
            return {"ready": False, "need_days": checks.RHYTHM_MIN_DAYS}
        have = i - visits[0].day + 1
        if have < checks.RHYTHM_MIN_DAYS:
            return {"ready": False, "need_days": checks.RHYTHM_MIN_DAYS - have}
        low = max(visits[0].day, i - 83)
        cells = [[0] * 4 for _ in range(7)]
        weeks = [0] * 7
        for index in range(low, i + 1):
            if history.counted[index]:
                weeks[history.date_of(index).weekday()] += 1
        for visit in visits:
            if low <= visit.day <= i and history.counted[visit.day]:
                cells[visit.local.weekday()][visit.started.hour // 6] += 1
        return {
            "ready": True,
            "need_days": 0,
            "rows": [
                [None if not weeks[d] else round(cells[d][c] / weeks[d], 2) for c in range(4)]
                for d in range(7)
            ],
            "days_counted": weeks,
        }

    # Backtest --------------------------------------------------------------------------------

    def backtest(self, *, hourly: bool = True) -> dict[str, list[str]]:
        """When each check would have fired, replayed over the whole history, newest knowledge
        only as of each day. Day-based checks are read at the end of each day, silence hourly."""
        history = self.history()
        fired: dict[str, set[str]] = {}
        if history.first is None:
            return {}
        for i in range(len(history.days) - 1):
            for finding in self.evaluate_day(history, i):
                key = (
                    finding.kind
                    if finding.cat_id is None
                    else f"{finding.kind}:{history.cats[finding.cat_id]['name']}"
                )
                fired.setdefault(key, set()).add(history.date_of(i).isoformat())
        watch = checks.SilenceWatch(history, self._cats.coverage.heard_share())
        step = timedelta(hours=1 if hourly else 24)
        t = datetime.combine(history.first, datetime.min.time(), tzinfo=history.tz).astimezone(UTC)
        stop = min(self._clock(), history.visits[-1].at + timedelta(days=30))
        while t <= stop:
            if watch.at(t).fired:
                fired.setdefault("silence", set()).add(t.astimezone(history.tz).date().isoformat())
            t += step
        for cat_id in history.by_cat:
            for burst in checks.find_bursts(history, cat_id):
                key = f"burst:{history.cats[cat_id]['name']}"
                fired.setdefault(key, set()).add(
                    burst.first.astimezone(history.tz).date().isoformat()
                )
        return {key: sorted(days) for key, days in sorted(fired.items())}


def _runs(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Consecutive days of one kind of trouble as {start, end, kind}, worst kind first."""
    runs: list[dict[str, str]] = []
    for row in rows:
        kind = (
            "unreliable"
            if row["unreliable"] is not None
            else "scale"
            if row["scale_disturbed"]
            else "no_data"
            if row["state"] == "none"
            else None
        )
        if kind is None:
            continue
        last = runs[-1] if runs else None
        if (
            last
            and last["kind"] == kind
            and date.fromisoformat(last["end"]) + timedelta(1) == date.fromisoformat(row["date"])
        ):
            last["end"] = row["date"]
        else:
            runs.append({"start": row["date"], "end": row["date"], "kind": kind})
    return runs


def _bands(history: History, low: int, high: int) -> list[dict[str, str]]:
    rows = [
        {
            "date": history.date_of(i).isoformat(),
            "state": history.days[i].state,
            "unreliable": history.days[i].unreliable,
            "scale_disturbed": history.disturbed[i],
        }
        for i in range(low, high + 1)
    ]
    return _runs(rows)
