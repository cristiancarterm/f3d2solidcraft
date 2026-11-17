"""What the result step shows, from the converter's exit code and its report (spec, ③ Resultado)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Outcome:
    kind: str  # verified | differs | error
    editable: int = 0
    imported: int = 0
    total: int = 0
    features: list[dict] = field(default_factory=list)
    message: str = ""


def _mismatch(verification: dict) -> str:
    parts = []
    if verification.get("missing"):
        parts.append(f"missing: {', '.join(verification['missing'])}")
    if verification.get("extra"):
        parts.append(f"extra: {', '.join(verification['extra'])}")
    if verification.get("rebuild_error"):
        parts.append(f"rebuild: {verification['rebuild_error']}")
    if verification.get("unverified"):
        parts.append(str(verification["unverified"]))
    # `ok` is also false when a paired body has a different face count (verify.Verification.ok);
    # each `matched` entry is a serialized BodyMatch: original, converted, faces_equal.
    differ = [
        m.get("original") or m.get("converted") or "?"
        for m in verification.get("matched") or []
        if isinstance(m, dict) and m.get("faces_equal") is False
    ]
    if differ:
        parts.append(f"faces differ: {', '.join(str(name) for name in differ)}")
    return "; ".join(parts) or "the converted design does not match the original"


def outcome_from(exit_code: int, report: dict | None, stderr: str) -> Outcome:
    if exit_code not in (0, 2) or report is None:
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        message = (
            lines[-1].removeprefix("error: ") if lines else f"the converter stopped with code {exit_code}"
        )
        return Outcome("error", message=message)
    counts = report.get("counts") or {}
    features = report.get("features") or []
    return Outcome(
        "verified" if exit_code == 0 else "differs",
        editable=counts.get("exact", 0) + counts.get("inferred", 0),
        imported=counts.get("fallback", 0),
        total=len(features),
        features=features,
        message="" if exit_code == 0 else _mismatch(report.get("verification") or {}),
    )
