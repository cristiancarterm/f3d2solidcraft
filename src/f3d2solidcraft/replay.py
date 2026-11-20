"""Walk the Fusion timeline: exact mapping first, fallback otherwise."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from f3d2solidcraft.engine import CommandRefused, Engine, EngineError, roll_back
from f3d2solidcraft.mappers import BY_DEFINITION, MAPPERS, Context, NoGeometry, Unsupported
from f3d2solidcraft.mappers.components import (
    ROOT,
    activate,
    activate_holder,
    activate_owner,
    create_components,
)
from f3d2solidcraft.mappers.params import add_parameters
from f3d2solidcraft.mappers.sketch import build_sketch, dimension_report
from f3d2solidcraft.model import Design, Feature

if TYPE_CHECKING:  # pragma: no cover - typing only
    from f3d2solidcraft.oracle import Oracle


@dataclass
class FeatureReport:
    ordinal: int
    name: str
    tag: str
    status: str  # exact | inferred | fallback | failed | skipped
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class SketchReport:
    """One sketch of the design that no feature names, and what became of it.

    Not a `FeatureReport`: there is no feature, so there is no ordinal, no tag and no label — and
    giving it a made-up one would put a row in the report the file does not have. `name` is what the
    engine called it once built, or the sketch record's internal id when it was not.
    """

    name: str
    status: str  # exact | failed
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReplayResult:
    features: list[FeatureReport]
    fallback_bodies: set[str]
    parameter_warnings: list[str]
    component_warnings: list[str] = field(default_factory=list)
    imported_bodies: list[str] = field(default_factory=list)  # SolidCraft names apply_fallback inserted
    sketches: list[SketchReport] = field(default_factory=list)  # the ones no feature names
    # The sketches' Fusion dimensions: how many arrived and why the rest did not (`dimension_report`).
    dimensions: dict = field(default_factory=dict)


_MAPPED = frozenset({"exact", "inferred"})


def _standing(ctx: Context) -> tuple[str, ...]:
    """Every body of the design standing in the engine right now, as Fusion body ids.

    What a feature that names no body of its own has to condemn. The extrude mapper already applies
    this rule where an operation reaches for a target it cannot name, and the reasoning is the same
    wherever a feature fails without naming one: it may have been the feature that finished any of
    these bodies, so every one of them is suspect.

    Measured on `Caja parametrica v2` and `corpus-02` once cadmpeg 0.6.7 let a body reach the
    end natively — the corpus's first two. Their later features fail without naming a body, and
    leaving the standing bodies alone kept a half-built one that the original has no counterpart
    for: the fallback imported the complete body beside it and the verification reported the native
    one as `extra`. Condemning it costs a native body we cannot prove was finished, which is the
    right way round: shipping one that is half-built says the design rebuilt when it did not.

    `body_names` holds every body the replay ever built, so it is filtered by what is standing now;
    the oracle's reference bodies are not in it and are never condemned.
    """
    # Names, not measurements: a `measure` recomputes every body's volume, area, faces and centre of mass and only names
    # are wanted here. On a 74.8 MB package of the user's, 1440 bodies, that is **52 s** against the
    # **0.0 s** `body_names` takes — and this runs per feature, which is where its 81 minutes went
    # (spec section 92). Every name asked about is one the replay built, a definition in the design,
    # so the definition listing answers it; the placements `measure` adds beyond it are an import's
    # instances, which are never the replay's.
    live = set(ctx.engine.body_names())
    return tuple(key for key, name in ctx.body_names.items() if name in live)


def _returned(value: object) -> tuple[str, list[str]]:
    """What a mapper said: `warnings`, or `("inferred", warnings)` when it inferred the feature.

    Any other shape raises. The call sits inside the mapper's own `try`, so a malformed return is
    recorded as an internal mapper error and the feature takes the usual rollback-and-fallback
    path: it must never skip the rollback nor put a status the report does not know into it.
    """
    status, warnings = value if isinstance(value, tuple) else ("exact", value)
    if status not in _MAPPED:
        raise ValueError(f"unknown status {status!r}, expected one of {sorted(_MAPPED)}")
    if not isinstance(warnings, list):
        raise TypeError(f"warnings came back as {type(warnings).__name__}, expected a list")
    return status, warnings


def _mapper_for(f: Feature) -> tuple[object | None, list[str]]:
    """The mapper for this feature, and the warning when it was found by its definition.

    The Fusion label is tried first, because it is what the registry was built on and it keeps every
    mapping this converter already had exactly as it was. The definition cadmpeg decoded is the
    fallback, and it is the one that generalizes: a label is the word the designer's own Fusion
    wrote, so there is one spelling per localization, while a definition is what the decoder made of
    the recipe and there is one per family.

    Measured on the corpus with cadmpeg 0.6.8 — **30 features** whose family we already map were
    unreachable by label alone: 19 `押し出し` typed `extrude`, 8 `Empalme` and 3 `フィレット` typed
    `fillet`. The whole timeline of `corpus-08.f3z` is in that first group (spec
    section 82).

    The route is reported on the feature's own row, so a mapping that did not come from the label is
    never silently the same as one that did.
    """
    by_label = MAPPERS.get(f.tag)
    if by_label is not None:
        return by_label, []
    by_kind = BY_DEFINITION.get(f.definition_kind)
    if by_kind is None:
        return None, []
    said = (
        f"no mapper for the Fusion label {f.tag!r}, so this feature was routed by the definition "
        f"cadmpeg decoded for it ({f.definition_kind})"
    )
    return by_kind, [said]


def _unmapped(f: Feature) -> str:
    """Why this feature was not rebuilt, saying which side the gap is on.

    `no mapper for X` read as this converter's gap in both of the cases it covered, and only one of
    them is. A feature cadmpeg typed carries a recipe we have not mapped yet; one it handed back as
    `native` carries no recipe at all, so no mapper could rebuild it however many we write — the
    tag is all that came through.

    Measured over the corpus's 108 unmapped features, the two piles are nothing alike. Sixteen are
    ours: `OffsetFaces` x10 decoded as `move_face`, `Combine` x4, `CopyPasteBodies` as
    `insert_bodies`, `Split` as `split_body`. The other ninety-two came back `native`, and most of
    those are one problem: 45 features lose their type to the *language* of their Fusion label,
    because cadmpeg 0.6.7 types a feature from a table of label spellings that carries French,
    German and Portuguese (Esquisse, Skizze, Esboço, Congé, Abrundung, Arredondamento, Schale,
    Maßstab) and neither Spanish nor Japanese. `corpus-08.f3z` loses its whole
    timeline that way (19 `押し出し`, 6 `スケッチ`, 3 `フィレット`), and `reader.locale` rescues the
    Spanish ones it can by rewriting the label in place, which only works where an English alias is
    the same UTF-16 length. `mango para lima`'s three Revolves are the same shape without the
    language: a family cadmpeg has, left undecoded here for one byte (spec section 46).

    Saying which pile a feature is in is the whole point: the first is a mapper to write, the second
    is a decoder to fix, and the report could not tell them apart.
    """
    if f.definition_kind == "native":
        return (
            f"cadmpeg typed no definition for {f.tag}: it came back opaque, so there is no recipe "
            f"for any mapper to rebuild"
        )
    return f"no mapper for {f.tag} (cadmpeg decoded it as {f.definition_kind})"


# Tags whose feature is construction geometry: a datum plane, a datum axis. It holds no solid and
# cannot change one, so when it fails there is no body to put in doubt — which is what sets it apart
# from a feature that merely records no body of its own, where `_standing` has to treat every
# standing body as a suspect.
#
# Measured on the corpus (the labels it really carries; `PlanoDeObra` is `WorkPlane` in Spanish and
# `reader.locale` cannot rewrite it, spec section 119):
#
# | tag | features | what it was condemning |
# | --- | --- | --- |
# | `WorkPlane` | 5 | `corpus-01` ord 11: the body 11 exact features had just built |
# | `PlanoDeObra` | 2 | nothing: `soporte controles` has no body standing at ord 2 |
# | `WorkAxis` | 1 | nothing: `corpus-07` has none standing at ord 6 either |
#
# `Component Insert` and `Lienzo` make no solid either, and are left as they are: the canvas already
# raises `NoGeometry`, and the insert's four features condemn nothing that was not condemned before
# them, so putting it here would change no count and claim a measurement there is none of.
CONSTRUCTION = frozenset({"WorkPlane", "PlanoDeObra", "WorkAxis"})


def _suspect(f: Feature, ctx: Context) -> tuple[str, ...]:
    """The bodies a failure of `f` puts in doubt, which is none when `f` cannot hold a solid."""
    return () if f.tag in CONSTRUCTION else _standing(ctx)


def replay(
    design: Design,
    engine: Engine,
    oracle: Oracle | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[ReplayResult, Context]:
    ctx = Context(engine, design, {}, {}, {}, {}, {}, oracle=oracle)
    parameter_warnings = add_parameters(ctx)
    component_warnings = create_components(ctx)
    fallback: set[str] = set()
    reports: list[FeatureReport] = []
    ordered = sorted(design.features, key=lambda f: f.ordinal)
    for index, f in enumerate(ordered, start=1):
        # Told before the feature runs, so a reader sees which one a long step is stuck on.
        if progress is not None:
            progress(index, len(ordered), f.name)

        def report(status: str, detail: str = "", warnings: list[str] | None = None, f=f) -> None:
            reports.append(FeatureReport(f.ordinal, f.name, f.tag, status, detail, warnings or []))

        if f.suppressed:
            report("skipped", "suppressed in Fusion")
            continue
        if fallback.intersection(f.outputs):
            fallback.update(f.outputs)
            report("fallback", "builds on a body that is already a fallback import")
            continue
        component_warnings += activate_owner(f, ctx)
        mapper, routed = _mapper_for(f)
        if mapper is None:
            unmapped = tuple(f.outputs) or _suspect(f, ctx)
            fallback.update(unmapped)
            report("fallback" if unmapped else "failed", _unmapped(f))
            continue
        start = engine.undo_depth()
        timeline = engine.timeline_ids()  # the undo stack caps at 200; this is what says what is there
        saved = [dict(m) for m in _maps(ctx)]
        # The bodies this failure condemns: the feature's own outputs, or the ones a mapper names
        # when the feature records none of its own (see `Unsupported.bodies`). Without them a
        # feature that names no body would be refused and its material simply lost: nothing would
        # be marked for the fallback to bring back from the original.
        condemned: tuple[str, ...] = tuple(f.outputs)
        try:
            status, warnings = _returned(mapper(f, ctx))
        except EngineError as exc:
            if not isinstance(exc, CommandRefused):
                raise  # the engine died or hung: nothing left to roll back into
            detail = f"refused by SolidCraft: {exc}"
        except NoGeometry as exc:
            # Nothing to rebuild and nothing wrong: it condemns no body and counts as no failure.
            report("skipped", str(exc))
            continue
        except Unsupported as exc:
            detail = f"not mapped: {exc}"
            condemned = condemned or exc.bodies
        except Exception as exc:  # noqa: BLE001  (a mapper bug on unexpected data must not abort the run)
            detail = f"internal mapper error: {type(exc).__name__}: {exc}"
        else:
            report(status, warnings=routed + warnings)
            continue
        roll_back(engine, start, timeline)
        for live, copy in zip(_maps(ctx), saved):
            live.clear()
            live.update(copy)
        condemned = condemned or _suspect(f, ctx)
        fallback.update(condemned)
        report("fallback" if condemned else "failed", detail, routed)
    sketches = _unnamed_sketches(ctx)
    # A visible body no feature produced natively (e.g. a file with bodies but no timeline) only
    # exists as B-rep: it comes back through the fallback import like any other.
    fallback.update(b.id for b in design.bodies if b.visible and b.id not in ctx.body_names)
    # The fallback import and everything measured after it belong to the design, not to whichever
    # component the last feature was authored in.
    if ctx.component_ids:
        activate(ROOT, ctx)
    result = ReplayResult(
        reports,
        fallback,
        parameter_warnings,
        component_warnings,
        sketches=sketches,
        dimensions=dimension_report(ctx),
    )
    return result, ctx


def _unnamed_sketches(ctx: Context) -> list[SketchReport]:
    """Build every sketch of the design that no feature names, after the timeline.

    A sketch reaches the result only through a `Sketch` feature, because the feature is what carries
    the name. So a sketch no feature names was built nowhere and said nothing — measured on the
    corpus, **22 of its 96 sketches**: all four of `corpus-11`, whose timeline
    cadmpeg reports as empty, and 18 of the 29 of `corpus-03`, whose timeline is direct modelling.
    Fusion shows them in its browser; the converted file had none of them, and nothing in the report
    said so. The verification cannot see it either: it compares bodies, and a sketch has no volume.

    They are buildable: each carries a resolved frame and its own curves, and **all 22** come out.
    Twenty-one did when this was written; the last holds a spline whose interior knots are not
    evenly spaced, which `_spline` refused outright — it is drawn as the chain of Bézier segments it
    is made of since spec section 77, so no sketch of the corpus is lost any more.

    Built **after** the timeline, which is what makes this safe: no feature names them, so no
    feature can read them, and nothing they add can change the geometry the verification measures.

    Reported in their own list rather than among the features, and named by the engine rather than by
    us: see `SketchReport` and `build_sketch`.
    """
    named = {f.sketch for f in ctx.design.features if f.sketch}
    warnings: list[str] = []
    reports: list[SketchReport] = []
    for sketch_id, sketch in sorted(ctx.design.sketches.items()):
        if sketch_id in named or sketch_id in ctx.sketch_names:
            continue
        handle = sketch_id.rsplit("/", 1)[-1]  # the file's own handle, without the stream path
        warnings += activate_holder([sketch_id], f"sketch {handle}", ctx)
        start = ctx.engine.undo_depth()
        timeline = ctx.engine.timeline_ids()
        try:
            built = build_sketch(sketch, None, ctx)
        except (Unsupported, CommandRefused) as exc:
            roll_back(ctx.engine, start, timeline)
            detail = f"not built: {exc}" if isinstance(exc, Unsupported) else f"refused by SolidCraft: {exc}"
            reports.append(SketchReport(handle, "failed", detail))
            continue
        reports.append(SketchReport(ctx.sketch_names[sketch_id], "exact", _NO_NAME, [*warnings, *built]))
        warnings = []
    return reports


# Why a sketch built here carries the engine's name and not the file's, on every row that has one.
_NO_NAME = (
    "no feature of the timeline names this sketch, so the file gives it no label and the engine numbered it"
)


def _maps(ctx: Context) -> list[dict]:
    """The maps a mapper may fill; replay snapshots and restores them on rollback.

    `ctx.dimensions` among them: a sketch rolled back is not in the saved design, so its dimensions
    must not be reported as arrived (`dimension_report` counts them as `sketch not built`).
    """
    return [ctx.param_names, ctx.sketch_names, ctx.curve_ids, ctx.body_names, ctx.dimensions]
