"""Pure seasonal-baseline and flag math for Phase 2.

Everything here is deterministic and free of network, raster, or LLM I/O so it
can be unit-tested with synthetic arrays. The LLM only *narrates* these numbers;
it never replaces them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Literal

Classification = Literal["normal", "seasonal_drought", "permanent_loss", "insufficient_data"]

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class Observation:
    """One monthly surface-area sample. `area_m2=None` means no valid pass."""

    date: date
    area_m2: float | None

    @property
    def observed(self) -> bool:
        return self.area_m2 is not None


@dataclass(frozen=True)
class MonthlyClimatology:
    month: int
    median_area_m2: float
    min_area_m2: float
    max_area_m2: float
    samples: int


@dataclass(frozen=True)
class Baseline:
    window_years: int
    start_date: date
    end_date: date
    annual_median_m2: float
    survival_threshold_m2: float
    climatology: tuple[MonthlyClimatology, ...]
    drought_months: tuple[int, ...]
    peak_months: tuple[int, ...]
    amplitude_pct: float

    def month_median(self, month: int) -> float:
        for entry in self.climatology:
            if entry.month == month:
                return entry.median_area_m2
        return self.annual_median_m2

    def month_samples(self, month: int) -> int:
        for entry in self.climatology:
            if entry.month == month:
                return entry.samples
        return 0


@dataclass(frozen=True)
class Trend:
    slope_m2_per_year: float
    slope_pct_per_year: float
    recent_median_m2: float
    prior_median_m2: float
    permanent_change_pct: float
    months_observed: int
    months_missing: int


@dataclass(frozen=True)
class FlagResult:
    area_m2: float
    baseline_area_m2: float
    drop_pct: float
    flag: bool
    classification: Classification
    confidence: float
    evidence: tuple[str, ...]
    evaluated_month: int


def _observed(observations: Iterable[Observation]) -> list[Observation]:
    return [item for item in observations if item.area_m2 is not None]


def _window(observations: Sequence[Observation], years: int) -> list[Observation]:
    """Keep the most recent `years` worth of samples, ending at the latest date."""

    if not observations:
        return []
    end = max(item.date for item in observations)
    start = date(end.year - years + 1, 1, 1)
    return [item for item in observations if item.date >= start]


def build_baseline(
    observations: Sequence[Observation],
    *,
    years: int = 10,
    near_low_band_pct: float = 10.0,
) -> Baseline | None:
    """Monthly climatology over the trailing window.

    The survival threshold is the lowest monthly median: the level the lake
    naturally reaches at the end of a dry season. Drought months sit within
    `near_low_band_pct` of that threshold.
    """

    window = _window(_observed(observations), years)
    if not window:
        return None

    by_month: dict[int, list[float]] = {month: [] for month in range(1, 13)}
    for item in window:
        assert item.area_m2 is not None
        by_month[item.date.month].append(item.area_m2)

    climatology: list[MonthlyClimatology] = []
    for month in range(1, 13):
        values = by_month[month]
        if not values:
            continue
        climatology.append(
            MonthlyClimatology(
                month=month,
                median_area_m2=median(values),
                min_area_m2=min(values),
                max_area_m2=max(values),
                samples=len(values),
            )
        )
    if not climatology:
        return None

    all_values = [value for values in by_month.values() for value in values]
    annual_median = median(all_values)
    lowest = min(entry.median_area_m2 for entry in climatology)
    highest = max(entry.median_area_m2 for entry in climatology)
    low_cut = lowest * (1 + near_low_band_pct / 100)
    high_cut = highest * (1 - near_low_band_pct / 100)
    amplitude_pct = ((highest - lowest) / highest * 100) if highest > 0 else 0.0

    return Baseline(
        window_years=years,
        start_date=min(item.date for item in window),
        end_date=max(item.date for item in window),
        annual_median_m2=annual_median,
        survival_threshold_m2=lowest,
        climatology=tuple(climatology),
        drought_months=tuple(
            entry.month for entry in climatology if entry.median_area_m2 <= low_cut
        ),
        peak_months=tuple(entry.month for entry in climatology if entry.median_area_m2 >= high_cut),
        amplitude_pct=amplitude_pct,
    )


def _year_fraction(value: date) -> float:
    return value.year + (value.month - 0.5) / 12


def build_trend(observations: Sequence[Observation], *, recent_years: int = 3) -> Trend:
    """Least-squares slope plus a recent-vs-prior median comparison."""

    points = [(item.date, item.area_m2) for item in _observed(observations)]
    months_observed = len(points)
    months_missing = len([item for item in observations if not item.observed])
    if not points:
        return Trend(0.0, 0.0, 0.0, 0.0, 0.0, 0, months_missing)

    xs = [_year_fraction(day) for day, _ in points]
    ys = [area for _, area in points if area is not None]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator
        if denominator
        else 0.0
    )

    end = max(day for day, _ in points)
    recent_start = date(end.year - recent_years + 1, 1, 1)
    prior_start = date(end.year - (recent_years * 2) + 1, 1, 1)
    recent = [area for day, area in points if day >= recent_start]
    prior = [area for day, area in points if prior_start <= day < recent_start]

    recent_median = median(recent) if recent else 0.0
    prior_median = median(prior) if prior else 0.0
    permanent_change = (
        (recent_median - prior_median) / prior_median * 100 if prior_median > 0 else 0.0
    )
    slope_pct = (slope / mean_y * 100) if mean_y > 0 else 0.0

    return Trend(
        slope_m2_per_year=slope,
        slope_pct_per_year=slope_pct,
        recent_median_m2=recent_median,
        prior_median_m2=prior_median,
        permanent_change_pct=permanent_change,
        months_observed=months_observed,
        months_missing=months_missing,
    )


def evaluate_flag(
    observations: Sequence[Observation],
    baseline: Baseline,
    *,
    current: Observation | None = None,
    drop_threshold_pct: float = 15.0,
    min_months: int = 24,
    min_month_samples: int = 3,
) -> FlagResult:
    """Compare the latest area to the same-calendar-month baseline.

    Never compare to the annual mean: a dry-season low is normal and would
    otherwise raise a false alarm.
    """

    observed = _observed(observations)
    if current is None:
        current = observed[-1] if observed else None
    if current is None or current.area_m2 is None:
        return FlagResult(
            area_m2=0.0,
            baseline_area_m2=0.0,
            drop_pct=0.0,
            flag=False,
            classification="insufficient_data",
            confidence=0.0,
            evidence=("No valid JRC observation was available for this lake.",),
            evaluated_month=0,
        )

    month = current.date.month
    month_baseline = baseline.month_median(month)
    samples = baseline.month_samples(month)
    drop_pct = (
        (month_baseline - current.area_m2) / month_baseline * 100 if month_baseline > 0 else 0.0
    )
    observed_count = len(observed)
    enough_history = observed_count >= min_months and samples >= min_month_samples
    month_name = MONTH_NAMES[month - 1]

    evidence = [
        f"{month_name} baseline is the median of {samples} monthly JRC observations.",
        (
            f"Latest area is {current.area_m2:,.0f} m² against a {month_name} baseline of "
            f"{month_baseline:,.0f} m² ({-drop_pct:+.1f}%)."
        ),
    ]

    if not enough_history:
        evidence.append(
            f"Only {observed_count} monthly observations are available; "
            f"at least {min_months} are needed for a stable baseline."
        )
        return FlagResult(
            area_m2=current.area_m2,
            baseline_area_m2=month_baseline,
            drop_pct=drop_pct,
            flag=False,
            classification="insufficient_data",
            confidence=0.2,
            evidence=tuple(evidence),
            evaluated_month=month,
        )

    trend = build_trend(observations)
    flagged = drop_pct > drop_threshold_pct
    permanent = trend.permanent_change_pct < -(drop_threshold_pct / 2)

    if flagged and permanent:
        classification: Classification = "permanent_loss"
        evidence.append(
            "The multi-year median has also fallen "
            f"{trend.permanent_change_pct:+.1f}%, so this is not just the dry season."
        )
    elif flagged:
        classification = "seasonal_drought"
        evidence.append(
            "The multi-year median is stable "
            f"({trend.permanent_change_pct:+.1f}%), so the drop is inside the seasonal cycle."
        )
    else:
        classification = "normal"
        evidence.append(f"The drop is within the {drop_threshold_pct:.0f}% seasonal tolerance.")

    # Confidence grows with the size of the anomaly and the length of history.
    history_factor = min(1.0, observed_count / (min_months * 2))
    if month_baseline > 0:
        anomaly_factor = min(1.0, abs(drop_pct) / (drop_threshold_pct * 2))
    else:
        anomaly_factor = 0.0
    confidence = round(0.35 + 0.45 * history_factor + 0.2 * anomaly_factor, 2)
    confidence = max(0.0, min(0.95, confidence))

    return FlagResult(
        area_m2=current.area_m2,
        baseline_area_m2=month_baseline,
        drop_pct=drop_pct,
        flag=flagged,
        classification=classification,
        confidence=confidence,
        evidence=tuple(evidence),
        evaluated_month=month,
    )
