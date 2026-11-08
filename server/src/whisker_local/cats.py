from __future__ import annotations

import statistics
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from .photos import CatPhotos
from .store import Store

# The robot reports one weight per visit and no name, so a cat is told apart only by how much it
# weighs. These values are tuned for the scatter of that scale, not for any particular cat.

# Visit to visit scatter for one cat is a few tenths of a pound (the scale is read while the cat
# shifts, and a cat eats and drinks). A visit this far from a cat's usual weight is more likely
# a guest, a hand on the scale or a different cat than that cat having a heavy day.
MATCH_TOLERANCE_LB = 0.75

# Cats whose usual weights are closer than about this cannot be told apart by one noisy reading.
# The nearest cat must beat the runner-up by this much, or the visit is left for the owner. Half
# a pound is about twice the scatter, so two cats a pound apart still separate cleanly.
MIN_MARGIN_LB = 0.5

# Usual weight is the median of this many latest labelled visits. A median ignores the odd
# wrong label, and 20 visits is about a week for a cat that visits a few times a day, which is
# short enough to follow a cat that is gaining or losing weight over months.
USUAL_VISITS = 20

# A new cat's estimate stands in for the visits it does not have yet. The first real visits
# nudge the usual weight (the median of readings and estimate pulls toward the readings) rather
# than replacing the estimate on one reading, and from this many visits on the estimate is gone.
MIN_VISITS_FOR_USUAL = 3

# Auto-assignment, suggestions and the needs-a-cat count look this far back. Older visits keep
# whatever label they had, so a long-ago correction is never rewritten by a rerun.
RECENT_DAYS = 30

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

# Weight is "in range" for the summary while the cat is this close to its usual weight. A cat's
# readings scatter by a few tenths of a pound, so a figure well past that has changed for real.
WEIGHT_RANGE_PCT = 7.0

# "Most common visit time" looks at about a month, enough visits to have a habit and recent enough
# to follow a changed routine.
HABIT_DAYS = 30

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


def usual_weight(weights: Sequence[float], estimate: float | None) -> float | None:
    """Median of the latest readings, padded with the owner's estimate while there are few."""
    samples = list(weights[-USUAL_VISITS:])
    if estimate is not None:
        samples.extend([estimate] * (MIN_VISITS_FOR_USUAL - len(samples)))
    return statistics.median(samples) if samples else None


def pick_cat(weight: float, usual: Mapping[int, float]) -> int | None:
    """The cat this weight clearly belongs to, or None when it is far from all or between two."""
    ranked = sorted((abs(weight - value), cat_id) for cat_id, value in usual.items())
    if not ranked or ranked[0][0] > MATCH_TOLERANCE_LB:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < MIN_MARGIN_LB:
        return None
    return ranked[0][1]


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
        """Replay visits from ``since`` in order, as if each had just arrived.

        Replaying in time order is what lets a cat's usual weight follow it as it gains or
        loses, while a user's label still counts as evidence and is never replaced.
        """
        histories: dict[int, deque[float]] = {}
        estimates: dict[int, float | None] = {}
        for cat in self._store.cats():
            earlier = self._store.visits(
                before=since, cat_id=cat["id"], newest_first=True, limit=USUAL_VISITS * 2
            )
            weights = [w for w in map(_weight, reversed(earlier)) if w is not None]
            histories[cat["id"]] = deque(weights[-USUAL_VISITS:], maxlen=USUAL_VISITS)
            estimates[cat["id"]] = cat["reference_weight_lb"]

        decisions: dict[int, int | None] = {}
        for visit in self._store.visits(since=since):
            weight = _weight(visit)
            if visit["label_source"] == "user":
                history = histories.get(visit["cat_id"]) if visit["label"] == "cat" else None
                if history is not None and weight is not None:
                    history.append(weight)
                continue
            chosen = None
            if weight is not None:
                usual = {
                    cat_id: value
                    for cat_id, history in histories.items()
                    if (value := usual_weight(list(history), estimates[cat_id])) is not None
                }
                chosen = pick_cat(weight, usual)
            decisions[visit["id"]] = chosen
            if chosen is not None and weight is not None:
                histories[chosen].append(weight)
        self._store.apply_auto_labels(decisions)

    def needing_cat(self) -> int:
        since = self._clock() - timedelta(days=RECENT_DAYS)
        return len(self._store.visits(since=since, needs_cat=True))

    def visits(
        self, *, days: int, cat_id: int | None = None, needs_cat: bool = False
    ) -> list[dict[str, Any]]:
        since = self._clock() - timedelta(days=days)
        return self._store.visits(since=since, cat_id=cat_id, needs_cat=needs_cat)

    def latest_unassigned_visit(self) -> dict[str, Any] | None:
        """The newest visit still waiting for a cat, with the cat its weight is closest to."""
        since = self._clock() - timedelta(days=RECENT_DAYS)
        found = self._store.visits(since=since, needs_cat=True, newest_first=True, limit=1)
        if not found:
            return None
        visit = found[0]
        weight = _weight(visit)
        usual = self._usual_weights()
        closest = (
            min(usual, key=lambda cat_id: abs(weight - usual[cat_id]))
            if weight is not None and usual
            else None
        )
        return {
            "id": visit["id"],
            "at": visit["at"],
            "weight_lb": weight,
            "duration_s": visit["detail"].get("duration_s"),
            "closest_cat_id": closest,
        }

    def _usual_weights(self) -> dict[int, float]:
        usual: dict[int, float] = {}
        for cat in self._store.cats():
            latest = self._store.visits(cat_id=cat["id"], newest_first=True, limit=USUAL_VISITS)
            weights = [w for w in map(_weight, reversed(latest)) if w is not None]
            value = usual_weight(weights, cat["reference_weight_lb"])
            if value is not None:
                usual[cat["id"]] = value
        return usual

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
        latest_visits = self._store.visits(cat_id=cat_id, newest_first=True, limit=USUAL_VISITS)
        weights = [w for w in map(_weight, reversed(latest_visits)) if w is not None]
        usual = usual_weight(weights, cat["reference_weight_lb"])
        latest = weights[-1] if weights else None

        week = self._store.visits(cat_id=cat_id, since=now - timedelta(days=RATE_DAYS))
        today = self._store.visits(cat_id=cat_id, since=self.day_start(self.today()))
        durations = [d for v in week if (d := v["detail"].get("duration_s")) is not None]

        per_day = None
        if latest_visits:
            first = self._store.visits(cat_id=cat_id, limit=1)[0]
            tracked_days = (now - _when(first)).total_seconds() / 86400
            per_day = len(week) / min(float(RATE_DAYS), max(1.0, tracked_days))

        change = None
        if latest is not None and usual:
            change = (latest - usual) / usual * 100
        return {
            "latest_weight_lb": _round(latest, 2),
            "usual_weight_lb": _round(usual, 2),
            "visits_today": len(today),
            "visits_per_day": _round(per_day, 2),
            "avg_visit_s": _round(statistics.fmean(durations) if durations else None, 1),
            "weight_change_pct": _round(change, 1),
            "last_visit_at": latest_visits[0]["at"] if latest_visits else None,
            **self.overview(cat_id),
        }

    def overview(self, cat_id: int) -> dict[str, Any]:
        """Yesterday and habits of one cat, for the tiles on the home and insights tabs."""
        today = self.today()
        yesterday = self._store.visits(
            cat_id=cat_id,
            since=self.day_start(today - timedelta(days=1)),
            before=self.day_start(today),
        )
        weights = [w for w in map(_weight, yesterday) if w is not None]
        seconds = sum(v["detail"].get("duration_s") or 0 for v in yesterday)
        this_week = self._store.visits(
            cat_id=cat_id,
            since=self.day_start(today - timedelta(days=WEEK_DAYS)),
            before=self.day_start(today),
        )
        habit = self._store.visits(
            cat_id=cat_id, since=self.day_start(today - timedelta(days=HABIT_DAYS - 1))
        )
        hours = Counter(_when(v).astimezone(self._tz).hour for v in habit)
        return {
            "yesterday_weight_lb": _round(statistics.median(weights) if weights else None, 2),
            "yesterday_visits": len(yesterday),
            "yesterday_minutes": _round(seconds / 60, 1),
            "week_visits": len(this_week),
            # Ties go to the earlier hour so that the answer does not flip between requests.
            "common_visit_hour": min(hours, key=lambda h: (-hours[h], h)) if hours else None,
        }

    def series(self, cat_id: int, start: date, end: date) -> dict[str, Any]:
        self._row(cat_id)
        visits = self._store.visits(
            cat_id=cat_id, since=self.day_start(start), before=self.day_start(end + timedelta(1))
        )
        by_day: dict[date, list[dict[str, Any]]] = {}
        for visit in visits:
            by_day.setdefault(_when(visit).astimezone(self._tz).date(), []).append(visit)
        days = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            group = by_day.get(day, [])
            weights = [w for w in map(_weight, group) if w is not None]
            durations = [d for v in group if (d := v["detail"].get("duration_s")) is not None]
            days.append(
                {
                    "date": day.isoformat(),
                    "visits": len(group),
                    "median_weight_lb": _round(statistics.median(weights) if weights else None, 2),
                    "avg_visit_s": _round(statistics.fmean(durations) if durations else None, 1),
                }
            )
        return {
            "cat_id": cat_id,
            "timezone": str(self._tz),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
        }
