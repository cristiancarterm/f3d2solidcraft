"""Fillet and Chamfer: the size comes from the file, and the edges from the file where it says.

Fusion records which edges a round or a bevel used as identities of a kernel history state. Those
identities **used** to carry no geometry, and this module was built around that: what the original
solid does say is which edges are no longer there, an edge the finished part lacks being an edge
something replaced, and those are the candidates to search among.

Since IR 10 the premise is gone. `feature_input_topologies` carries an `edge_geometry` for a state —
interior point, tangent, start and end — and only for the edges the feature really used: in `corpus-01`, `Fillet 1` names 8 edges, its state holds 101, and the 8 are the ones with geometry.
Where the file gives a point for every edge of the group, `_declared` builds the feature from it and
nothing below runs: no oracle, no subsets, no budget, and `exact` rather than `inferred`. The search
is what answers for the rest — a native edge group, or a `historical_partial` one, which is what the
corpus's `corpus-02` has (spec section 107).

Measured on the real corpus: the criterion is exact where it can be read. On `corpus-01`
the body reaching `Fillet 1` has 12 edges, 8 of them gone from the finished part, and 8 is exactly
the number the file names. No whole design reaches either mapper yet, because an earlier feature on
the same body cannot be rebuilt natively and the replay condemns the body from there on; and on the
truncated chain that does reach it, the body is not Fusion's body, so two of those 8 edges are
tangential seams Fusion never rounded and SolidCraft refuses the set. A two-distance chamfer is
refused here on purpose: its second value is an angle in radians, and passing it as a distance
would silently build the wrong bevel.

Which subset to use is decided on two sides, because one was not enough: a trial is only admitted
when it adds no material the original lacks, and only then ranked by how little of the original it
leaves uncovered. Ranked on that one number alone, `corpus-01`'s `Chamfer 1` came back
`inferred` having *added* 60.8 mm³ to the body — a silent wrong answer, and worse than a refusal.
Nothing is exempt from that test, not even a count that matches the file exactly: that set is
applied, measured and undone like any other candidate before it is kept.
"""

from __future__ import annotations

import math
from itertools import combinations

from f3d2solidcraft.engine import CommandRefused, EngineError, rolled_back
from f3d2solidcraft.mappers import Context, Unsupported, definition, register
from f3d2solidcraft.mappers.components import placed_point
from f3d2solidcraft.mappers.params import follows
from f3d2solidcraft.model import Feature
from f3d2solidcraft.oracle import (
    OracleUnavailable,
    ReadingIncoherent,
    missing_edges,
    noise,
    out_of_budget,
)
from f3d2solidcraft.pairing import declared_reference

# Edge subsets tried when the file does not say how many edges were used.
#
# **Measured at 96 over the whole corpus, with the oracle's budget at 300 s to match (spec section
# 110): +2 features for 2.16x the corpus's time.** Both are `corpus-02`'s, which goes 12/4/12
# to 12/6/10 and 9.8 s to 71.0 s, and the eight features still behind them are behind a two-distance
# chamfer whose second value the file does not settle — not behind this number. What decides it is
# `corpus-04`: **6.8 s to 251.1 s, 36.7x, and not one feature gained**, which is 244 of the 313
# seconds the raise costs. What it is paying for is the kernel: one boolean inside a single trial
# there takes about 46 s, which is also why `CLOCK_CEILING_SECONDS` cannot cover a single question,
# and a higher cap simply buys more of them.
MAX_SUBSETS = 32


def _body_for(feature: Feature, ctx: Context) -> str:
    """The body this feature works on: the one its outputs name, or the orphan before it.

    A feature that declares no output at all is not rare, and it used to end the timeline: measured
    on `Caja parametrica v2`, the extrudes at ordinals 2 and 8 declare none, so nothing maps their
    body to a Fusion id and the fillets right after them were refused for "its body was not rebuilt
    natively" — with the body standing in the engine the whole time. The one body the feature before
    built and the file had no id for is what it meant; more than one and there is nothing to choose
    with, so it is refused as before (spec section 107).

    Nothing here is taken on trust: the caller then asks the engine for edges at the points the file
    gives, and a body that is not this feature's has no edge at those points.
    """
    for output in feature.outputs:
        name = ctx.body_names.get(output)
        if name is not None:
            return name
    if len(ctx.orphan_bodies) == 1:
        return ctx.orphan_bodies[0]
    raise Unsupported("its body was not rebuilt natively")


def _apply(ctx: Context, feature: Feature, body: str, points: list) -> None:
    op = feature.edge_op
    params = {"edges": list(points), "body": body}
    if op.kind == "fillet":
        radius = follows(ctx, feature, "Radius", op.value, "mm") or f"{op.value} mm"
        ctx.engine.execute("solid.fillet", {**params, "radius": radius})
        return
    if op.angle_rad is not None:
        # angle_rad is an angle in radians, not a second distance: not mapped in M1.
        raise Unsupported("two-distance chamfer")
    distance = follows(ctx, feature, "Distance", op.value, "mm") or f"{op.value} mm"
    ctx.engine.execute("solid.chamfer", {**params, "distance": distance})


def _edge_candidates(ctx: Context, body: str, oracle) -> list[dict]:
    if body in ctx.engine.unmeasurable:
        # Known not to answer, from an earlier attempt of this same conversion: the feature is
        # refused so its bodies come back from the original, instead of spending the engine on a
        # call already proved unanswerable (spec section 98).
        raise Unsupported(f"the topology of {body!r} could not be read in an earlier attempt")
    current = ctx.engine.topology_of(body).get("edges") or []
    return missing_edges(current, oracle.edges())


def _trial(
    ctx: Context, feature: Feature, body: str, subset: list[dict], reference: str
) -> tuple[float, float]:
    """Apply this edge subset, measure the body against the original, and undo it.

    Both numbers `shortfall` gives come back — (original left uncovered, material the original
    lacks) — because the criterion needs both sides. A `CommandRefused` reaches the caller, which
    decides whether the engine refusing this set only rules out one candidate or ends the feature.

    Measured with `shortfall`, not `overlap`: SolidCraft 0.16.2's `inspect.interference` fails
    inside the kernel when one side is the imported original — "boolean Intersect: the operation
    failed: no result" on `corpus-01` and on `corpus-04`, which is to say on the designs
    that have the fillets and chamfers this search exists for — while `inspect.compare`, what
    `shortfall` asks, answers on that very same pair.

    **One unit of budget per trial**, charged by `shortfall` when the oracle answers and by
    `charge` here when the engine refused the edge set and the oracle was asked nothing. It used to
    be charged the trial's whole wall clock by a wrapper here, which is what made the result of a
    conversion depend on how loaded the machine was (spec section 130).
    """
    depth = ctx.engine.undo_depth()
    asked = ctx.oracle.asked
    try:
        with rolled_back(ctx.engine, depth):
            _apply(ctx, feature, body, [e["mid"] for e in subset])
            return ctx.oracle.shortfall(body, reference)
    finally:
        if ctx.oracle.asked == asked:
            ctx.oracle.charge()


def _search(
    ctx: Context,
    feature: Feature,
    body: str,
    subsets: list[tuple],
    before: tuple[float, float],
    reference: str,
) -> tuple[float, tuple, int]:
    """The edge subset to use: of those that add nothing, the one leaving least of the original bare.

    Returns the winner's uncovered volume, its edges, and **how many admitted subsets are tied with
    it** within `noise`. More than one means the measurement did not choose: the first of them is
    taken, and the caller says so in the warning instead of presenting it as a reading.

    Two-sided, because one side was not enough. Ranking by uncovered volume alone cannot say no:
    taking material away can only leave more of the original uncovered, never less, so every subset
    whose material lies outside the original ties at the body's own shortfall and **a concave edge
    whose fill lands inside the original beats all of them**. Measured on `corpus-01`, that
    is what ranking alone picked for `Chamfer 1`: a bevel that *added* 60.8 mm³ to the body and was
    reported `inferred` — a silent wrong answer, worse than a refusal.

    So leftover decides admission and uncovered decides the ranking: a round or a bevel that puts
    material where the original has none cannot be the one Fusion applied, whatever it scores.

    Where two admitted sets leave the same volume uncovered, the one that takes away **more** of the
    material the original lacks is the closer of the two — on both sides of the reading, by the same
    two numbers — so that breaks the tie instead of taking whichever came first. Measured on
    `corpus-01`'s `Fillet 1`, three unions tie at 3157.418 mm³ uncovered and they remove
    315.466, 11.815 and 9.158 mm³ of leftover: order alone would have taken any of them.

    And a subset whose reading is impossible is thrown out before either test, because the ranking
    cannot defend itself against one. Taking material away cannot cover *more* of the original than
    the body covered before, so a trial whose uncovered volume falls did not round the body: it
    broke it. Those win the ranking outright — measured on `corpus-01`'s `Fillet 1`, six
    single-edge trials read 222.721 mm³ *less* uncovered while adding nothing at all, against the
    0.049 mm³ more that the real answer reads. §30's coherence test cannot see them, because each of
    those answers is self-consistent; it is the physics that is not.

    A subset whose body the engine will not build, and one whose comparison contradicts itself, are
    both simply not the answer: they rule themselves out and the search goes on. Neither says
    anything about the oracle, which is why `ReadingIncoherent` is caught here while its parent
    `OracleUnavailable` still ends the feature.
    """
    uncovered_before, leftover_before = before
    admitted: list[tuple[float, tuple]] = []
    added: list[float] = []
    unreadable = broken = 0
    for subset in subsets:
        if ctx.oracle.questions_left() < 1:
            raise Unsupported(out_of_budget(ctx.oracle))
        try:
            uncovered, leftover = _trial(ctx, feature, body, list(subset), reference)
        except CommandRefused:
            continue  # the engine would not build this set: it is not the answer
        except ReadingIncoherent:
            # The body this set built cannot be measured — its comparison contradicts itself. That
            # rules out this candidate, not the oracle: the next set builds a different body.
            unreadable += 1
            continue
        if uncovered < uncovered_before - noise(uncovered_before):
            broken += 1
            continue
        if leftover - leftover_before > noise(leftover_before):
            added.append(leftover - leftover_before)
            continue
        admitted.append((uncovered, leftover, subset))
    if admitted:
        # Least uncovered first, then most leftover taken away; order is the order tried, so `min`
        # keeps the first of a tie on both.
        best = min(admitted, key=lambda row: (row[0], row[1]))
        tied = sum(
            1
            for uncovered, leftover, _ in admitted
            if uncovered - best[0] <= noise(best[0]) and leftover - best[1] <= noise(best[1])
        )
        return best[0], best[2], tied
    aside = "".join(
        part
        for part, n in (
            (f"; {unreadable} more could not be measured", unreadable),
            (f"; {broken} more read as impossible for a round", broken),
        )
        if n
    )
    if not added:
        if unreadable or broken:
            raise Unsupported(
                f"none of the {len(subsets)} edge subsets gives a reading a round could "
                f"produce{aside}, and SolidCraft refused the rest"
            )
        raise Unsupported("SolidCraft refused every edge subset")
    raise Unsupported(
        f"every one of the {len(subsets)} edge subsets adds material the original lacks "
        f"(the least is {min(added):.3f} mm³){aside}"
    )


def _unmeasurable(exc: OracleUnavailable) -> Unsupported:
    return Unsupported(f"the original solid could not be measured: {exc}")


def _one_at_a_time(feature: Feature, ctx: Context, body: str) -> str:
    """Which of the declared points this body can take at all, asked of the engine one at a time.

    The refusal of the whole set names **one** point and says what is wrong with it in the engine's
    own words, and that sentence is read as if it were the diagnosis. It is not, and on `corpus-01` it sent spec section 119's own note the wrong way: `solid.fillet` answered
    "[7.448, -7.821, 11.5] is where 2 edges meet: give a point along one edge, such as its midpoint",
    which reads as a pick that landed on a vertex — something a better pick would fix. Measured
    against the body the replay actually holds there, **no edge of it comes within 2.890 mm of that
    point**: the two edges the engine means meet at the *nearest* place on the body, 2.890 mm away,
    and the sentence describes where it looked rather than where it was sent.

    So the question is put to the engine instead of to the message: each point on its own, applied
    and undone like any other trial. Measured on `corpus-01`'s `Fillet 1`, it takes **4 of
    the 8** — the four at z 2.25, which land on a declared edge's own midpoint to within 1e-11 mm —
    and refuses the four at z 11.5. Half the set on this body and half on another is not a pick to
    improve: it is the chain before this feature having built something else, with every feature of
    that chain but one reporting `exact` (spec section 120).

    Counted, not listed: eight indexes in a refusal nobody can act on is noise. What the reader
    needs is whether the body is the wrong one — some points placeable, some not — or whether the
    engine simply will not round this set, which is what every point placing on its own and the set
    still failing means.

    And this runs on the way out of a refusal, so it may not turn one into an exception: an engine
    failure that is not a refusal ends the questioning and is reported as the gap it leaves.
    """
    points = list(feature.edge_op.edge_points)
    placed = 0
    for point in points:
        depth = ctx.engine.undo_depth()
        try:
            try:
                with rolled_back(ctx.engine, depth):
                    _apply(ctx, feature, body, [list(placed_point(ctx, point))])
            except CommandRefused:
                continue
            except EngineError as exc:
                return f"; the engine could not be asked point by point: {exc}"
            placed += 1
        finally:
            # One unit per point, charged explicitly: this asks the engine and not the oracle, so
            # nothing would charge it otherwise, and it is the engine half of a trial — time the
            # inference below no longer has. A wall clock charged it before (spec section 130).
            if ctx.oracle is not None:
                ctx.oracle.charge()
    if placed == len(points):
        return (
            f"; the engine places each of the {len(points)} points the file names on its own and "
            "refuses them together"
        )
    return (
        f"; of the {len(points)} points the file names the engine places {placed} on this body and "
        f"refuses {len(points) - placed}, so this is not the body Fusion rounded"
    )


def _declared(feature: Feature, ctx: Context) -> tuple[list[str] | None, str | None]:
    """Build this round or bevel on the edges the file names: (warnings, why the engine refused).

    This is the whole point of the module's first paragraph being out of date. The file does carry
    geometry for the edges a fillet used — a point inside each one — since IR 10, and where it gives
    one for every edge of the group there is nothing to search for and nothing to measure: the size
    is declared, the edges are declared, and `solid.fillet` takes exactly a point on each edge. No
    oracle, no subsets, no budget. `exact`, by the same rule as an extrude of a whole sketch.

    A refusal from the engine is the answer, not an error: it means no edge of this body lies at
    those points, so either the body is not the one the file meant or what we built differs from
    Fusion's. Either way the search below is still entitled to its own try, so this returns None and
    says why (spec section 107). Which of the two it is, the engine is asked rather than guessed at —
    `_one_at_a_time` has the measurement that says why the refusal alone cannot be read.
    """
    op = feature.edge_op
    if not op.edge_points or op.angle_rad is not None:
        return None, None
    body = _body_for(feature, ctx)
    depth = ctx.engine.undo_depth()
    try:
        _apply(ctx, feature, body, [list(placed_point(ctx, point)) for point in op.edge_points])
    except CommandRefused as exc:
        with rolled_back(ctx.engine, depth):
            pass
        return None, f"{exc}{_one_at_a_time(feature, ctx, body)}"
    return [f"{len(op.edge_points)} edges read from the file, with a point inside each one"], None


def _map_edge_op(feature: Feature, ctx: Context) -> tuple[str, list[str]] | list[str]:
    """The edges the file names, and failing that the ones measuring the original points to.

    When the file did name the edges and the engine would not take them, the refusal that finally
    comes out has to carry both halves: on its own, the inference's reason reads as if the file had
    said nothing. Section 107 wrote that half into the refusals it could see and **the ones it could
    not see went on dropping it** — the refusal for a spent budget, the ones for a reference that
    cannot be measured, and everything `_search` raises from inside. Measured on `corpus-02`,
    whose `Empalme 16` reported only "the oracle has 1.8 s of budget left" while the thing worth
    knowing was that the point the file gives for its one edge had been refused (spec section 110).

    So the half is attached here, around the whole inference, instead of at each refusal: that is
    what makes it hold for a refusal added later, which is how it was lost in the first place.
    """
    op = feature.edge_op
    if op is None:
        raise Unsupported("the file does not give a single constant size for this feature")
    read, refused = _declared(feature, ctx)
    if read is not None:
        return read
    if not refused:
        return _inferred(feature, ctx)
    because = f"; the edges the file names were refused: {refused}"
    try:
        return _inferred(feature, ctx)
    except Unsupported as exc:
        raise Unsupported(f"{exc}{because}", bodies=exc.bodies) from None


def _inferred(feature: Feature, ctx: Context) -> tuple[str, list[str]] | list[str]:
    """Work the edges out by measuring the original, for a feature whose file did not give them."""
    op = feature.edge_op
    oracle = ctx.oracle
    if oracle is None:
        raise Unsupported("no original solid to infer the edges from")
    body = _body_for(feature, ctx)
    try:
        candidates = _edge_candidates(ctx, body, oracle)
    except OracleUnavailable as exc:
        # A reference that cannot be read says nothing about which edges are gone: fall back.
        raise _unmeasurable(exc) from None
    if not candidates:
        raise Unsupported("no edge of this body is missing from the original")
    if oracle.questions_left() < 1:
        raise Unsupported(out_of_budget(oracle))
    try:
        # The reference body this body stands for, measured once: every trial below is read
        # against that same one, so a trial cannot quietly change what it is being compared with.
        reference = oracle.standing_for(body, declared_reference(ctx.design, feature, oracle.bodies))
        before = oracle.shortfall(body, reference)
    except OracleUnavailable as exc:
        raise _unmeasurable(exc) from None
    if op.edge_count is not None and len(candidates) == op.edge_count:
        # As many edges missing as the file names, which is the strongest evidence there is here —
        # but still only a count. The set is applied, measured and undone like any other candidate,
        # and kept only once its reading is one a round could produce: it adds nothing the original
        # lacks, and it does not cover more of the original than the body covered before, which
        # taking material away cannot do (`_search` has the measurement that says why). A set that
        # fails either falls through to the search below instead of being believed. A refusal is the
        # engine's own answer about this very set and ends the feature in its own words.
        try:
            uncovered, leftover = _trial(ctx, feature, body, candidates, reference)
        except OracleUnavailable as exc:
            raise _unmeasurable(exc) from None
        impossible = uncovered < before[0] - noise(before[0])
        if not impossible and leftover - before[1] <= noise(before[1]):
            _apply(ctx, feature, body, [e["mid"] for e in candidates])
            verified = (
                f"{len(candidates)} edges inferred from the original solid, verified to add nothing "
                "it lacks and to leave no more of it covered than before"
            )
            return "inferred", [verified]
    # A native edge group carries no count, so there is nothing to search for but an assumption, and
    # the one made here is k = 1. Spec §6 promised subsets of any size; widening k would blow the
    # subset cap on the first real case (one edge of 8 is 8 subsets, two is 28, three is 56 against a
    # limit of 32), so the assumption stays and the warning says it out loud instead.
    wanted = op.edge_count or 1
    assumed = (
        [f"native edge group: assumed a single edge of the {len(candidates)} missing"]
        if op.edge_count is None
        else []
    )
    if wanted > len(candidates):
        raise Unsupported(f"the file names {wanted} edges, the original is missing {len(candidates)}")
    # Counted before the subsets are built, not after. `list(combinations(...))` materialises every
    # subset first, so the cap below could only ever reject a list that had already been paid for:
    # measured on `corpus-01` under cadmpeg 0.6.7, which hands this mapper more candidate
    # edges than 0.6.3 does, the process grew to 13.3 GB and was killed by the OS before reaching
    # the check. `math.comb` is the same number for nothing.
    how_many = math.comb(len(candidates), wanted)
    if how_many > MAX_SUBSETS:
        # §57 built the way past this and measured it: screen each edge on its own, group the
        # ones that read alike, and try unions of whole groups. It answers — `corpus-01` goes from 16 exact / 1 inferred / 5 fallback to 17 / 4 / 1 — and it is parked
        # there rather than here, because it costs that file 19 s -> 373 s and the design it
        # saves is identical: one chamfer still falls back and brings its body back imported.
        raise Unsupported(f"{how_many} edge subsets to try (limit {MAX_SUBSETS})")
    subsets = list(combinations(candidates, wanted))
    try:
        uncovered, chosen_edges, tied = _search(ctx, feature, body, subsets, before, reference)
    except OracleUnavailable as exc:
        raise _unmeasurable(exc) from None
    _apply(ctx, feature, body, [e["mid"] for e in chosen_edges])
    chosen = (
        f"{len(chosen_edges)} of {len(candidates)} missing edges: the subset that adds nothing the "
        f"original lacks and leaves least of it uncovered ({uncovered:.3f} mm³)"
    )
    warnings = [*assumed, chosen]
    if tied > 1:
        # The measurement admitted this set but did not single it out: say so rather than let the
        # line above read as a reading that chose.
        warnings.append(
            f"ambiguous: {tied} subsets leave the same {uncovered:.3f} mm³ uncovered, took the first"
        )
    return "inferred", warnings


@register("Fillet")
@definition("fillet")
def map_fillet(feature: Feature, ctx: Context) -> tuple[str, list[str]] | list[str]:
    return _map_edge_op(feature, ctx)


@register("Chamfer")
@definition("chamfer")
def map_chamfer(feature: Feature, ctx: Context) -> tuple[str, list[str]] | list[str]:
    return _map_edge_op(feature, ctx)
