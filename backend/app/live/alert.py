"""Multilingual-ready anomaly alert narration for Phase 3.

The deterministic flag already decided whether the lake is anomalous. The LLM
only writes the alert; it cannot flip the flag. If NVIDIA is unavailable, the
deterministic headline and summary are used.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import Settings
from app.services.intent import _parse_json_object

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the alert writer for an Indian municipal lake-monitoring system.
You receive the live NDWI measurement, the same-month JRC seasonal baseline, the
deterministic flag, and scene-quality metadata. The flag is final: do not
re-decide it.

Return ONLY a JSON object with exactly these keys:
{"headline": "short alert headline, max 90 characters",
 "summary_en": "2-3 sentences a municipal engineer can act on",
 "recommended_action": "one concrete next step",
 "severity": "normal|watch|critical",
 "confidence": 0.0,
 "evidence": ["short factual bullet", "short factual bullet"]}

Rules:
- severity must be "critical" when flag is true, "watch" when the reading is
  within 5 percentage points of the threshold, otherwise "normal".
- Cite only numbers present in the input. Never invent dates or percentages.
- If classification is insufficient_data, say the reading is provisional.
- Mention cloud masking or scene quality only if it materially affects trust.
- No markdown, no commentary, no extra keys."""


def deterministic_alert(payload: dict[str, Any]) -> dict[str, Any]:
    lake = payload.get("lake") or "The monitored lake"
    drop = float(payload.get("drop_pct") or 0.0)
    month = payload.get("month_name") or "this month"
    classification = payload.get("classification") or "normal"
    baseline = float(payload.get("baseline_area_m2") or 0.0)
    area = float(payload.get("area_m2") or 0.0)
    threshold = float(payload.get("threshold_pct") or 15.0)
    flagged = bool(payload.get("flag"))
    cloud = float(payload.get("cloud_pct") or 0.0)
    permanent = float(payload.get("permanent_change_pct") or 0.0)

    evidence = [
        f"Live NDWI area: {area:,.0f} m².",
        f"{month} JRC baseline: {baseline:,.0f} m² ({drop:+.1f}%).",
        f"Flag threshold: {threshold:.0f}%; cloud-masked pixels: {cloud:.0f}%.",
    ]

    if classification == "insufficient_data":
        return {
            "headline": "Provisional reading: not enough history",
            "summary_en": (
                f"{lake} measures {area:,.0f} m² today, but there are too few "
                "same-month JRC observations to judge whether that is normal. "
                "Treat this as a provisional reading and re-check after the next "
                "cloud-free pass."
            ),
            "recommended_action": "Keep the lake on watch and re-run after the next clear scene.",
            "severity": "watch",
            "confidence": round(float(payload.get("confidence") or 0.3), 2),
            "evidence": evidence,
        }

    if flagged and classification == "permanent_loss":
        return {
            "headline": f"Critical alert: {lake} is losing water permanently",
            "summary_en": (
                f"{lake} is {abs(drop):.1f}% below its {month} baseline at "
                f"{area:,.0f} m² versus {baseline:,.0f} m². The multi-year median is "
                f"also down {abs(permanent):.1f}%, so this is not a normal dry-season low."
            ),
            "recommended_action": (
                "Dispatch a field inspection for encroachment, siltation, or a blocked inlet."
            ),
            "severity": "critical",
            "confidence": round(float(payload.get("confidence") or 0.7), 2),
            "evidence": evidence,
        }

    if flagged:
        return {
            "headline": f"Critical alert: {lake} has shrunk past its seasonal baseline",
            "summary_en": (
                f"{lake} is {abs(drop):.1f}% below its {month} baseline at "
                f"{area:,.0f} m² versus {baseline:,.0f} m², beyond the "
                f"{threshold:.0f}% seasonal tolerance. Immediate inspection is recommended."
            ),
            "recommended_action": (
                "Send a municipal inspection team to verify the shoreline and inflows this week."
            ),
            "severity": "critical",
            "confidence": round(float(payload.get("confidence") or 0.7), 2),
            "evidence": evidence,
        }

    near = drop > threshold - 5
    return {
        "headline": (
            f"Watch: {lake} is approaching its seasonal low"
            if near
            else f"{lake} is within its seasonal range"
        ),
        "summary_en": (
            f"{lake} measures {area:,.0f} m² against a {month} baseline of "
            f"{baseline:,.0f} m² ({-drop:+.1f}%), inside the {threshold:.0f}% "
            "seasonal tolerance. No immediate action is needed."
        ),
        "recommended_action": (
            "Continue routine monitoring and re-check after the next clear pass."
            if near
            else "No action required; keep the routine monitoring cadence."
        ),
        "severity": "watch" if near else "normal",
        "confidence": round(float(payload.get("confidence") or 0.6), 2),
        "evidence": evidence,
    }


def _clean_alert(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    def text(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else fallback[key]

    severity = str(parsed.get("severity") or fallback["severity"]).lower()
    if severity not in {"normal", "watch", "critical"}:
        severity = fallback["severity"]

    evidence = parsed.get("evidence")
    if not isinstance(evidence, list):
        evidence = fallback["evidence"]
    cleaned = [str(item).strip() for item in evidence if str(item).strip()][:6]

    try:
        confidence = float(parsed.get("confidence", fallback["confidence"]))
    except (TypeError, ValueError):
        confidence = float(fallback["confidence"])

    return {
        "headline": text("headline"),
        "summary_en": text("summary_en"),
        "recommended_action": text("recommended_action"),
        "severity": severity,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": cleaned or fallback["evidence"],
    }


async def analyze_live(
    payload: dict[str, Any],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """Return (alert, source, warning). Never fails because NVIDIA is down."""

    fallback = deterministic_alert(payload)
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
        "max_tokens": 600,
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
                body.pop("response_format", None)
                response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        parsed = _parse_json_object(content)
        return _clean_alert(parsed, fallback), "nvidia", None
    except Exception as exc:  # noqa: BLE001 - fallback is intentional
        logger.warning("NVIDIA alert narration failed, using deterministic text: %s", exc)
        return (
            fallback,
            "deterministic",
            f"NVIDIA alert narration failed; used the deterministic alert ({exc}).",
        )
