from __future__ import annotations

import bisect
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from . import identity
from .coverage import Coverage
from .photos import CatPhotos
from .store import ReplayRow, Store

# The robot reports one weight per visit and no name, so a cat is told apart only by how much it
# weighs. How that is done, and how sure it is, lives in identity.py.

# How many of a cat's latest readings are looked at for its latest weight.
LATEST_READINGS = 20

# Auto-assignment, suggestions and the needs-a-cat count look this far back. Older visits keep
# whatever label they had, so a long-ago correction is never rewritten by a rerun.
RECENT_DAYS = 30

# A cat's recent weight is the median of its confident readings this far back, and a change
# from its anchor is shown only when there are enough of them: the scale is off by 3 to 4
# percent on each reading, so a few readings say nothing about a change of a few percent.
RECENT_WEIGHT_DAYS = 14
MIN_READINGS_FOR_CHANGE = 8

# Weights within this gap of the next one are one cluster of unlabelled visits. Readings of one
# cat are dense, so a gap beyond this is a different cat.
CLUSTER_GAP_LB = 0.5

# Chained gaps can join two cats into one long cluster. A cluster wider than this is split at
# its largest gap, since one cat does not span two pounds.
CLUSTER_MAX_SPAN_LB = 2.0

# Smaller groups are as likely to be a stray reading as a cat.
CLUSTER_MIN_VISITS = 3

# Whisker waits about a week. Suggestions need a few days so that a single busy afternoon is not
# mistaken for the household, and enough visits for every cat to show up more than once.
SUGGESTION_MIN_DAYS = 3
SUGGESTION_MIN_VISITS = 12

# Rates and averages use the last week: long enough to smooth a day, short enough to notice change.
RATE_DAYS = 7

# A rate over fewer days than this is one or two days, not a rate.
MIN_RATE_DAYS = 3

# "Most common visit time" looks at about a month, enough visits to have a habit and recent enough
# to follow a changed routine.
HABIT_DAYS = 30

# An hour is a habit only when it clearly stands out: at least this share of the month's visits
# and at least this many, and strictly more than any other hour. A tie is no habit.
HABIT_MIN_SHARE = 0.25
HABIT_MIN_VISITS = 3

# The seven completed days before today. A calendar week would hold a single day on Sunday.
WEEK_DAYS = 7

# A series for the charts is bounded so that one request cannot ask for a decade of days.
MAX_SERIES_DAYS = 366


class CatNotFound(LookupError):
    pass


class VisitNotFound(LookupError):
    pass


class CatNameTaken(ValueError):
    pass


class CatArchived(ValueError):
    pass


class NotEnoughReadings(ValueError):
    """A cat has too few confident readings to anchor on."""


@dataclass(frozen=True, slots=True)
class Cluster:
    center: float
    low: float
    high: float
    visits: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "center_lb": round(self.center, 2),
            "low_lb": round(self.low, 2),
            "high_lb": round(self.high, 2),
            "visits": self.visits,
        }


def _split_wide(group: list[float]) -> list[list[float]]:
    if len(group) < 2 or group[-1] - group[0] <= CLUSTER_MAX_SPAN_LB:
        return [group]
    middle = len(group) / 2
    # Equal gaps are common in a long chain, and cutting at the first would shave off one reading.
    cut = (
        max(
            range(len(group) - 1),
            key=lambda i: (group[i + 1] - group[i], -abs(i + 1 - middle)),
        )
        + 1
    )
    return _split_wide(group[:cut]) + _split_wide(group[cut:])


def find_clusters(weights: Sequence[float]) -> list[Cluster]:
    ordered = sorted(weights)
    if not ordered:
        return []
    groups: list[list[float]] = [[ordered[0]]]
    for weight in ordered[1:]:
        if weight - groups[-1][-1] > CLUSTER_GAP_LB:
            groups.append([])
        groups[-1].append(weight)
    clusters = [
        Cluster(statistics.median(part), part[0], part[-1], len(part))
        for group in groups
        for part in _split_wide(group)
        if len(part) >= CLUSTER_MIN_VISITS
    ]
    return sorted(clusters, key=lambda cluster: cluster.center)


def _weight(visit: dict[str, Any]) -> float | None:
    value = visit["detail"].get("weight_lb")
    return None if value is None else float(value)


def _when(visit: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(visit["at"])


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)


def visit_started(visit: dict[str, Any], zone: tzinfo) -> datetime:
    """When the cat went in, in local time, or the end if that is unknown or the day before."""
    ended = _when(visit).astimezone(zone)
    started = visit["detail"].get("started")
    if started is None:
        return ended
    began = datetime.fromisoformat(started).astimezone(zone)
    return began if began.date() == ended.date() else ended


def visit_minutes(visits: Sequence[dict[str, Any]], state: str) -> float | None:
    """Minutes in the box for a day, or None when that is not known.

    History imported from Whisker has no durations, and a day with only some of them would be a
    sum of part of the visits, so a figure is given only when every visit has its duration.
    """
    if state == "none":
        return None
    if not visits:
        return 0.0 if state == "full" else None
    seconds = [v["detail"].get("duration_s") for v in visits]
    if any(value is None for value in seconds):
        return None
    return round(sum(seconds) / 60, 1)


def clear_habit_hour(hours: Counter[int], visits: int) -> int | None:
    """The hour most visits start in, if it clearly stands out from the others."""
    if not hours:
        return None
    ranked = hours.most_common(2)
    top_hour, top = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if top < max(HABIT_MIN_VISITS, HABIT_MIN_SHARE * visits) or top == runner_up:
        return None
    return top_hour


class CatTracker:
    """Names the cat behind each visit, from weight alone.

    Nothing here is stored besides the cats and the labels. Usual weights are always worked out
    from the labelled visits, so they follow the labels without any separate bookkeeping.
    """

    def __init__(
        self,
        store: Store,
        timezone: tzinfo = UTC,
        clock: Callable[[], datetime] | None = None,
        photos: CatPhotos | None = None,
    ) -> None:
        self._store = store
        self._tz = timezone
        self._clock = clock or (lambda: datetime.now(UTC))
        self._photos = photos
        self.coverage = Coverage(store, timezone, self._clock)

    @property
    def timezone(self) -> tzinfo:
        return self._tz

    def day_start(self, day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self._tz).astimezone(UTC)

    def today(self) -> date:
        return self._clock().astimezone(self._tz).date()

    def create_cat(
        self,
        name: str,
        reference_weight_lb: float | None,
        *,
        breed: str | None = None,
        birthday: date | None = None,
        sex: str = "unknown",
    ) -> dict[str, Any]:
        self._require_free_name(name)
        cat_id = self._store.add_cat(
            name, reference_weight_lb, self._clock(), breed=breed, birthday=birthday, sex=sex
        )
        self._relabel_recent()
        return self.cat(cat_id)

    def update_cat(
        self,
        cat_id: int,
        *,
        name: str | None = None,
        archived: bool | None = None,
        profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Change what was sent; ``profile`` holds only the profile fields that were sent."""
        cat = self._row(cat_id)
        if name is not None and name != cat["name"]:
            self._require_free_name(name, ignoring=cat_id)
            self._store.rename_cat(cat_id, name)
        if profile:
            stored = {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in profile.items()
            }
            self._store.update_cat_profile(cat_id, stored)
            if "reference_weight_lb" in stored:
                self._relabel_recent()
        if archived is not None and archived != (cat["archived_at"] is not None):
            if not archived:
                self._require_free_name(name or cat["name"], ignoring=cat_id)
            self._store.set_cat_archived(cat_id, self._clock() if archived else None)
            self._relabel_recent()
        return self.cat(cat_id)

    def cat(self, cat_id: int) -> dict[str, Any]:
        return self._view(self._row(cat_id))

    def cats(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return [self._view(row) for row in self._store.cats(include_archived=include_archived)]

    def _row(self, cat_id: int) -> dict[str, Any]:
        row = self._store.cat(cat_id)
        if row is None:
            raise CatNotFound(cat_id)
        return row

    def _require_free_name(self, name: str, *, ignoring: int | None = None) -> None:
        taken = {row["name"].casefold() for row in self._store.cats() if row["id"] != ignoring}
        if name.casefold() in taken:
            raise CatNameTaken(name)

    def label_visit(self, event_id: int, label: str, cat_id: int | None) -> dict[str, Any]:
        visit = self._store.visit(event_id)
        if visit is None:
            raise VisitNotFound(event_id)
        if label == "cat":
            assert cat_id is not None  # noqa: S101
            if self._row(cat_id)["archived_at"] is not None:
                raise CatArchived(cat_id)
        if label == "unknown":
            self._store.clear_label(event_id)
        else:
            self._store.set_user_label(event_id, label, cat_id)
        self.relabel(since=_when(visit))
        result = self._store.visit(event_id)
        assert result is not None  # noqa: S101
        return result

    def on_visit_saved(self, event_id: int) -> None:
        visit = self._store.visit(event_id)
        if visit is not None:
            self.relabel(since=_when(visit))

    def _relabel_recent(self) -> None:
        self.relabel(since=self._clock() - timedelta(days=RECENT_DAYS))

    def relabel(self, since: datetime) -> None:
        """Replay every visit in order, as if each had just arrived, and save what is new.

        The whole history is replayed so that the trackers are where they would have been, but
        only visits from ``since`` on are written, so an old label is never rewritten by a rerun.
        A hand-made label is never replaced, and counts as evidence.
        """
        rows = self._store.replay_rows()
        times = [datetime.fromisoformat(row.at) for row in rows]
        result = self._run(rows)
        for cat_id, anchor in result.new_anchors.items():
            self._store.put_anchor(cat_id, anchor)
        decisions: dict[int, int | None] = {}
        identities: dict[int, tuple[str, int | None, float | None]] = {}
        for row, at in zip(rows, times, strict=True):
            if row.source == "user" or at < since:
                continue
            verdict = result.verdicts[row.id]
            named = verdict.cat_id if verdict.status == "confident" else None
            held = row.cat_id if row.label == "cat" and row.source == "auto" else None
            if named != held:
                decisions[row.id] = named
            identity_now = (verdict.status, verdict.candidate_id, verdict.confidence)
            if identity_now != row.identity:
                identities[row.id] = identity_now
        if decisions or identities:
            self._store.apply_auto_labels(decisions, identities)

    def rebuild(self) -> None:
        """Label the whole history afresh; hand-made labels stay as they are."""
        self.relabel(since=datetime.min.replace(tzinfo=UTC))

    def _run(self, rows: list[ReplayRow]) -> identity.Result:
        seeds = [identity.Seed(cat["id"], cat["reference_weight_lb"]) for cat in self._store.cats()]
        readings = [
            identity.Reading(
                row.id,
                datetime.fromisoformat(row.at),
                row.weight_lb,
                (row.label, row.cat_id) if row.source == "user" else None,
            )
            for row in rows
        ]
        heard = self._heard_days(readings[0].at) if readings else None
        return identity.assign(
            readings,
            seeds,
            self._store.anchors(),
            heard_days=heard or identity.elapsed_days,
            trusted=self._trusted(),
        )

    def _heard_days(self, first: datetime):
        """Days of robot data between two instants, so a gap in coverage is not a silent cat."""
        start = first.astimezone(self._tz).date() - timedelta(days=1)
        known = self.coverage.days(start, self.today())

        starts = [self.day_start(day.date) for day in known]
        ends = [self.day_start(day.date + timedelta(days=1)) for day in known]
        weights = [0.0 if day.state == "none" else 1.0 for day in known]
        # Heard seconds from the first day up to the start of each day, so a span is two lookups.
        before = [0.0]
        for begin, end, weight in zip(starts, ends, weights, strict=True):
            before.append(before[-1] + (end - begin).total_seconds() * weight)

        def heard_until(moment: datetime) -> float:
            index = bisect.bisect_right(starts, moment) - 1
            if index < 0:
                return 0.0
            inside = min(moment, ends[index]) - starts[index]
            return before[index] + inside.total_seconds() * weights[index]

        def heard(low: datetime, high: datetime) -> float:
            return max(0.0, heard_until(high) - heard_until(low)) / 86400

        return heard

    def _trusted(self):
        unreliable = self._store.unreliable_ranges()

        def trusted(at: datetime) -> bool:
            day = at.astimezone(self._tz).date()
            return not any(
                date.fromisoformat(r["start_date"]) <= day <= date.fromisoformat(r["end_date"])
                for r in unreliable
            )

        return trusted

    def anchors(self) -> dict[int, dict[str, Any]]:
        return {cat_id: self._anchor_view(a) for cat_id, a in self._store.anchors().items()}

    @staticmethod
    def _anchor_view(anchor: identity.Anchor) -> dict[str, Any]:
        return {
            "weight_lb": anchor.weight_lb,
            "spread_lb": anchor.spread_lb,
            "visits": anchor.visits,
            "visits_per_day": None
            if anchor.visits_per_day is None
            else round(anchor.visits_per_day, 2),
            "set_at": anchor.set_at.isoformat(timespec="milliseconds"),
            "source": anchor.source,
        }

    def set_anchor(self, cat_id: int, weight_lb: float | None) -> dict[str, Any]:
        """Re-anchor a cat to the weight given, or to its latest confident readings."""
        self._row(cat_id)
        now = self._clock()
        recent = [
            (_when(v), w)
            for v in self._store.visits(cat_id=cat_id, newest_first=True, limit=LATEST_READINGS)
            if (w := _weight(v)) is not None
        ]
        previous = self._store.anchors().get(cat_id)
        rate = previous.visits_per_day if previous is not None else None
        if weight_lb is not None:
            spread = previous.spread_lb if previous is not None else identity.INITIAL_SD_LB
            anchor = identity.Anchor(weight_lb, spread, 0, rate, now, "owner")
        elif len(recent) >= identity.ANCHOR_VISITS:
            anchor = identity.make_anchor(recent, now, rate, "re-anchored")
        else:
            raise NotEnoughReadings(cat_id)
        self._store.put_anchor(cat_id, anchor)
        self.relabel(since=now - timedelta(days=RECENT_DAYS))
        return self._anchor_view(anchor)

    def clear_anchor(self, cat_id: int) -> None:
        self._row(cat_id)
        self._store.delete_anchor(cat_id)
        self.rebuild()

    def identity_overview(self, days: int = RECENT_DAYS) -> dict[str, Any]:
        """How many recent visits could be named, and which cats have gone quiet."""
        now = self._clock()
        rows = self._store.replay_rows()
        times = [datetime.fromisoformat(row.at) for row in rows]
        result = self._run(rows)
        counts: Counter[str] = Counter()
        for row, at in zip(rows, times, strict=True):
            if at < now - timedelta(days=days) or row.label == "not_cat":
                continue
            counts["user" if row.source == "user" else result.verdicts[row.id].status] += 1
        total = sum(counts.values())
        named = counts["confident"] + counts["user"]
        return {
            "days": days,
            "visits": total,
            "named": named,
            "not_sure": counts["unsure"] + counts["off_cluster"] + counts["unsettled"],
            "unsettled": counts["unsettled"],
            "no_weight": counts["no_weight"],
            "by_status": dict(counts),
            "silent_cats": identity.silence_summary(
                [s for s in result.silences if s.until is None], now
            ),
        }

    def needing_cat(self) -> int:
        return len(self._waiting())

    def _waiting(self, *, newest_first: bool = False, limit: int | None = None):
        since = self._clock() - timedelta(days=RECENT_DAYS)
        # An imported weight that fits no cat is history to browse, not a question to ask now,
        # and a visit with no weight is one nobody could answer from the scale.
        found = self._store.visits(
            since=since, needs_cat=True, exclude_imported=True, newest_first=newest_first
        )
        weighed = [v for v in found if _weight(v) is not None]
        return weighed if limit is None else weighed[:limit]

    def visits(
        self, *, days: int, cat_id: int | None = None, needs_cat: bool = False
    ) -> list[dict[str, Any]]:
        since = self._clock() - timedelta(days=days)
        return self._store.visits(since=since, cat_id=cat_id, needs_cat=needs_cat)

    def confident_visits(
        self,
        cat_id: int,
        *,
        since: datetime | None = None,
        before: datetime | None = None,
        reliable_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Visits this cat is named for with confidence or by the owner, oldest first.

        Days marked unreliable are left out unless asked for, because a trend or a baseline
        should not be built on them.
        """
        found = self._store.visits(since=since, before=before, cat_id=cat_id)
        if not reliable_only:
            return found
        trusted = self._trusted()
        return [visit for visit in found if trusted(_when(visit))]

    def latest_unassigned_visit(self) -> dict[str, Any] | None:
        """The newest visit still waiting for a cat, with the cat its weight is most like."""
        found = self._waiting(newest_first=True, limit=1)
        if not found:
            return None
        visit = found[0]
        weight = _weight(visit)
        candidate = (visit.get("identity") or {}).get("candidate_cat_id")
        if candidate is None and weight is not None:
            candidate = self._nearest_cat(weight)
        return {
            "id": visit["id"],
            "at": visit["at"],
            "weight_lb": weight,
            "duration_s": visit["detail"].get("duration_s"),
            "closest_cat_id": candidate,
        }

    def _nearest_cat(self, weight: float) -> int | None:
        anchors = self._store.anchors()
        usual = {
            cat["id"]: anchors[cat["id"]].weight_lb
            if cat["id"] in anchors
            else cat["reference_weight_lb"]
            for cat in self._store.cats()
        }
        usual = {cat_id: value for cat_id, value in usual.items() if value is not None}
        return min(usual, key=lambda cat_id: abs(weight - usual[cat_id])) if usual else None

    def suggestions(self) -> dict[str, Any]:
        since = self._clock() - timedelta(days=RECENT_DAYS)
        unlabelled = self._store.visits(since=since, needs_cat=True)
        weighed = [(_when(v), w) for v in unlabelled if (w := _weight(v)) is not None]
        span_days = (
            (max(at for at, _ in weighed) - min(at for at, _ in weighed)).total_seconds() / 86400
            if weighed
            else 0.0
        )
        return {
            "enough_data": len(weighed) >= SUGGESTION_MIN_VISITS
            and span_days >= SUGGESTION_MIN_DAYS,
            "visits_considered": len(weighed),
            "days_of_data": round(span_days, 1),
            "min_visits": SUGGESTION_MIN_VISITS,
            "min_days": SUGGESTION_MIN_DAYS,
            "clusters": [c.as_dict() for c in find_clusters([w for _, w in weighed])],
        }

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        photo_version = None if self._photos is None else self._photos.version(row["id"])
        return {
            "id": row["id"],
            "name": row["name"],
            "color_index": row["color_index"],
            "breed": row["breed"],
            "birthday": row["birthday"],
            "sex": row["sex"],
            "reference_weight_lb": row["reference_weight_lb"],
            # The name this field had before the profile existed; same value, kept so an app
            # written against the old shape keeps working.
            "estimated_weight_lb": row["reference_weight_lb"],
            "has_photo": photo_version is not None,
            "photo_version": photo_version,
            "archived": row["archived_at"] is not None,
            "created_at": row["created_at"],
            "summary": self._summary(row),
        }

    def _summary(self, cat: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        cat_id = cat["id"]
        latest_visits = self._store.visits(cat_id=cat_id, newest_first=True, limit=LATEST_READINGS)
        latest = next((w for v in latest_visits if (w := _weight(v)) is not None), None)
        anchor = self._store.anchors().get(cat_id)

        week = self._store.visits(cat_id=cat_id, since=now - timedelta(days=RATE_DAYS))
        today = self._store.visits(cat_id=cat_id, since=self.day_start(self.today()))
        durations = [d for v in week if (d := v["detail"].get("duration_s")) is not None]

        recent = [
            w
            for v in self._store.visits(
                cat_id=cat_id, since=now - timedelta(days=RECENT_WEIGHT_DAYS)
            )
            if (w := _weight(v)) is not None
        ]
        recent_median = statistics.median(recent) if len(recent) >= 3 else None
        change = None
        if anchor is not None and len(recent) >= MIN_READINGS_FOR_CHANGE:
            change = (statistics.median(recent) - anchor.weight_lb) / anchor.weight_lb * 100
        usual = anchor.weight_lb if anchor is not None else cat["reference_weight_lb"]
        return {
            "latest_weight_lb": _round(latest, 2),
            "usual_weight_lb": _round(usual, 2),
            "anchor_weight_lb": None if anchor is None else anchor.weight_lb,
            "recent_weight_lb": _round(recent_median, 2),
            "recent_readings": len(recent),
            "visits_today": len(today),
            "visits_per_day": _round(self._visits_per_day(cat_id), 2),
            "avg_visit_s": _round(statistics.fmean(durations) if durations else None, 1),
            "weight_change_pct": _round(change, 1),
            "last_visit_at": latest_visits[0]["at"] if latest_visits else None,
            **self.overview(cat_id),
        }

    def _visits_per_day(self, cat_id: int) -> float | None:
        """Visits per day over the last week, counting only the days that can be averaged."""
        today = self.today()
        first = today - timedelta(days=RATE_DAYS)
        earliest = self._store.visits(cat_id=cat_id, limit=1)
        if not earliest:
            return None
        began = _when(earliest[0]).astimezone(self._tz).date()
        counted = [
            d
            for d in self.coverage.days(first, today - timedelta(days=1))
            if d.counts and d.date >= began
        ]
        if len(counted) < MIN_RATE_DAYS:
            return None
        visits = self._store.visits(
            cat_id=cat_id, since=self.day_start(first), before=self.day_start(today)
        )
        by_day = Counter(_when(v).astimezone(self._tz).date() for v in visits)
        return sum(by_day[day.date] for day in counted) / len(counted)

    def overview(self, cat_id: int) -> dict[str, Any]:
        """Yesterday and habits of one cat, for the tiles on the home and insights tabs."""
        today = self.today()
        known = self.coverage.by_day(today - timedelta(days=HABIT_DAYS - 1), today)
        yesterday_day = known[today - timedelta(days=1)]
        yesterday = self._store.visits(
            cat_id=cat_id,
            since=self.day_start(today - timedelta(days=1)),
            before=self.day_start(today),
        )
        weights = [w for w in map(_weight, yesterday) if w is not None]
        this_week = self._store.visits(
            cat_id=cat_id,
            since=self.day_start(today - timedelta(days=WEEK_DAYS)),
            before=self.day_start(today),
        )
        week_days = [known[today - timedelta(days=n)] for n in range(1, WEEK_DAYS + 1)]
        habit = self._store.visits(
            cat_id=cat_id, since=self.day_start(today - timedelta(days=HABIT_DAYS - 1))
        )
        hours = Counter(visit_started(v, self._tz).hour for v in habit)
        no_data = yesterday_day.state == "none"
        return {
            "yesterday_weight_lb": _round(statistics.median(weights) if weights else None, 2),
            "yesterday_visits": None if no_data else len(yesterday),
            "yesterday_minutes": visit_minutes(yesterday, yesterday_day.state),
            "yesterday_coverage": yesterday_day.state,
            "week_visits": len(this_week) if any(d.state != "none" for d in week_days) else None,
            "week_days_with_data": sum(1 for d in week_days if d.state != "none"),
            "common_visit_hour": clear_habit_hour(hours, len(habit)),
        }

    def series(self, cat_id: int, start: date, end: date) -> dict[str, Any]:
        self._row(cat_id)
        visits = self._store.visits(
            cat_id=cat_id, since=self.day_start(start), before=self.day_start(end + timedelta(1))
        )
        known = self.coverage.by_day(start, end)
        by_day: dict[date, list[dict[str, Any]]] = {}
        for visit in visits:
            by_day.setdefault(_when(visit).astimezone(self._tz).date(), []).append(visit)
        days = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            group = by_day.get(day, [])
            weights = [w for w in map(_weight, group) if w is not None]
            durations = [d for v in group if (d := v["detail"].get("duration_s")) is not None]
            covered = known[day]
            heard = covered.state != "none"
            days.append(
                {
                    "date": day.isoformat(),
                    "visits": len(group) if heard else None,
                    "median_weight_lb": _round(statistics.median(weights) if weights else None, 2),
                    "avg_visit_s": _round(statistics.fmean(durations) if durations else None, 1),
                    "coverage": covered.state,
                    "unreliable": covered.unreliable is not None,
                }
            )
        return {
            "cat_id": cat_id,
            "timezone": str(self._tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
        }
