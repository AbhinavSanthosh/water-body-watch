"""LangGraph orchestration for Phase 4 forensic diagnostics.

Nodes run in order: velocity -> geometry -> NDBI -> classify -> narrate ->
localize. The deterministic nodes compute numbers; the `narrate` node only
writes the report from those numbers; `localize` translates and speaks it.

The graph is built with closures over settings and optional fake transports so
the deterministic core is testable without any live API.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.forensics.classify import classify
from app.forensics.geometry import current_water_polygon, geometry_metrics
from app.forensics.metrics import recession_metrics
from app.forensics.ndbi import synthetic_ndbi
from app.forensics.report import analyze_forensics
from app.models import Step
from app.services.bhashini import BhashiniClient, BhashiniError


class ForensicState(TypedDict, total=False):
    water_body_id: str
    name: str
    language: str
    speak: bool
    record: Any
    series: list[Any]
    baseline: Any
    max_geometry: dict[str, Any]
    target_area_m2: float
    style: str
    live_flag: dict[str, Any]
    recession: dict[str, Any]
    geometry: dict[str, Any]
    ndbi: dict[str, Any]
    diagnosis: dict[str, Any]
    report: dict[str, Any]
    localized_report: str | None
    audio_base64: str | None
    audio_mime: str | None
    index_version: str
    steps: list[dict[str, Any]]
    warnings: list[str]


def _step(steps: list[dict[str, Any]], name: str, status: str, detail: str) -> list[dict[str, Any]]:
    result = [*steps, Step(name=name, status=status, detail=detail).model_dump()]  # type: ignore[arg-type]
    return result


def build_graph(
    settings: Settings,
    *,
    report_transport: httpx.AsyncBaseTransport | None = None,
    bhashini: BhashiniClient | None = None,
):
    bhashini = bhashini or BhashiniClient(settings)

    async def velocity_node(state: ForensicState) -> dict[str, Any]:
        recession = recession_metrics(state["series"], state["baseline"])
        steps = _step(
            state.get("steps", []),
            "recession_velocity",
            "ok",
            f"Dry-season retreat {recession.velocity_m2_per_day:,.0f} m²/day "
            f"({recession.velocity_ratio:.2f}× natural).",
        )
        return {"recession": asdict(recession), "steps": steps}

    async def geometry_node(state: ForensicState) -> dict[str, Any]:
        seed = int.from_bytes(
            hashlib.sha256(f"{state['water_body_id']}:geom".encode()).digest()[:8], "big"
        )
        rng = random.Random(seed)
        current = current_water_polygon(
            state["max_geometry"], state["target_area_m2"], state["style"], rng
        )
        metrics = geometry_metrics(state["max_geometry"], current)
        steps = _step(
            state.get("steps", []),
            "retreat_geometry",
            "ok",
            f"Asymmetry index {metrics.asymmetry_index:.2f} "
            f"({metrics.shared_boundary_ratio:.0%} shared, "
            f"{metrics.front_straightness:.0%} straight).",
        )
        payload = asdict(metrics)
        payload["current_geometry"] = current
        return {"geometry": payload, "steps": steps}

    async def ndbi_node(state: ForensicState) -> dict[str, Any]:
        seed = int.from_bytes(
            hashlib.sha256(f"{state['water_body_id']}:ndbi".encode()).digest()[:8], "big"
        )
        rng = random.Random(seed)
        metrics = synthetic_ndbi(state["record"], settings, style=state["style"], rng=rng)
        steps = _step(
            state.get("steps", []),
            "buffer_ndbi",
            "ok",
            (
                f"100 m ring: {metrics.ndbi_built_pct:.0f}% built-up "
                f"(mean NDBI {metrics.ndbi_mean:+.2f})."
            ),
        )
        return {"ndbi": asdict(metrics), "steps": steps}

    async def classify_node(state: ForensicState) -> dict[str, Any]:
        from app.forensics.geometry import GeometryMetrics
        from app.forensics.metrics import Recession
        from app.forensics.ndbi import BufferMetrics

        recession = Recession(**state["recession"])
        geometry_payload = dict(state["geometry"])
        geometry_payload.pop("current_geometry", None)
        geometry = GeometryMetrics(**geometry_payload)
        ndbi = BufferMetrics(**state["ndbi"])
        diagnosis = classify(recession, geometry, ndbi, state["live_flag"], settings)
        steps = _step(
            state.get("steps", []),
            "diagnosis",
            "ok",
            f"Classified as {diagnosis.classification.replace('_', ' ')} "
            f"(confidence {diagnosis.confidence:.2f}).",
        )
        return {"diagnosis": asdict(diagnosis), "steps": steps}

    async def narrate_node(state: ForensicState) -> dict[str, Any]:
        geometry = state["geometry"]
        ndbi = state["ndbi"]
        recession = state["recession"]
        diagnosis = state["diagnosis"]
        payload = {
            "lake": state["name"],
            "classification": diagnosis["classification"],
            "confidence": diagnosis["confidence"],
            "velocity_m2_per_day": recession["velocity_m2_per_day"],
            "natural_velocity_m2_per_day": recession["natural_velocity_m2_per_day"],
            "velocity_ratio": recession["velocity_ratio"],
            "shrinkage_pct": geometry["shrinkage_pct"],
            "shared_boundary_ratio": geometry["shared_boundary_ratio"],
            "front_straightness": geometry["front_straightness"],
            "asymmetry_index": geometry["asymmetry_index"],
            "ndbi_built_pct": ndbi["ndbi_built_pct"],
            "ndbi_mean": ndbi["ndbi_mean"],
            "buffer_m": ndbi["buffer_m"],
        }
        report, source, warning = await analyze_forensics(
            payload, settings, transport=report_transport
        )
        warnings = list(state.get("warnings", []))
        if warning:
            warnings.append(warning)
        steps = _step(
            state.get("steps", []),
            "forensic_report",
            "ok" if source == "nvidia" else "warning",
            "NVIDIA NeMo wrote the forensic report."
            if source == "nvidia"
            else "Used the deterministic forensic report.",
        )
        report["source"] = source
        return {"report": report, "warnings": warnings, "steps": steps}

    async def localize_node(state: ForensicState) -> dict[str, Any]:
        report = state["report"]
        language = state["language"]
        diagnosis = state["diagnosis"]
        speak = state.get("speak", False)
        warnings = list(state.get("warnings", []))
        steps = state.get("steps", [])

        localized = report.get("report_en")
        if language != "en":
            if settings.bhashini_configured:
                try:
                    localized = await bhashini.translate(report["report_en"], "en", language)
                    steps = _step(steps, "localization", "ok", "Bhashini translated the diagnosis.")
                except BhashiniError as exc:
                    warnings.append(f"Diagnosis translation failed: {exc}")
                    steps = _step(steps, "localization", "warning", str(exc))
            else:
                warnings.append("Bhashini is not configured; the diagnosis stays in English.")

        actionable = diagnosis["classification"] in {
            "siltation_debris",
            "encroachment",
            "encroachment_suspected",
        }
        audio = None
        audio_mime = None
        if speak and actionable:
            if settings.bhashini_configured:
                spoken = f"{report['headline']}. {localized}"
                try:
                    audio, audio_mime = await bhashini.text_to_speech(spoken, language)
                    steps = _step(steps, "voice_diagnosis", "ok", "Bhashini TTS diagnosis ready.")
                except BhashiniError as exc:
                    warnings.append(f"Diagnosis audio failed: {exc}")
                    steps = _step(steps, "voice_diagnosis", "warning", str(exc))
            else:
                warnings.append("Bhashini is not configured; diagnosis audio was skipped.")

        return {
            "localized_report": localized,
            "audio_base64": audio,
            "audio_mime": audio_mime,
            "warnings": warnings,
            "steps": steps,
        }

    builder = StateGraph(ForensicState)
    builder.add_node("velocity", velocity_node)
    builder.add_node("geometry", geometry_node)
    builder.add_node("ndbi", ndbi_node)
    builder.add_node("classify", classify_node)
    builder.add_node("narrate", narrate_node)
    builder.add_node("localize", localize_node)

    builder.add_edge(START, "velocity")
    builder.add_edge("velocity", "geometry")
    builder.add_edge("geometry", "ndbi")
    builder.add_edge("ndbi", "classify")
    builder.add_edge("classify", "narrate")
    builder.add_edge("narrate", "localize")
    builder.add_edge("localize", END)

    return builder.compile()
