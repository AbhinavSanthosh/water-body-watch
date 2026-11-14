"""Deterministic forensic diagnosis rubric for Phase 4.

The LLM only narrates this result; it never overrides it. The rubric reduces
three signals to one classification:

- fast concentric retreat            -> siltation / debris dumping
- straight one-sided retreat + concrete -> encroachment (confirmed)
- straight one-sided retreat, no concrete -> encroachment (suspected)
- slow concentric retreat            -> seasonal drought
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.forensics.geometry import GeometryMetrics
from app.forensics.metrics import Recession
from app.forensics.ndbi import BufferMetrics

Classification = Literal[
    "seasonal_drought",
    "siltation_debris",
    "encroachment",
    "encroachment_suspected",
    "inconclusive",
    "insufficient_data",
]


@dataclass(frozen=True)
class Diagnosis:
    classification: Classification
    confidence: float
    evidence: tuple[str, ...]


def classify(
    recession: Recession,
    geometry: GeometryMetrics,
    ndbi: BufferMetrics,
    live_flag: dict[str, Any],
    settings: Any,
) -> Diagnosis:
    classification: Classification
    evidence: list[str] = []

    vel_ratio = recession.velocity_ratio
    evidence.append(
        f"Dry-season retreat velocity is {recession.velocity_m2_per_day:,.0f} m²/day "
        f"versus a natural {recession.natural_velocity_m2_per_day:,.0f} m²/day "
        f"({vel_ratio:.2f}×)."
    )
    evidence.append(
        f"Retreat is {geometry.shared_boundary_ratio:.0%} shared with the maximum boundary "
        f"and the cut front is {geometry.front_straightness:.0%} straight "
        f"(asymmetry index {geometry.asymmetry_index:.2f})."
    )
    evidence.append(
        f"The {ndbi.buffer_m:.0f} m buffer ring is {ndbi.ndbi_built_pct:.0f}% built-up "
        f"(mean NDBI {ndbi.ndbi_mean:+.2f})."
    )

    insufficient = live_flag.get("classification") == "insufficient_data" or not live_flag.get(
        "flag"
    )
    if insufficient:
        classification = "insufficient_data"
        evidence.append("The live NDWI flag is not confirmed, so no forensic cause is claimed.")
        return Diagnosis(classification=classification, confidence=0.2, evidence=tuple(evidence))

    vel_high = vel_ratio >= settings.forensic_velocity_ratio_threshold
    asym_high = (
        geometry.asymmetry_index >= settings.forensic_shared_ratio_threshold
        and geometry.front_straightness >= settings.forensic_straightness_threshold
    )
    ndbi_high = ndbi.ndbi_built_pct >= settings.forensic_ndbi_built_threshold
    concentric = geometry.asymmetry_index < settings.forensic_shared_ratio_threshold

    strengths: list[float] = []
    if vel_high:
        strengths.append(min(1.0, vel_ratio / (2 * settings.forensic_velocity_ratio_threshold)))
    if asym_high:
        strengths.append(min(1.0, geometry.front_straightness))
    if ndbi_high:
        strengths.append(min(1.0, ndbi.ndbi_anomaly))

    if vel_high and concentric:
        classification = "siltation_debris"
        evidence.append(
            "The retreat is fast and even on all sides, which points to an abnormally "
            "shallow basin (siltation or debris dumping) rather than a walled-off bank."
        )
    elif asym_high and ndbi_high:
        classification = "encroachment"
        evidence.append(
            "A straight one-sided cut coincides with a spike in built-up pixels, "
            "corroborating active encroachment on that bank."
        )
    elif asym_high:
        classification = "encroachment_suspected"
        evidence.append(
            "The retreat is straight and one-sided, but the buffer ring is not yet "
            "built-up enough to confirm construction."
        )
    elif concentric and not vel_high:
        classification = "seasonal_drought"
        evidence.append("The retreat is slow and concentric, matching the normal seasonal cycle.")
    else:
        classification = "inconclusive"
        evidence.append("The signals are mixed; more scene coverage is needed.")

    confidence = 0.4 + 0.15 * len(strengths)
    if strengths:
        confidence += 0.25 * (sum(strengths) / len(strengths))
    confidence = round(max(0.0, min(0.95, confidence)), 2)

    return Diagnosis(classification=classification, confidence=confidence, evidence=tuple(evidence))
