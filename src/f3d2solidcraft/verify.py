"""Measure the converted design against the original B-rep."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from f3d2solidcraft.engine import CommandRefused, Engine, EngineError
from f3d2solidcraft.fallback import same_body
from f3d2solidcraft.mappers import Context
from f3d2solidcraft.mappers.params import driver
from f3d2solidcraft.replay import ReplayResult


@dataclass
class BodyMatch:
    original: str
    converted: str
    faces_equal: bool


@dataclass
class Verification:
    matched: list[BodyMatch] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    #: Bodies the engine could not measure, on either side, left out of the pairing: nothing is
    #: claimed about them either way. Kept as two lists and not one count, because the asymmetry is
    #: the only thing a reader can still judge — the same body unmeasurable on both sides is the
    #: engine refusing one solid, while one side alone is a question this verification cannot answer
    #: (spec section 133).
    unmeasured_original: list[str] = field(default_factory=list)
    unmeasured_converted: list[str] = field(default_factory=list)
    #: Bodies that lost placements between the measurement above and the save, by name, with how
    #: many each lost. The conversion is measured before its last steps rearrange components, so
    #: `matched` describes the design *as measured*; this is what says whether the file written is
    #: still that design. A part placed N times whose bodies end up in a component placed once
    #: reads exactly the same to a measurement taken before, and the file holds N - 1 fewer copies.
    placements_lost: dict[str, int] = field(default_factory=dict)
    rebuild_error: str | None = None
    rebuild_parameter: str | None = None  # None: the rebuild check had nothing to exercise
    #: Why no comparison was made at all. Not the same as a comparison that found something
    #: wrong: here the original could not be read, so nothing is claimed about any body —
    #: every list above stays empty rather than calling the design's own bodies `extra`,
    #: which would say the original lacks them when that is exactly what is not known.
    unverified: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.unverified is None
            and not self.missing
            and not self.extra
            and not self.placements_lost
            and all(m.faces_equal for m in self.matched)
            and self.rebuild_error is None
        )


def original_bodies(engine: Engine, step: Path) -> list[dict]:
    """Open the original in the same session (no second engine process) and measure it."""
    engine.call("open", {"path": str(step)})
    return engine.body_readings()


def _measurable(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Split a reading into the bodies that can be compared and the names of those that cannot.

    The engine answers for a body it could not tessellate with an `error` and nothing else — no
    volume, no box, no face count — and `same_body` needs all three: measured on
    `corpus-09.f3z` under 0.40.0-rc.3, the body `3214j (2)` comes back as *"not supported
    yet: tessellation: face 3 (cylinder): edge 10: node 0: its pcurve is 0.46 mm off the edge"* on
    **both** sides, the original's reading and the conversion's, and `same_body` answered False
    between two readings of the same unmeasurable body. So the one body nobody could measure was
    reported as **1 `missing` and 1 `extra`** — the same name in both lists — and the file failed its
    verification for it (spec section 128 wondered about that 1 and 1; this is it, and it is not the
    split body of section 133).

    A body nobody could measure is not evidence of a mismatch. It is left out and said out loud,
    which is what this project already does for a body the engine cannot measure mid conversion.
    """
    whole = [b for b in rows if b.get("faces") is not None]
    return whole, [b["name"] for b in rows if b.get("faces") is None]


def compare(original: list[dict], converted: list[dict]) -> Verification:
    """Pair every body of the original with one of the conversion's.

    Nothing is held back for being hidden in Fusion. `cadmpeg convert` exports **every** body:
    measured, `corpus-02` has 2 bodies with 1 hidden and its STEP holds 2, `Caja parametrica v2`
    has 10 with 7 hidden and its STEP holds 10. This used to drop the converted counterpart of a
    hidden body from the pool while its original stayed in the list, which left that original
    unmatched and reported `missing`. It stayed harmless only while no native body survived.

    A body the engine could not measure is left out of the pairing on both sides rather than
    reported as a mismatch — `_measurable` has the measurement that says why.
    """
    v = Verification()
    measurable, v.unmeasured_original = _measurable(original)
    pool, v.unmeasured_converted = _measurable(converted)
    for o in measurable:
        # faces=False: a body that matches in volume/bbox but not in face count is a match with
        # faces_equal=False, not a missing+extra pair.
        match = next((c for c in pool if same_body(o, c, faces=False)), None)
        if match is None:
            v.missing.append(o["name"])
            continue
        pool.remove(match)
        v.matched.append(BodyMatch(o["name"], match["name"], o["faces"] == match["faces"]))
    v.extra = [c["name"] for c in pool]
    return v


def native_names(ctx: Context, result: ReplayResult) -> set[str]:
    """Bodies in the design now that the replay built (everything but the fallback imports)."""
    return set(ctx.engine.body_names()) - set(result.imported_bodies)


def _driving_fusion_parameter(ctx: Context, result: ReplayResult) -> str | None:
    """Parameter driving the first mapped extrude whose output is still a native body at the end.

    A distance that is an alias of another name (`d125 = LatchFrameThickness`) has no parameter of
    its own in the design, so the one exercised is the name it restates (`params.driver`). A distance
    that is a formula has no single parameter to nudge, so that extrude is passed over for the next.
    """
    native = native_names(ctx, result)
    # inferred counts with exact: the check only asks whether changing a parameter rebuilds the
    # design, and an inferred extrude whose body survives natively drives real geometry; whether
    # that geometry matches Fusion is what the volume/bbox/face verification already answers.
    mapped = {r.ordinal for r in result.features if r.status in ("exact", "inferred")}
    drivers = (
        driver(ctx, f.extrude.distance_param)
        for f in sorted(ctx.design.features, key=lambda f: f.ordinal)
        if f.extrude
        and f.ordinal in mapped
        and f.extrude.distance_param in ctx.param_names
        and any(ctx.body_names.get(b) in native for b in f.outputs)
    )
    return next((name for name in drivers if name is not None), None)


def driving_parameter(ctx: Context, result: ReplayResult) -> str | None:
    """SolidCraft name of the parameter the rebuild check exercises, or None (not exercised)."""
    fusion_name = _driving_fusion_parameter(ctx, result)
    return ctx.param_names[fusion_name] if fusion_name else None


def _geometry(engine: Engine) -> tuple[float, dict[str, list[float]]]:
    rows = engine.body_readings()
    boxes = {b["name"]: b["bbox"]["min"] + b["bbox"]["max"] for b in rows}
    return engine.total_volume(rows), boxes


def _changed(before: tuple[float, dict], after: tuple[float, dict], tol: float = 1e-6) -> bool:
    (vol_a, boxes_a), (vol_b, boxes_b) = before, after
    if abs(vol_a - vol_b) > tol or boxes_a.keys() != boxes_b.keys():
        return True
    return any(abs(x - y) > tol for name in boxes_a for x, y in zip(boxes_a[name], boxes_b[name]))


def check_rebuild(ctx: Context, result: ReplayResult) -> str | None:
    """Nudge the parameter driving a native extrude, require the geometry to move, always put it back."""
    fusion_name = _driving_fusion_parameter(ctx, result)
    if fusion_name is None:
        return None
    name = ctx.param_names[fusion_name]
    original = ctx.param_expressions.get(fusion_name)
    if original is None:
        return f"no expression for {fusion_name}"
    error: str | None = None
    out: dict = {}
    back: dict = {}
    moved = False
    try:
        before = _geometry(ctx.engine)
        out = ctx.engine.execute("parameters.change", {"name": name, "expression": f"({original}) * 1.01"})
        moved = _changed(before, _geometry(ctx.engine))
    except CommandRefused as exc:
        error = f"changing {name} was refused: {exc.message}"
    finally:
        try:
            back = ctx.engine.execute("parameters.change", {"name": name, "expression": original})
        except EngineError as exc:
            error = f"could not restore {name} to {original}: {getattr(exc, 'message', exc)}"
    if error:
        return error
    errors = list((out or {}).get("errors") or []) + list((back or {}).get("errors") or [])
    if errors:
        return f"rebuilding after changing {name}: {errors}"
    return None if moved else f"changing {name} did not change the geometry"
