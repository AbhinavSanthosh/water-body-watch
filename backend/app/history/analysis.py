"""Turn deterministic history metrics into a written trend analysis.

The LLM is a narrator, not the trigger. It receives the already-computed
baseline, trend, and flag numbers, and must return JSON that cites only those
numbers. If NVIDIA is unavailable or returns something malformed, a
deterministic summary is used instead and the source is recorded.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import Settings
from app.history.baseline import MONTH_NAMES, Baseline, FlagResult, Trend
from app.services.intent import _parse_json_object

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a hydrological analyst for an Indian municipal lake-monitoring system.
You receive a JSON object with a lake name, a monthly JRC surface-area baseline,
trend metrics, and a deterministic flag. Write a short briefing for a municipal
engineer.

Return ONLY a JSON object with exactly these keys:
{"summary_en": "3-4 sentence plain-English briefing",
 "seasonal_pattern": "one sentence on the normal seasonal cycle",
 "drought_window": "one sentence naming the months the lake normally reaches its low",
 "anomaly": "one sentence on the current reading versus the same-month baseline",
 "confidence": 0.0,
 "evidence": ["short factual bullet", "short factual bullet"]}

Rules:
- Use ONLY numbers present in the input. Never invent percentages, dates, or areas.
- Distinguish seasonal drawdown from permanent loss. A drop inside the seasonal
  swing is normal; a drop beyond it against the same calendar month is the flag.
- If the flag classification is insufficient_data, say so plainly.
- confidence is your confidence in the *interpretation*, between 0 and 1.
- evidence must cite concrete values from the input.
- No markdown, no commentary, no extra keys."""


def _month_names(months: tuple[int, ...]) -> str:
    if not months:
        return "no clear low season"
    return ", ".join(MONTH_NAMES[month - 1] for month in months)


def metrics_payload(
    *,
    name: str,
    baseline: Baseline,
    trend: Trend,
    flag: FlagResult,
    current_area_m2: float,
    current_date: str,
    drop_threshold_pct: float,
) -> dict[str, Any]:
    return {
        "lake": name,
        "current_date": current_date,
        "current_area_m2": round(current_area_m2, 1),
        "same_month_baseline_m2": round(flag.baseline_area_m2, 1),
        "drop_pct": round(flag.drop_pct, 2),
        "flag": flag.flag,
        "classification": flag.classification,
        "flag_threshold_pct": drop_threshold_pct,
        "evaluated_month": flag.evaluated_month,
        "confidence": flag.confidence,
        "seasonal": {
            "window_years": baseline.window_years,
            "annual_median_m2": round(baseline.annual_median_m2, 1),
            "survival_threshold_m2": round(baseline.survival_threshold_m2, 1),
            "amplitude_pct": round(baseline.amplitude_pct, 2),
            "drought_months": list(baseline.drought_months),
            "peak_months": list(baseline.peak_months),
        },
        "trend": {
            "slope_m2_per_year": round(trend.slope_m2_per_year, 1),
            "slope_pct_per_year": round(trend.slope_pct_per_year, 3),
            "recent_median_m2": round(trend.recent_median_m2, 1),
            "prior_median_m2": round(trend.prior_median_m2, 1),
            "permanent_change_pct": round(trend.permanent_change_pct, 2),
            "months_observed": trend.months_observed,
            "months_missing": trend.months_missing,
        },
    }


def deterministic_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """Readable fallback briefing built straight from the metrics."""

    seasonal = payload["seasonal"]
    trend = payload["trend"]
    month_name = ""
    evaluated_month = payload.get("evaluated_month")
    if isinstance(evaluated_month, int) and 1 <= evaluated_month <= 12:
        month_name = MONTH_NAMES[evaluated_month - 1]

    drought = _month_names(tuple(seasonal.get("drought_months") or ()))
    peaks = _month_names(tuple(seasonal.get("peak_months") or ()))
    classification = payload.get("classification", "normal")
    name = payload.get("lake") or "This lake"
    drop = payload.get("drop_pct", 0.0)
    permanent = trend.get("permanent_change_pct", 0.0)

    summary = (
        f"{name} normally peaks in {peaks} and reaches its low in {drought}. "
        f"Across {seasonal.get('window_years', 10)} years the seasonal swing is about "
        f"{seasonal.get('amplitude_pct', 0.0):.0f}% of its high-water area, and the "
        f"lowest monthly median is {seasonal.get('survival_threshold_m2', 0.0):,.0f} m². "
        f"The latest reading is {payload.get('current_area_m2', 0.0):,.0f} m² against a "
        f"{month_name or 'same-month'} baseline of "
        f"{payload.get('same_month_baseline_m2', 0.0):,.0f} m² ({-drop:+.1f}%). "
        f"The multi-year trend is {trend.get('slope_pct_per_year', 0.0):+.2f}% per year."
    )

    if classification == "permanent_loss":
        anomaly = (
            f"The lake is {drop:.1f}% below its {month_name or 'seasonal'} baseline and the "
            f"recent 3-year median is {permanent:+.1f}% versus the prior period, which points "
            "to a permanent loss rather than a normal dry season."
        )
    elif classification == "seasonal_drought":
        anomaly = (
            f"The lake is {drop:.1f}% below its {month_name or 'seasonal'} baseline, but the "
            f"multi-year median is stable ({permanent:+.1f}%), so this matches the normal "
            "dry-season drawdown."
        )
    elif classification == "insufficient_data":
        anomaly = (
            "There are too few valid JRC observations to separate a seasonal low from a "
            "real loss. Treat the current reading as provisional."
        )
    else:
        anomaly = (
            f"The lake is {-drop:+.1f}% versus its {month_name or 'seasonal'} baseline, inside "
            "the normal seasonal tolerance."
        )

    return {
        "summary_en": summary,
        "seasonal_pattern": (
            f"Peak months are {peaks}; the natural low season is {drought}, with a "
            f"{seasonal.get('amplitude_pct', 0.0):.0f}% swing."
        ),
        "drought_window": (
            f"Surface area normally bottoms out in {drought} at about "
            f"{seasonal.get('survival_threshold_m2', 0.0):,.0f} m²."
        ),
        "anomaly": anomaly,
        "confidence": round(float(payload.get("confidence", 0.5)), 2),
        "evidence": [
            f"Same-month baseline: {payload.get('same_month_baseline_m2', 0.0):,.0f} m².",
            f"Current area: {payload.get('current_area_m2', 0.0):,.0f} m² ({-drop:+.1f}%).",
            f"10-year slope: {trend.get('slope_pct_per_year', 0.0):+.2f}% per year.",
            f"Recent vs prior 3-year median: {permanent:+.1f}%.",
        ],
    }


def _clean_analysis(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    def text(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else fallback[key]

    evidence = parsed.get("evidence")
    if not isinstance(evidence, list):
        evidence = fallback["evidence"]
    cleaned_evidence = [str(item).strip() for item in evidence if str(item).strip()][:6]

    try:
        confidence = float(parsed.get("confidence", fallback["confidence"]))
    except (TypeError, ValueError):
        confidence = float(fallback["confidence"])

    return {
        "summary_en": text("summary_en"),
        "seasonal_pattern": text("seasonal_pattern"),
        "drought_window": text("drought_window"),
        "anomaly": text("anomaly"),
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": cleaned_evidence or fallback["evidence"],
    }


async def analyze_history(
    payload: dict[str, Any],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """Return (analysis, source, warning). Never fails because NVIDIA is down."""

    fallback = deterministic_analysis(payload)
    if not settings.nvidia_configured or not settings.history_llm_enabled:
        return fallback, "deterministic", None

    url = f"{settings.nvidia_base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": settings.nvidia_llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.nvidia_timeout_s, transport=transport
        ) as client:
            response = await client.post(url, headers=headers, json=body)
            if response.status_code == 400:
                # Some NIM-hosted models do not implement response_format.
                body.pop("response_format", None)
                response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        parsed = _parse_json_object(content)
        return _clean_analysis(parsed, fallback), "nvidia", None
    except Exception as exc:  # noqa: BLE001 - fallback is intentional
        logger.warning("NVIDIA history analysis failed, using deterministic text: %s", exc)
        return (
            fallback,
            "deterministic",
            f"NVIDIA trend narration failed; used the deterministic summary ({exc}).",
        )
