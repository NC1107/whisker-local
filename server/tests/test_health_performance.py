from __future__ import annotations

import time
from datetime import timedelta

from health_data import NOW, CatPlan, build

# Twenty thousand visits is about eight years of two busy cats. The wall time is generous so
# the test catches a quadratic step, not a slow machine.
BUDGET_SECONDS = 6.0


def test_notes_and_charts_stay_cheap_at_twenty_thousand_visits(tmp_path):
    plans = [CatPlan("Ada", 10.0, per_day=(3, 4, 5)), CatPlan("Bo", 9.0, per_day=(3, 4, 5))]
    world = build(tmp_path, plans, days=2600)
    assert len(world.visit_ids) > 20000

    began = time.perf_counter()
    notes = world.service.notes()
    quality = world.service.quality(NOW.date() - timedelta(days=700), NOW.date())
    chart = world.service.cat_weight(
        world.cat_ids["Ada"], NOW.date() - timedelta(days=1000), NOW.date()
    )
    rhythm = world.service.rhythm(None, NOW.date() - timedelta(days=90), NOW.date())
    elapsed = time.perf_counter() - began

    assert notes["waiting"] and len(quality["days"]) == 701
    assert len(chart["line"]) > 900 and len(rhythm["actogram"]) == 90
    assert elapsed < BUDGET_SECONDS, f"took {elapsed:.1f}s"
