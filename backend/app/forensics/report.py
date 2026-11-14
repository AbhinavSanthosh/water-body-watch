"""Forensic report narration for Phase 4.

The diagnosis is already decided deterministically. NVIDIA NeMo writes the
report text from those numbers; the deterministic fallback is used whenever the
model is unavailable or returns something malformed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import Settings
from app.services.intent import _parse_json_object

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a forensic hydrologist for an Indian municipal lake-monitoring system.
You receive deterministic metrics: recession velocity, geometric retreat shape,
and buffer-ring NDBI, plus a final classification. The classification is final:
do not re-decide it.

Return ONLY a JSON object with exactly these keys:
{"headline": "short diagnostic headline, max 100 characters",
 "report_en": "3-4 sentence forensic report for a municipal engineer",
 "recommended_action": "one concrete next step",
 "confidence": 0.0,
 "evidence": ["short factual bullet", "short factual bullet"]}

Rules:
- Cite only numbers present in the input; never invent percentages or dates.
- Explain WHY the classification follows from the velocity, shape, and NDBI.
- If the classification is insufficient_data or inconclusive, say so plainly.
- No markdown, no commentary, no extra keys."""


def deterministic_report(payload: dict[str, Any]) -> dict[str, Any]:
    lake = payload.get("lake") or "The lake"
    classification = payload.get("classification") or "inconclusive"
    velocity = float(payload.get("velocity_m2_per_day") or 0.0)
    ratio = float(payload.get("velocity_ratio") or 0.0)
    shrinkage = float(payload.get("shrinkage_pct") or 0.0)
    shared = float(payload.get("shared_boundary_ratio") or 0.0)
    straight = float(payload.get("front_straightness") or 0.0)
    built = float(payload.get("ndbi_built_pct") or 0.0)
    confidence = round(float(payload.get("confidence") or 0.5), 2)

    evidence = [
        f"Recession velocity {velocity:,.0f} m²/day ({ratio:.2f}× natural).",
        f"Retreat shape: {shared:.0%} shared boundary, {straight:.0%} straight front "
        f"over {shrinkage:.0f}% of the lakebed.",
        f"100 m buffer ring: {built:.0f}% built-up (NDBI).",
    ]

    if classification == "siltation_debris":
        return {
            "headline": f"Siltation risk: {lake}'s basin is abnormally shallow",
            "report_en": (
                f"{lake} receded at {velocity:,.0f} m²/day, {ratio:.1f}× its natural "
                f"dry-season rate, and the retreat is even on all sides. That pattern "
                "means the basin is losing capacity to silt or dumped debris, so the "
                "monsoon surface area can look full while the water is now only a thin layer."
            ),
            "recommended_action": (
                "Order a bathymetric survey and sediment sampling of the lakebed."
            ),
            "confidence": confidence,
            "evidence": evidence,
        }

    if classification == "encroachment":
        return {
            "headline": f"Active encroachment detected on one bank of {lake}",
            "report_en": (
                f"The water boundary has a straight {straight:.0%}-linear cut on one side "
                f"while the other banks are unchanged, and the 100 m buffer ring is "
                f"{built:.0f}% built-up. A sharp asymmetric retreat plus a concrete spike "
                "corroborates unauthorized land reclamation."
            ),
            "recommended_action": (
                "Dispatch an inspection team to the affected bank and cross-check the "
                "revenue records for new construction."
            ),
            "confidence": confidence,
            "evidence": evidence,
        }

    if classification == "encroachment_suspected":
        return {
            "headline": f"Suspected encroachment on one bank of {lake}",
            "report_en": (
                f"{lake} shows a straight, one-sided retreat on {shrinkage:.0f}% of its "
                f"lakebed, but the buffer ring is still only {built:.0f}% built-up. "
                "Encroachment is the leading hypothesis, not yet confirmed by construction."
            ),
            "recommended_action": (
                "Monitor the affected bank and re-run NDBI after the next clear scene."
            ),
            "confidence": confidence,
            "evidence": evidence,
        }

    if classification == "seasonal_drought":
        return {
            "headline": f"{lake} is in its normal dry-season drawdown",
            "report_en": (
                f"{lake} retreated at {velocity:,.0f} m²/day ({ratio:.1f}× natural) in an "
                f"even, concentric pattern with only {built:.0f}% built-up in the buffer ring. "
                "This is the normal seasonal cycle, not a structural problem."
            ),
            "recommended_action": "No intervention; continue routine monitoring.",
            "confidence": confidence,
            "evidence": evidence,
        }

    if classification == "insufficient_data":
        return {
            "headline": "Forensic diagnosis pending more data",
            "report_en": (
                f"{lake} has not crossed a confirmed shrinkage flag, so no cause is claimed. "
                "Re-run the live check after the next clear Sentinel-2 pass."
            ),
            "recommended_action": "Continue monitoring and re-check after the next clear scene.",
            "confidence": confidence,
            "evidence": evidence,
        }

    return {
        "headline": f"Forensic diagnosis inconclusive for {lake}",
        "report_en": (
            f"The velocity ({ratio:.1f}× natural), retreat shape, and buffer-ring NDBI "
            f"({built:.0f}% built-up) do not point to a single cause yet. Collect another "
            "scene or widen the monitoring window."
        ),
        "recommended_action": "Collect additional scenes before dispatching a team.",
        "confidence": confidence,
        "evidence": evidence,
    }


def _clean_report(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    def text(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else fallback[key]

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
        "report_en": text("report_en"),
        "recommended_action": text("recommended_action"),
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": cleaned or fallback["evidence"],
    }


async def analyze_forensics(
    payload: dict[str, Any],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    fallback = deterministic_report(payload)
    if not settings.nvidia_configured or not settings.forensic_llm_enabled:
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
                body.pop("response_format", None)
                response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        parsed = _parse_json_object(content)
        return _clean_report(parsed, fallback), "nvidia", None
    except Exception as exc:  # noqa: BLE001 - fallback is intentional
        logger.warning("NVIDIA forensic narration failed, using deterministic text: %s", exc)
        return (
            fallback,
            "deterministic",
            f"NVIDIA forensic narration failed; used the deterministic report ({exc}).",
        )
