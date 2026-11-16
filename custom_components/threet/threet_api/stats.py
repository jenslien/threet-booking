"""Training statistics computed from 3T's workout summary (no Home Assistant imports)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .client import Booking, _parse_dt
from .suggest import WEEKDAYS

MONTHS = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]
NO_SHOW_FEE = 60


@dataclass(frozen=True)
class Workout:
    start: datetime  # local time
    name: str
    center: str


def parse_workouts(summary: dict[str, Any], tz: tzinfo) -> list[Workout]:
    workouts = []
    for w in summary.get("userWorkouts") or []:
        if not (start := _parse_dt(w.get("workoutDate"))):
            continue
        center = ((w.get("center") or {}).get("page") or {}).get("title") or ""
        workouts.append(Workout(start.astimezone(tz), (w.get("workoutName") or "").strip(), center))
    return sorted(workouts, key=lambda w: w.start)


def summary_months(summary: dict[str, Any], tz: tzinfo) -> dict[str, int]:
    """{"2025-03": 12, ...} from the summary's per-year month buckets."""
    months = {}
    for year in summary.get("years") or []:
        for m in year.get("months") or []:
            if dt := _parse_dt(m.get("month")):
                months[dt.astimezone(tz).strftime("%Y-%m")] = int(m.get("visits") or 0)
    return months


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def time_of_day(dt: datetime) -> str:
    if dt.hour < 9:
        return "morgen"
    if dt.hour < 12:
        return "formiddag"
    if dt.hour < 17:
        return "ettermiddag"
    return "kveld"


def _pct_change(now: int, before: int) -> float | None:
    return round((now - before) / before * 100, 1) if before else None


def _top(counter: Counter, n: int = 5) -> list[dict[str, Any]]:
    return [{"navn": k, "antall": v} for k, v in counter.most_common(n)]


def month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def compute(
    *,
    today: date,
    workouts: list[Workout],
    monthly: dict[str, int],
    goal: int | None,
    summary: dict[str, Any],
    old_ytd: dict[int, int],
    prev_12m: int,
    no_shows: list[Booking],
    tz: tzinfo,
) -> dict[str, Any]:
    """All statistics as a flat dict; see sensor.py for what each key means.

    ``monthly`` covers all history (summary months + backfilled older months).
    ``old_ytd`` is visits from 1 Jan to the same date for years older than the summary.
    ``prev_12m`` is visits in the 12 months before the last 12 months.
    """
    tomorrow = today + timedelta(days=1)

    def count(start: date, end: date) -> int:
        return sum(1 for w in workouts if start <= w.start.date() < end)

    year_start = today.replace(month=1, day=1)
    ytd = count(year_start, tomorrow)

    # Year-to-date for every year (same day and month), for comparison.
    ytd_per_year = dict(old_ytd)
    for y in {w.start.year for w in workouts}:
        try:
            same_day = today.replace(year=y) + timedelta(days=1)
        except ValueError:  # 29 Feb
            same_day = today.replace(year=y, day=28) + timedelta(days=1)
        ytd_per_year[y] = count(date(y, 1, 1), same_day)
    last_year_ytd = ytd_per_year.get(today.year - 1, 0)

    per_year: dict[int, int] = {}
    for key, n in monthly.items():
        per_year[int(key[:4])] = per_year.get(int(key[:4]), 0) + n

    last12_start = today - timedelta(days=365)
    last12 = count(last12_start + timedelta(days=1), tomorrow)
    recent = [w for w in workouts if w.start.date() > last12_start]
    this_year = [w for w in workouts if w.start.date() >= year_start]

    # Weeks (Monday–Sunday)
    per_week = Counter(week_start(w.start.date()) for w in workouts)
    this_week = week_start(today)
    week_count = per_week.get(this_week, 0)

    def streak(threshold: int) -> int:
        # Count back from this week if already reached, else from last week.
        wk = this_week if per_week.get(this_week, 0) >= threshold else this_week - timedelta(weeks=1)
        n = 0
        while per_week.get(wk, 0) >= threshold:
            n += 1
            wk -= timedelta(weeks=1)
        return n

    def longest(threshold: int) -> int:
        if not workouts:
            return 0
        best = run = 0
        wk = week_start(workouts[0].start.date())
        while wk <= this_week:
            run = run + 1 if per_week.get(wk, 0) >= threshold else 0
            best = max(best, run)
            wk += timedelta(weeks=1)
        return best

    month_key = today.strftime("%Y-%m")
    prev_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    best_month = max(monthly.items(), key=lambda kv: kv[1], default=None)
    best_year = max(per_year.items(), key=lambda kv: kv[1], default=None)
    days_in_year = (date(today.year + 1, 1, 1) - year_start).days
    elapsed = (today - year_start).days + 1
    last = workouts[-1].start if workouts else None

    instructors: Counter = Counter()
    for y in summary.get("years") or []:
        if (dt := _parse_dt(y.get("year"))) and dt.astimezone(tz).year == today.year:
            for ip in y.get("instructorPopularity") or []:
                name = ((ip.get("instructor") or {}).get("page") or {}).get("title")
                if name:
                    instructors[name] += int(ip.get("occurences") or 0)

    recent_names = Counter(w.name for w in recent if w.name)
    year_names = Counter(w.name for w in this_year if w.name)
    weekdays = Counter(WEEKDAYS[w.start.weekday()] for w in recent)
    tod = Counter(time_of_day(w.start) for w in recent)
    centers = Counter(w.center for w in recent if w.center)
    no_show_12m = [
        b for b in no_shows if b.activity and b.activity.start.astimezone(tz).date() > last12_start
    ]

    return {
        "ytd": ytd,
        "ytd_last_year": last_year_ytd,
        "ytd_change_pct": _pct_change(ytd, last_year_ytd),
        "ytd_per_year": {str(k): v for k, v in sorted(ytd_per_year.items())},
        "forecast_year": round(ytd / elapsed * days_in_year),
        "last_12m": last12,
        "prev_12m": prev_12m,
        "last_12m_change_pct": _pct_change(last12, prev_12m),
        "per_year": {str(k): v for k, v in sorted(per_year.items())},
        "total": sum(per_year.values()),
        "this_week": week_count,
        "last_week": per_week.get(this_week - timedelta(weeks=1), 0),
        "this_month": monthly.get(month_key, count(today.replace(day=1), tomorrow)),
        "last_month": monthly.get(prev_month, 0),
        "per_month": dict(sorted(monthly.items())),
        "avg_week_12m": round(last12 / 52, 2),
        "avg_week_ytd": round(ytd / max(elapsed / 7, 1), 2),
        "goal": goal,
        "goal_progress_pct": round(week_count / goal * 100) if goal else None,
        "goal_streak": streak(goal) if goal else None,
        "goal_streak_longest": longest(goal) if goal else None,
        "active_streak": streak(1),
        "active_streak_longest": longest(1),
        "last_workout": last,
        "last_workout_name": workouts[-1].name if workouts else None,
        "days_since_last": (today - last.date()).days if last else None,
        "favorite_class_ytd": year_names.most_common(1)[0][0] if year_names else None,
        "favorite_class_ytd_top": _top(year_names),
        "favorite_class_12m": recent_names.most_common(1)[0][0] if recent_names else None,
        "favorite_class_12m_top": _top(recent_names),
        "distinct_classes_ytd": len(year_names),
        "favorite_instructor_ytd": instructors.most_common(1)[0][0] if instructors else None,
        "favorite_instructor_ytd_top": _top(instructors),
        "favorite_weekday": weekdays.most_common(1)[0][0] if weekdays else None,
        "weekdays": {d: weekdays.get(d, 0) for d in WEEKDAYS},
        "favorite_time_of_day": tod.most_common(1)[0][0] if tod else None,
        "time_of_day": {k: tod.get(k, 0) for k in ("morgen", "formiddag", "ettermiddag", "kveld")},
        "favorite_center": centers.most_common(1)[0][0] if centers else None,
        "centers": dict(centers.most_common()),
        "best_month": month_label(best_month[0]) if best_month else None,
        "best_month_count": best_month[1] if best_month else None,
        "best_year": best_year[0] if best_year else None,
        "best_year_count": best_year[1] if best_year else None,
        "no_shows_12m": len(no_show_12m),
        "no_show_fees_12m": len(no_show_12m) * NO_SHOW_FEE,
    }
