"""Fusion parameters as SolidCraft user parameters."""

from __future__ import annotations

import math
import re

from f3d2solidcraft.engine import CommandRefused
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.model import Feature, Parameter

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


# Fusion's own names for a model parameter, and for the copy a second occurrence of a component
# gets (`d22_1`). Neither is a name the designer chose, so neither is worth showing in place of one.
_MODEL_OR_COPY = re.compile(r"^d\d+(_\d+)?$")
_NUMBER = re.compile(r"^\s*[-+]?[0-9.]+(e[-+]?\d+)?\s*$")
# A number with its unit (`5 mm`): a minus in front negates the whole of it, so it needs no parentheses.
_QUANTITY = re.compile(r"^\s*[0-9.]+(e[-+]?\d+)?\s*([A-Za-z]+)?\s*$")


def _closing(expression: str, opening: int) -> int | None:
    """Index of the parenthesis that closes the one at `opening`, or None if it never closes."""
    depth = 0
    for i in range(opening, len(expression)):
        depth += {"(": 1, ")": -1}.get(expression[i], 0)
        if depth == 0:
            return i
    return None


def _bare(expression: str) -> bool:
    """Whether the expression is one name or one number, which needs no parentheses to embed."""
    return bool(_IDENTIFIER.match(expression) or _NUMBER.match(expression))


def embedded(expression: str) -> str:
    """The expression ready to stand for one name inside another expression."""
    expression = expression.strip()
    # A signed number gets parentheses too: `a - -5` is not something to hand an expression parser.
    if _bare(expression) and expression[:1] not in "+-":
        return expression
    if expression.startswith("(") and _closing(expression, 0) == len(expression) - 1:
        return expression
    return f"({expression})"


def negated(expression: str) -> str:
    """The opposite of an expression, written the way a person would: `-W`, `W` for `-W`, `-(W / 2)`.

    A reversed feature carries its distance or angle as a sign. Put in front of a formula a bare
    minus would negate only its first term, so anything that is not one name gets parentheses.
    """
    expression = expression.strip()
    if expression.startswith("-"):
        rest = expression[1:].strip()
        if _IDENTIFIER.match(rest) or _QUANTITY.match(rest):
            return rest
        if rest.startswith("(") and _closing(rest, 0) == len(rest) - 1:
            inner = rest[1:-1].strip()
            return inner if _bare(inner) else rest
    if _IDENTIFIER.match(expression) or _QUANTITY.match(expression):
        return f"-{expression}"
    return f"-{embedded(expression)}"


def _inlined(parameters: list[Parameter]) -> set[str]:
    """The model parameters that only restate other names: an alias, or a formula over the user's.

    A model parameter (`d7`, `d22_1`) is one of these when its whole expression is one parameter's
    name (`d7 = CaseExteriorWidth`, and `d2 = d1` too: its users are handed `d1` directly), or when
    its expression names one of the user's parameters, or another model parameter that does. A model
    parameter whose expression is a plain number, or a formula only over plain model parameters, is
    not: it has no name of the designer's to show, so it keeps a `fusion_dN` of its own.
    """
    by_name = {p.name: p for p in parameters}
    model = {name for name in by_name if _MODEL_OR_COPY.match(name)}
    aliases = {name for name in model if (by_name[name].expression or "").strip() in by_name.keys() - {name}}
    # Only what reaches one of the user's names spreads: a formula over an alias of a plain model
    # parameter is still a formula over plain model parameters, created as one.
    reaching: set[str] = set()
    grew = True
    while grew:
        grew = False
        for name in model - reaching:
            words = set(_WORD.findall(by_name[name].expression or "")) & by_name.keys()
            if words - model or words & reaching:
                reaching.add(name)
                grew = True
    return reaching | aliases


def _evaluated(ctx: Context, expression: str) -> dict:
    """What the engine computes for an expression, shaped as `parameters.add` answers, or refused.

    `expr.evaluate` adds nothing to the design: it is how a formula is valued without leaving a
    parameter behind. It answers `ok: false` rather than refusing, so that is turned into a refusal.
    """
    out = ctx.engine.execute("expr.evaluate", {"expression": expression})
    if not isinstance(out, dict) or not out.get("ok"):
        raise CommandRefused("expr.evaluate", str((out or {}).get("error") if isinstance(out, dict) else out))
    if out.get("kind") == "angle":
        # `value` is the angle in radians; `display_value` is only how the engine would show it.
        value = out.get("value")
        return {"value": math.degrees(value) if isinstance(value, int | float) else None, "unit": "deg"}
    if out.get("kind") == "length":
        return {"value": out.get("value"), "unit": "mm"}
    return {"value": out.get("value"), "unit": ""}


def add_parameters(ctx: Context) -> list[str]:
    """Create the file's parameters, and say for each what a feature names when it follows it.

    The user's own parameters and the plain model ones are created (`CaseExteriorWidth`,
    `fusion_d10`) and named by that name. A model parameter that only restates the user's names is
    not created: what a feature or a dimension is handed for it is the name itself (`d7 =
    CaseExteriorWidth`) or the formula, written with the real names (`d34 = HingeDiameter / 2`), so
    the design reads the way its designer wrote it. Its value is still the engine's, from
    `expr.evaluate`, and checked against the file's like any other.
    """
    recovered, warnings = recover_user_parameters(ctx.design.parameters)
    # The recovered ones are plain lengths, so they go first and the loop below needs no pass to
    # discover that the rest depend on them.
    parameters = recovered + list(ctx.design.parameters)
    inlined = _inlined(parameters)
    names = {p.name: solidcraft_name(p.name) for p in parameters}
    pending = list(parameters)
    errors: dict[str, str] = {}
    while pending:
        progressed = False
        for p in list(pending):
            # A parameter naming an inlined one still pending is refused (`fusion_dN` is not in the
            # design) and comes round again once that one has its expression.
            expression = _rewrite(p.expression, names)
            try:
                if p.name in inlined:
                    created = _evaluated(ctx, expression)
                else:
                    created = ctx.engine.execute(
                        "parameters.add", {"name": names[p.name], "expression": expression}
                    )
            except CommandRefused as exc:
                errors[p.name] = exc.message
                continue
            if (wrong := _disagrees(p, created)) is not None:
                warnings.append(wrong)
            if p.name in inlined:
                names[p.name] = embedded(expression)
                ctx.param_names[p.name] = expression.strip()
                ctx.param_inlined.add(p.name)
            else:
                ctx.param_names[p.name] = names[p.name]
            ctx.param_expressions[p.name] = expression
            if isinstance(created, dict) and isinstance(created.get("value"), int | float):
                ctx.param_values[p.name] = (float(created["value"]), str(created.get("unit")))
            pending.remove(p)
            errors.pop(p.name, None)
            progressed = True
        if not progressed:
            break
    return warnings + [f"parameter {name} not created: {msg}" for name, msg in errors.items()]


def driver(ctx: Context, fusion_name: str) -> str | None:
    """The Fusion name of the parameter in the design that drives this one exactly, or None.

    Itself, if it was created. An alias (`d125 = LatchFrameThickness`, `d2 = d1`) has no parameter
    of its own, so it is the name it restates, followed through a chain of aliases. A formula has
    none: nudging one of its names need not move it (`max(A, B)` with B the larger, a name at 0 mm,
    terms that cancel), so a check that relies on the nudge moving the geometry must look elsewhere.
    """
    seen: set[str] = set()
    name = fusion_name
    while name in ctx.param_names and name not in seen:
        seen.add(name)
        if name not in ctx.param_inlined:
            return name
        expression = next((p.expression for p in ctx.design.parameters if p.name == name), None)
        name = (expression or "").strip()
        if not _IDENTIFIER.match(name):
            return None
    return None


def follows(ctx: Context, feature: Feature, role: str, wanted: float, unit: str) -> str | None:
    """An expression of this feature's own parameter that comes to `wanted`, or None to keep the number.

    Taken only where the file is unambiguous: exactly one of the parameters the feature owns fills
    `role`, it has a name or a formula in the design, and the value the engine computed for it is `wanted` in
    `unit` — or its opposite, which a reversed feature carries as a sign (`-fusion_d30`). Anything
    else keeps the number the file resolved, so a feature never follows a parameter it does not own.
    """
    owned = [p for p in ctx.design.parameters if p.name in feature.parameters and p.role == role]
    if len(owned) != 1:
        return None
    name = ctx.param_names.get(owned[0].name)
    computed = ctx.param_values.get(owned[0].name)
    if name is None or computed is None or computed[1] != unit:
        return None
    tolerance = max(_AGREEMENT, 1e-9 * abs(wanted))
    if abs(computed[0] - wanted) <= tolerance:
        return name
    if abs(computed[0] + wanted) <= tolerance:
        return negated(name)
    return None
