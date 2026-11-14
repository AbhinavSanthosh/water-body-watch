"""Deterministic dry-season recession-velocity math for Phase 4.

Recession velocity is the rate at which the water line retreats during the
driest months. A lake that shrinks far faster than its own climatological
dry-season slope is abnormally shallow (siltation or debris), even if the
monsoon surface area later looks full.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.history.baseline import Baseline, Observation

_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _days_between_months(start_month: int, end_month: int) -> int:
    """Whole days between the 15th of two calendar months (trough may wrap)."""

    if end_month < start_month:
        end_month += 12
    start = sum(_MONTH_DAYS[: start_month - 1]) + 15
    end = sum(_MONTH_DAYS[: end_month - 1]) + 15
    return max(1, end - start)


def natural_recession_velocity_m2_per_day(baseline: Baseline) -> float:
    """The lake's own normal dry-season slope, from its monthly climatology."""

    if not baseline.drought_months or not baseline.peak_months:
        return 0.0

    peak = max(baseline.month_median(month) for month in baseline.peak_months)
    trough = min(baseline.month_median(month) for month in baseline.drought_months)
    peak_month = max(baseline.peak_months, key=lambda month: baseline.month_median(month))
    trough_month = min(baseline.drought_months, key=lambda month: baseline.month_median(month))
    days = _days_between_months(peak_month, trough_month)
    return max(0.0, (peak - trough) / days)


@dataclass(frozen=True)
class Recession:
    start_date: str | None
    end_date: str | None
    months: int
    days: int
    start_area_m2: float
    end_area_m2: float
    loss_m2: float
    velocity_m2_per_day: float
    natural_velocity_m2_per_day: float
    velocity_ratio: float


def recession_metrics(series: list[Observation], baseline: Baseline) -> Recession:
    """Last consecutive dry-season retreat in the trailing 12 months."""

    observed = [item for item in series if item.observed]
    if len(observed) < 2:
        return Recession(None, None, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    end_date = max(item.date for item in observed)
    start_date = date(end_date.year - 1, end_date.month, 1)
    window = [item for item in observed if item.date >= start_date]

    natural = natural_recession_velocity_m2_per_day(baseline)

    # Collect every run of consecutive decreasing months in the window.
    runs: list[list[Observation]] = []
    current: list[Observation] = []
    for previous, item in zip(window, window[1:], strict=False):
        assert previous.area_m2 is not None and item.area_m2 is not None
        if item.area_m2 < previous.area_m2:
            if not current:
                current = [previous, item]
            else:
                current.append(item)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = []

    if len(current) >= 2:
        runs.append(current)
    if not runs:
        return Recession(
            start_date=None,
            end_date=None,
            months=0,
            days=0,
            start_area_m2=0.0,
            end_area_m2=0.0,
            loss_m2=0.0,
            velocity_m2_per_day=0.0,
            natural_velocity_m2_per_day=natural,
            velocity_ratio=0.0,
        )

    # The dry-season recession is the retreat that sheds the most water.
    run = max(runs, key=lambda items: (items[0].area_m2 or 0.0) - (items[-1].area_m2 or 0.0))

    first = run[0]
    last = run[-1]
    assert first.area_m2 is not None and last.area_m2 is not None
    loss = first.area_m2 - last.area_m2
    days = max(1, (last.date - first.date).days)
    velocity = loss / days
    ratio = (velocity / natural) if natural > 0 else 0.0

    return Recession(
        start_date=first.date.isoformat(),
        end_date=last.date.isoformat(),
        months=len(run) - 1,
        days=days,
        start_area_m2=first.area_m2,
        end_area_m2=last.area_m2,
        loss_m2=loss,
        velocity_m2_per_day=velocity,
        natural_velocity_m2_per_day=natural,
        velocity_ratio=ratio,
    )
