"""The conversion report: one row per feature plus final metrics."""

from __future__ import annotations

import dataclasses
from collections import Counter

from f3d2solidcraft.model import Design
from f3d2solidcraft.replay import ReplayResult
from f3d2solidcraft.verify import Verification


def _component_tree(design: Design) -> list[dict]:
    """Fusion's component tree as the report states it: parents before children, root first."""
    rows = []
    for occ in design.occurrences:
        component = design.components.get(occ.component or "")
        if component is None:
            continue
        parent = next((o for o in design.occurrences if o.id == occ.parent), None)
        parent_component = design.components.get(parent.component or "") if parent else None
        rows.append(
            {
                "name": component.name,
                "parent": parent_component.name if parent_component else None,
                "root": occ.parent is None,
                "bodies": len(component.bodies),
                "sketches": len(component.sketches),
                "identity": occ.identity,
            }
        )
    return rows


def build_report(
    design: Design,
    result: ReplayResult,
    fallback_warnings: list[str],
    verification: Verification,
    budget: dict | None = None,
) -> dict:
    counts = Counter(r.status for r in result.features)
    # matched + extra is every body of the converted design
    converted = [m.converted for m in verification.matched] + verification.extra
    imported = sum(1 for name in converted if name in set(result.imported_bodies))
    return {
        "source": design.source,
        "counts": {s: counts.get(s, 0) for s in ("exact", "inferred", "fallback", "failed", "skipped")},
        "features": [dataclasses.asdict(r) for r in result.features],
        "bodies": {"native": len(converted) - imported, "imported": imported},
        # The sketches no feature names, built after the timeline; they have no feature row to go in.
        "sketches": [dataclasses.asdict(s) for s in result.sketches],
        "components": _component_tree(design),
        "warnings": (
            design.warnings + result.parameter_warnings + result.component_warnings + fallback_warnings
        ),
        "verification": {**dataclasses.asdict(verification), "ok": verification.ok},
        # What the conversion spent of the oracle, and whether the wall-clock valve fired. None
        # when there was no original to ask (spec section 131).
        "oracle": budget,
    }


def summary(report: dict) -> str:
    c = report["counts"]
    v = report["verification"]
    lines = [
        f"{report['source']}",
        (
            f"  features: {c['exact']} exact, {c['inferred']} inferred, {c['fallback']} fallback, "
            f"{c['failed']} failed, {c['skipped']} skipped"
        ),
        f"  bodies: {len(v['matched'])} matched, {len(v['missing'])} missing, {len(v['extra'])} extra"
        + (
            f", {len(v['unmeasured_original'])}/{len(v['unmeasured_converted'])} not measured"
            if v.get("unmeasured_original") or v.get("unmeasured_converted")
            else ""
        ),
        f"  bodies: {report['bodies']['native']} native, {report['bodies']['imported']} imported",
        f"  verified: {'yes' if v['ok'] else 'NO'}",
    ]
    lost = v.get("placements_lost") or {}
    if lost:
        lines.append(
            f"  placements lost before saving: {sum(lost.values())} of {len(lost)} body(ies) "
            f"({', '.join(sorted(lost))})"
        )
    if v.get("unverified"):
        lines.append(f"  not verified: {v['unverified']}")
    loose = report.get("sketches") or []
    if loose:
        built = sum(1 for s in loose if s["status"] == "exact")
        lines.append(
            f"  sketches no feature names: {built} built, {len(loose) - built} not "
            "(the engine numbered them; the file gives them no label)"
        )
    named = [c for c in report.get("components", []) if c["name"]]
    if named:
        lines.append(f"  components: {len(named)} ({', '.join(c['name'] for c in named)})")
    if v["rebuild_error"]:
        lines.append(f"  rebuild: {v['rebuild_error']}")
    elif v["rebuild_parameter"] is None:
        lines.append("  rebuild: not exercised")
    else:
        lines.append(f"  rebuild: {v['rebuild_parameter']} ok")
    lines += [f"  ! {w}" for w in report["warnings"]]
    return "\n".join(lines)
