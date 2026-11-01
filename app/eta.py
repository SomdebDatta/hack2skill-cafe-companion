"""Wait-time model.

Deliberately a transparent formula, not a trained model: it is reproducible on stage and
the agent can narrate *why* a number came out, which reads as more intelligent than an
opaque prediction. See README for where BigQuery AI.FORECAST would slot in.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).parent / "data"
TRAFFIC = json.loads((DATA / "traffic.json").read_text())

STATIONS = 2          # two baristas working the queue in parallel
MIN_ETA = 1

_BY_DAY_HOUR = {(r["day"], r["hour"]): r["orders"] for r in TRAFFIC}
_WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MEDIAN = sorted(r["orders"] for r in TRAFFIC)[len(TRAFFIC) // 2]


def _now():
    return datetime.now()


def rush_multiplier(when: datetime | None = None) -> tuple[float, str]:
    """How busy this hour runs historically, as a multiplier on queue drain time."""
    when = when or _now()
    day = _WEEKDAY[when.weekday()]
    hour = max(7, min(18, when.hour))
    expected = _BY_DAY_HOUR.get((day, hour), _MEDIAN)
    mult = round(max(0.8, min(1.6, expected / _MEDIAN)), 2)
    if mult >= 1.3:
        label = "peak rush"
    elif mult >= 1.1:
        label = "busy"
    elif mult <= 0.9:
        label = "quiet"
    else:
        label = "steady"
    return mult, label


HANDOVER = 1.0  # minutes to bag a grab-and-go item and hand it over


def _is_premade(item: dict) -> bool:
    return bool(item.get("premade"))


def order_prep_minutes(items: list[dict]) -> float:
    """Barista work an order adds to the queue.

    Drinks share one espresso machine, so they are not fully parallel: take the longest
    item in full, then 40% of the rest. Grab-and-go items (already brewed, or sitting in
    the case) add no barista work at all.
    """
    made = [i for i in items if not _is_premade(i)]
    if not made:
        return 0.0
    preps = sorted((i["prep_minutes"] * i.get("qty", 1) for i in made), reverse=True)
    return preps[0] + 0.4 * sum(preps[1:])


def estimate(items: list[dict], queue_ahead: list[dict], when: datetime | None = None) -> dict:
    """Return an ETA plus the breakdown that produced it."""
    mult, label = rush_multiplier(when)
    queue_work = sum(order_prep_minutes(o["items"]) for o in queue_ahead)
    drain = (queue_work / STATIONS) * mult
    own = order_prep_minutes(items)

    # An order of only grab-and-go items skips the queue entirely — nothing in it
    # needs the machine, so it is handed straight over.
    all_premade = bool(items) and all(_is_premade(i) for i in items)
    if all_premade:
        drain, total = 0.0, max(MIN_ETA, round(HANDOVER))
    else:
        total = max(MIN_ETA, round(drain + own))

    return {
        "eta_minutes": total,
        "position": len(queue_ahead) + 1,
        "breakdown": {
            "queue_ahead": len(queue_ahead),
            "queue_work_minutes": round(queue_work, 1),
            "stations": STATIONS,
            "drain_minutes": round(drain, 1),
            "own_prep_minutes": round(own, 1),
            "rush_multiplier": mult,
            "rush_label": label,
            "skips_queue": all_premade,
        },
    }


def best_times_today(when: datetime | None = None) -> dict:
    """Hourly expected load for today, plus the quietest upcoming hours."""
    when = when or _now()
    day = _WEEKDAY[when.weekday()]
    hours = [
        {"hour": h, "orders": _BY_DAY_HOUR.get((day, h), 0)} for h in range(7, 19)
    ]
    peak = max(h["orders"] for h in hours) or 1
    for h in hours:
        h["load"] = round(h["orders"] / peak, 2)
    upcoming = [h for h in hours if h["hour"] > when.hour]
    quietest = sorted(upcoming, key=lambda h: h["orders"])[:3]
    return {
        "day": day,
        "current_hour": when.hour,
        "hours": hours,
        "quietest_upcoming": sorted(quietest, key=lambda h: h["hour"]),
    }
