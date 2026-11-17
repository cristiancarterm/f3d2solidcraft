"""Fusion parameters as SolidCraft user parameters."""

from __future__ import annotations

import re

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.model import Parameter

_MODEL_NAME = re.compile(r"^d\d+$")
_WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Two readings of one name are the same reading when they agree to this, in mm. They come from the
# same resolved value in the same file, so anything above it is a disagreement, not rounding.
_AGREEMENT = 1e-6


def solidcraft_name(name: str) -> str:
    """Fusion's automatic d1, d2... would collide with SolidCraft's own; prefix them."""
    return f"fusion_{name}" if _MODEL_NAME.match(name) else name


def _rewrite(expression: str, names: dict[str, str]) -> str:
    return _WORD.sub(lambda m: names.get(m.group(0), m.group(0)), expression)


def recover_user_parameters(parameters: list[Parameter]) -> tuple[list[Parameter], list[str]]:
    """The named parameters the file leans on but never defines, read off what it resolved.

    A parametric Fusion design is driven from the top by the user's own parameters
    (`CaseExteriorWidth`, `Tolerance`), and **that table does not reach this converter**: measured
    on the corpus, neither cadmpeg's dump nor ezf3d's read path carries it, and the names turn up
    only inside the expressions of the model parameters that use them. Without them every
    expression naming one is refused — 48 of `Caja parametrica v2`'s 90 — so the design arrives
    with no dimension driving anything.

    What does reach us is each model parameter's **resolved value**. A model parameter whose whole
    expression is one of those names therefore states that name's value: `d7 = CaseExteriorWidth`
    at 150 mm says `CaseExteriorWidth` is 150 mm. That is read off the file, not inferred from
    geometry, and it is taken only where the file agrees with itself — a name whose readings differ,
    or whose readings carry no length (an angle resolves to none), is refused and reported instead
    of guessed. Measured: 12 of the 14 named in `Caja parametrica v2`, 5 in `corpus-03`, 1 in
    `mango para lima`, and nothing to recover in the other nine files.

    Expressions name units too (`mm`, `deg`), and those are never recovered: a unit is not the whole
    expression of any parameter, so it has no witness. The same rule keeps function names out.
    """
    defined = {p.name for p in parameters}
    witnesses: dict[str, list[Parameter]] = {}
    for p in parameters:
        name = (p.expression or "").strip()
        if _IDENTIFIER.match(name) and name not in defined:
            witnesses.setdefault(name, []).append(p)
    recovered: list[Parameter] = []
    warnings: list[str] = []
    for name, seen in sorted(witnesses.items()):
        values = [p.value_mm for p in seen]
        if any(value is None for value in values):
            warnings.append(
                f"user parameter {name} not recovered: the file resolves no length for it, so its "
                f"value is not in the file ({len(seen)} parameter(s) name it)"
            )
            continue
        lengths = [float(value) for value in values if value is not None]
        if max(lengths) - min(lengths) > _AGREEMENT:
            warnings.append(
                f"user parameter {name} not recovered: the file resolves it to "
                f"{', '.join(f'{v:g}' for v in sorted(set(lengths)))} mm in different places"
            )
            continue
        recovered.append(
            Parameter(
                name=name,
                expression=f"{lengths[0]:.10g} mm",
                role="recovered",  # not a role the file gives: the file does not carry this parameter
                unit="mm",
                value_mm=lengths[0],
            )
        )
    return recovered, warnings


def _disagrees(p: Parameter, created: object) -> str | None:
    """Whether SolidCraft computed something other than the value the file resolved.

    The check a recovery has to pass: a recovered value that is wrong makes every expression built
    on it wrong too, and `parameters.add` answers with the value it computed, so the two can be
    compared without asking anything further. Only lengths are compared — the file resolves no
    value for an angle, so there is nothing to compare it with.
    """
    if p.value_mm is None or not isinstance(created, dict) or created.get("unit") != "mm":
        return None
    try:
        got = float(created["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(got - p.value_mm) <= max(_AGREEMENT, 1e-9 * abs(p.value_mm)):
        return None
    return (
        f"parameter {p.name} came out {got:.10g} mm where the file resolved {p.value_mm:.10g}: "
        "the expression or a value recovered for a name it uses is wrong"
    )


def add_parameters(ctx: Context) -> list[str]:
    recovered, warnings = recover_user_parameters(ctx.design.parameters)
    # The recovered ones are plain lengths, so they go first and the loop below needs no pass to
    # discover that the rest depend on them.
    parameters = recovered + list(ctx.design.parameters)
    names = {p.name: solidcraft_name(p.name) for p in parameters}
    pending = list(parameters)
    errors: dict[str, str] = {}
    while pending:
        progressed = False
        for p in list(pending):
            expression = _rewrite(p.expression, names)
            try:
                created = ctx.engine.execute(
                    "parameters.add", {"name": names[p.name], "expression": expression}
                )
            except CommandRefused as exc:
                errors[p.name] = exc.message
                continue
            if (wrong := _disagrees(p, created)) is not None:
                warnings.append(wrong)
            ctx.param_names[p.name] = names[p.name]
            ctx.param_expressions[p.name] = expression
            pending.remove(p)
            errors.pop(p.name, None)
            progressed = True
        if not progressed:
            break
    return warnings + [f"parameter {name} not created: {msg}" for name, msg in errors.items()]
