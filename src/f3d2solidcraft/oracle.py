"""The original solid, imported once, answering what the .f3d does not record.

Fusion stores an extrude's chosen profiles and a fillet's edges as opaque identities. The only
faithful source left is the finished solid, so it is imported as a reference component and asked
two questions: how much of a candidate body lies inside it, and which edges of a body it lacks.
The component is deleted before anything is saved.

Every answer here is either a measurement or an `OracleUnavailable`. A zero that really means "not
measured" would be read as "no overlap" and would quietly pick the wrong profile, so the oracle
refuses instead of reporting a false zero.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from f3d2solidcraft.engine import CommandRefused, Engine, EngineError

# How many questions one conversion may put to the oracle. **Questions, not seconds** — this was a
# 60 s wall clock until spec section 130, and a wall clock makes the result of a conversion depend
# on what else the machine was doing: the same file, the same commit and the same engine gave 21
# `exact` idle and 17 with 16 busy loops on an 18-core Mac, because the loaded run bought fewer
# trials with its 60 s. A count that moves with the load is not a measurement, and every number
# this project compares is one.
#
# What costs a unit is **one trial**: the question it puts, or `charge` where the engine refused the
# candidate and nothing was asked (`_trial` in both mappers), or the engine half of a trial without
# a measurement (`_one_at_a_time`). A question of its own beyond the trial — the confirmation at a
# second spacing, each comparison of a pairing survey — costs one each. Free are the STEP import in
# `open`, the topology reads in `edges` and the cached readings of the original. Nothing in this
# module enforces the budget either — `shortfall` keeps answering past zero — so a caller that
# cares asks `questions_left()` before putting another question.
#
# **The number.** Measured on the corpus with the budget raised out of the way, every conversion's
# demand is: 47 questions (`Caja parametrica v2`), 39 (`corpus-08`), 36
# (`corpus-02`), 30 (`corpus-05`), 26 (`corpus-01`), 21 (`corpus-03`), 21
# (`corpus-06`) and 0 for the eight that never ask. At 60 every one of them
# finishes what it starts and the corpus reproduces its stored baselines file for file, with a
# quarter of the budget left over for a file the corpus does not have.
#
# 30 was the number the two old constants implied — 60 s at a 2 s floor — and it would have cost
# features: four files ask more than that today (spec section 131).
BUDGET_QUESTIONS = 60

# The wall clock, kept as a safety valve and never as the currency: a run that crosses this stops
# asking and says so, in its own sentence and in the report. Its job is to end a conversion whose
# engine has stopped making progress, not to pace one, so it sits far above anything the corpus
# does. Measured: the heaviest conversion of the 15 takes 92.2 s end to end, and a question costs
# between 0.30 s (`corpus-04`) and 4.05 s (`corpus-03`) — 13x apart, which is why no count of
# questions can stand for a count of seconds and the clock cannot stand for the budget. A whole
# budget of the costliest questions measured is about 240 s; 400 s is above that and 4.3x the
# heaviest file.
#
# What it cannot cover is a single question that hangs: one kernel boolean inside one trial on
# `corpus-04` takes about 46 s, and the engine timeout is what ends those.
CLOCK_CEILING_SECONDS = 400.0

# How many regions a comparison is asked to **list**, which is `inspect.compare`'s own `max_regions`
# parameter and nothing to do with `mappers.extrude.MAX_REGIONS`: that one caps how many sketch
# regions a *search tries*, this one how many difference regions one *answer reports*. Conflating
# them reads as "lower 29" where what is needed is "raise the engine's 50".
#
# Why it is raised at all: the engine's default is 50, and a side's total is the volume of the cut it
# could attribute — so when the listing truncates, **the total truncates with it, silently**.
# Measured on a comb of 60 separate overlaps against one box, where the exact answer is 120 mm³:
# at the default the answer states `only_in_b` **98.0** with `unlisted_regions: 11`, no warning at
# all, and in 0.40.0-rc.3 every listed region claiming `measured_by: "boolean"` — 18 % low and
# stamped exact. Asked for 200 it states **120.000** on rc.3 and 119.755 on 0.30.1 (sampled, but
# whole). Raising it cannot cost anything the default was already paying for: the cap only binds on
# an answer that finds more regions than it, and across the corpus's 371 comparisons (205 on 0.30.1,
# 166 on rc.3) **not one** reaches 50 — the most any answer lists is 16. So this is a latent hole
# closed, not a behaviour bought (spec section 132).
COMPARE_LISTED_REGIONS = 200

# How far apart two volume readings may be and still be the same measurement. Relative, because
# these volumes come back in the thousands of mm³: an absolute 1e-6 mm³ is far below the kernel's own
# repeatability — two readings of one unchanged body on `corpus-01` differ by 0.006 mm³ on a
# 2679 mm³ leftover — so an absolute threshold would reject a correct answer over noise. 1e-5 of the
# reading is about 0.03 mm³ there: comfortably above that noise and far below the 60.8 mm³ a wrong
# edge set really adds. The floor keeps it meaningful when the volume compared is tiny.
# The absolute floor `incoherent` allows the two statements of the shared volume to differ by, so a
# relative band does not decide on almost nothing where a side is essentially zero. Measured: it has
# to exceed **0.0411 mm³**, the largest absolute gap two readings of one side under 10 mm³ showed in
# the corpus, and stay well under **3.876 mm³**, the smallest volume any decision of this converter
# has turned on (`corpus-01` region 2's overshoot in `_added_inside`). 0.1 sits at the noise
# end of that two-orders-of-magnitude gap.
#
# It used to be shared with `_confirm`, the second sampling that kept a warned comparison when two
# spacings agreed within `COMPARE_BAND`. On solidcore a clean comparison is measured by boolean and
# exact, `_confirm` fired zero times across the corpus on 0.40.1, and it was removed with its band and
# spacing: a warned reading that is not exact is now refused outright (spec section 147).
COMPARE_FLOOR = 0.1  # mm³

# A comparison answer states each side's own volume beside the one-sided differences, so it gives
# the common volume **twice**: `a - only_in_a` and `b - only_in_b`. Those are the same solid, so they
# have to agree, and an answer where they do not is contradicting itself — whatever its warnings say.
#
# The check is free: both numbers are already in the answer, so it costs no engine call.
#
# Read against the **larger** of the two volumes, not the reference's. `inspect.compare` integrates
# along lines through the whole pair, so its error scales with the bigger solid: measured, comparing
# a 27000 mm³ box with a 1000 mm³ cube inside it closes to 142.083 mm³, which is 14 % of the cube
# and 0.53 % of the box. Scaled by the reference it would read as nonsense; scaled by the box it is
# the method's floor.
#
# Both bounds are measured. Sound readings reach **0.758 %** — the widest is `around first` against
# `cadmpeg_model` in this module's own tests, 1637.139 mm³ on a 216000 mm³ box; the 50 single-edge
# fillet trials of `corpus-01`'s `Fillet 1` stay under 0.1 %. The unsound one sits at
# **9.836 %**: 1356.980 mm³ on 13796.306, one of those same trials, and the engine had already said
# that body's faces had failed ("its surface encloses 11962.445 mm³ but the body measures
# 13796.306 mm³"). Thirteen times apart, so 2 % needs no tuning: 2.6× above the worst sound reading
# and 5× below the unsound one. Note that the body is reported `closed` in both cases, so the
# answer's own flag does not separate them — the two statements of the shared volume do.
COHERENCE_BAND = 0.02

NOISE_REL = 1e-5
NOISE_FLOOR = 1e-6  # mm³


# How long the strict reading gets to prove itself: the one call whose cost can be the whole run.
#
# Three times the slowest answer there is, which is the only honest way to set it. Measured: 1.74 s
# on `corpus-09.f3z` (494 placements), **59.90 s** on `corpus-12.f3z` (1610
# placements, 75 MB) and never on `Hypercube Evo+.f3z`, where one body of the original takes over
# 610 s on its own. At 60 s the `corpus-12` cleared it by **a tenth of a second**, which made the
# strictness of its verification a question about how loaded the machine was rather than about the
# design — the same defect as section 97, in another place (spec section 99).
#
# What a larger value costs is only the wait before degrading, on a design that cannot answer at
# all: 180 s against an import of that design's original that costs 196 s on its own, so it is not
# what dominates such a run.
STRICT_READING_SECONDS = 180.0


class PlacementsUnmeasurable(RuntimeError):
    """This engine cannot enumerate the reference's placements, so the run reads per definition."""


class OracleUnavailable(RuntimeError):
    """The oracle cannot answer. The question is unresolved, not answered with zero."""


class ReadingIncoherent(OracleUnavailable):
    """One comparison answer states two different common volumes, so it is not a reading.

    Separate from its parent because the cause is local: it is *this* candidate body the engine
    could not measure, not the oracle that has stopped answering. A search may rule out the
    candidate and go on to the next, the way it does with a set the engine refuses to build.
    """


def out_of_budget(oracle: Oracle) -> str:
    """Why no further trial should be started, for a mapper to raise `Unsupported` with.

    Two different sentences, because they are two different facts about the run: the budget is
    spent, which is a decision this module made and will make again on any machine, or the clock
    ceiling was crossed, which is a statement about *this* machine and makes the counts of the run
    its own. A reader who cannot tell them apart cannot tell a result from an accident, which is
    what spec section 130 is about.
    """
    if oracle.clock_exceeded:
        return (
            f"the oracle spent {oracle.spent:.1f} s of wall clock, over the "
            f"{CLOCK_CEILING_SECONDS:.0f} s ceiling, so it stopped asking: what this run counted "
            f"is not what the budget alone decides"
        )
    return (
        f"the oracle has {oracle.questions_left()} questions of budget left of its "
        f"{BUDGET_QUESTIONS}, and a trial costs one"
    )


def noise(scale: float) -> float:
    """Tolerance for comparing two volume readings of magnitude `scale`, in mm³.

    It lives here because it is a property of the measurement, not of either mapper: every volume
    either mapper compares comes back from this module, so both ask the same question of it.
    """
    return max(NOISE_FLOOR, NOISE_REL * abs(scale))


# How much of a reference body a candidate must cover before that overlap is read as real, as a
# share of that body. `noise()` is the wrong tolerance here and measuring showed it: it sizes the
# gap between two readings of one volume, while this asks whether a boolean intersection is zero,
# and that answer carries far more error. Measured on a 64 mm³ box 500 mm away from a three-body
# reference, overlapping nothing at all, the comparison still reported 29.952, 235.083 and
# **-644.056** mm³ of shared volume — up to 0.86% of a body, against a `noise()` of 0.75 mm³.
# The real pairings sit two orders above that: 41.4%, 69.9% and 74.2% (spec section 105).
#
# That error was SolidCraft 0.30.1's. On 0.40.1 the same comparison of nothing reports **0.0
# exactly** for all three bodies, because the boolean is exact (spec section 146) — so the reason
# this floor exists is gone on the engine the converter now runs on. It stays: it is still an order
# below the smallest real pairing, so it costs nothing, and it is what keeps a sampled engine honest.
PAIRING_SHARE = 0.05
# How much more of one reference body a candidate must cover than of any other before that body is
# the one it stands in for. Measured on the two designs this settles: `corpus-08`
# covers 74.2% of one and 10.8% of the other, a ratio of 6.9, and `corpus-06` 69.9% against
# 0.3%. What sets the floor is the case this must NOT answer: `corpus-02` covers 41.4% and
# 35.7%, a ratio of 1.16, where the two readings are a hair apart and which body the candidate
# becomes is not in the geometry — that one is settled by the file's declaration or not at all.
DOMINANT_RATIO = 3.0


def _shares(covered: dict[str, float], volumes: dict[str, float]) -> list[tuple[float, str]]:
    """How much of each reference body the candidate covers, as a share of it, largest first."""
    return sorted(((covered[r] / volumes[r], r) for r in covered if volumes[r] > 0.0), reverse=True)


def _dominant(covered: dict[str, float], volumes: dict[str, float]) -> str | None:
    """The one reference body a candidate covers decisively more of than any other, or None.

    Shares, not volumes: a candidate that happens to overlap a big body in absolute terms may still
    be a step of a small one it covers almost entirely. Every body has to have been measured —
    a reading that contradicted itself could have been the dominant one, so with any of those
    missing this answers nothing, the same rule the containment survey follows.
    """
    if len(covered) != len(volumes) or len(covered) < 2:
        return None
    shares = _shares(covered, volumes)
    if len(shares) < 2:
        return None
    (best, name), (second, _) = shares[0], shares[1]
    if best < PAIRING_SHARE:
        return None  # covers nothing of it worth reading: not a step of anything
    return name if second <= 0.0 or best / second >= DOMINANT_RATIO else None


def missing_edges(current: list[dict], reference: list[dict], tol: float = 0.01) -> list[dict]:
    """Edges of `current` whose midpoint no edge of `reference` shares (within `tol` mm)."""
    gone = []
    for edge in current:
        mid = edge["mid"]
        if not any(math.dist(mid, other["mid"]) <= tol for other in reference):
            gone.append(edge)
    return gone


def interference_incomplete(out: dict, asked: int, about: str | None = None) -> str:
    """Why these interference results cannot be read as complete, or "" when they can.

    Every body handed to `inspect.interference` is one the oracle needs an answer about, so
    anything the engine truncated, skipped or left unchecked can be hiding the very pair that was
    asked about.

    `about` narrows only the `unchecked` list, and only where the engine says which pair it is: an
    unchecked pair between two *other* bodies — two reference bodies the kernel cannot intersect —
    says nothing about the body the question was for, whose hits are the only ones read. Measured on
    0.40.1: the sample's cylinder inside its box ("SMLib's result is not a closed solid") used to
    refuse every overlap question on that design. An entry that names no pair still counts, and so
    does everything else here (`checked`, `truncated`, `skipped_pairs`), because none of those says
    which pair it was (spec section 147). Without `about` it is as conservative as it always was.

    `checked` matters most. The command never refuses: handed a name it cannot resolve it quietly
    drops it, reporting no interference, an empty `unchecked` and a `checked` one lower. A body
    consumed or renamed by an earlier feature would otherwise read as "no overlap".
    """
    checked = out.get("checked")
    if not isinstance(checked, int) or checked != asked:
        return f"the engine resolved {checked} of the {asked} bodies asked about"
    if out.get("truncated"):
        return "the engine truncated the results"
    skipped = out.get("skipped_pairs") or 0
    if skipped:
        return f"{skipped} pair(s) skipped"
    unchecked = out.get("unchecked") or []
    if about is not None:
        unchecked = [
            pair
            for pair in unchecked
            if not (isinstance(pair, dict) and "a" in pair and "b" in pair) or about in (pair["a"], pair["b"])
        ]
    if unchecked:
        return f"unchecked bodies: {unchecked}"
    return ""


def incoherent(answer: dict, reading: tuple[float, float]) -> str:
    """Why this comparison answer is not a reading, or "" when it holds together.

    `a - only_in_a` and `b - only_in_b` are both the volume the two solids share, so they have to
    agree within `COHERENCE_BAND` of the larger of the two volumes. This catches what a second
    sampling structurally cannot. Measured on `corpus-01`'s `Fillet 1`, six of its
    single-edge trials leave the body with failed faces, and the comparison then answers
    2944.740/2589.893 against the 4383.249/3157.418 of the untouched body — it reads as a trial that
    moved 1438 mm³ closer to the original while *adding* 498 mm³ of material, which no round can do.
    The engine warned on it, and the second sampling `_confirm` used to take (removed, spec section
    147) **agreed**, because the gap in the surface is the same gap at either spacing, so the
    reading was kept. Ranked on that number it wins, which is how a silently wrong `inferred` gets
    built.

    The contradiction, on the other hand, is plain: those answers put the shared volume 1356.980 mm³
    apart — 9.8 % of the body they measured — where every sound reading of the same pair closes to
    under 0.1 %.

    An answer that does not state both volumes gets the same treatment, for the same reason: nothing
    checks it against its own differences, so it is not a reading either.
    """
    volumes = []
    for side in ("a", "b"):
        stated = answer.get(side)
        value = stated.get("volume_mm3") if isinstance(stated, dict) else None
        if value is None:
            return (
                f"the answer does not state the {side!r} volume, so nothing checks it against its "
                f"own differences"
            )
        volumes.append(float(value))
    from_a, from_b = volumes[0] - reading[1], volumes[1] - reading[0]
    allowed = max(COHERENCE_BAND * max(abs(volumes[0]), abs(volumes[1])), COMPARE_FLOOR)
    if abs(from_a - from_b) <= allowed:
        return ""
    return (
        f"the answer states the shared volume twice and the two disagree ({from_a:.3f} from its 'a' "
        f"side, {from_b:.3f} from its 'b' side, {abs(from_a - from_b):.3f} mm³ apart against "
        f"{allowed:.3f} allowed), so it is not a reading"
    )


def unmeasured(answer: dict) -> str:
    """Why this comparison states no volume for one of its sides, or "" when it states both.

    **The false zero this module exists to refuse.** A side's `volume_mm3` comes back `null` when the
    engine could not make that cut, and `_compared` used to turn that into `0.0` — which reads as
    the strongest possible evidence: "the candidate covers the original completely" on the `b` side,
    "it adds nothing the original lacks" on the `a` side. That is how a wrong `exact` gets built out
    of a failed boolean.

    Measured on the corpus, 0.40.0-rc.3: **23 of its 166 comparisons** come back with a side stating
    no volume — 8 with neither side stated and 15 with one. Two of those 8 are the whole case in one
    answer: both totals `null` while the regions the engine *did* measure hold 384.845 and
    9050.767 mm³ of material that is only in the candidate. The oracle read that as (0.0, 0.0).
    `incoherent` does not catch it: with the reading at zero it compares the two bodies' own volumes,
    which agree whenever the candidate is close to its reference — exactly the case a search is in.
    On 0.30.1 it never happens in the corpus (205 comparisons, every side stated), which is why this
    was invisible until the solidcore lane.

    The engine's own words come out with it: the `not_measured` of the regions, which says which cut
    failed and why, and what the measured regions do add up to, since that is a lower bound on what
    the missing total would have been.
    """
    absent = [side for side in ("only_in_a", "only_in_b") if _volume(answer, side) is None]
    if not absent:
        return ""
    regions = answer.get("regions") or []
    why = [r.get("not_measured") for r in regions if r.get("not_measured")]
    measured = sum(r.get("volume_mm3") or 0.0 for r in regions if r.get("measured_by") == "boolean")
    reasons = f": {'; '.join(str(w).splitlines()[0] for w in why[:2])}" if why else ""
    return (
        f"the answer states no volume for {' and '.join(absent)}, so there is no reading of "
        f"{'that side' if len(absent) == 1 else 'either side'}{reasons} (the regions it did measure "
        f"hold {measured:.3f} mm³)"
    )


def truncated(answer: dict) -> str:
    """Why this comparison's totals are a partial sum, or "" when it listed every region.

    A side's total is the volume of the cut the engine attributed to the regions it listed, so a
    listing cut short cuts the total short with it — and nothing warns. See
    `COMPARE_LISTED_REGIONS` for the measurement: 98.0 mm³ stated against 120.0 exact, with the
    regions that *were* listed each claiming to be exact. Asking for more regions is what avoids it;
    this is the guard for an answer that truncates even so.
    """
    unlisted = answer.get("unlisted_regions") or 0
    if not unlisted:
        return ""
    return (
        f"the answer lists {len(answer.get('regions') or [])} regions and leaves {unlisted} out, so "
        f"the volumes it states for each side are the sum of the listed ones and nothing else"
    )


def measured_exactly(answer: dict) -> bool:
    """Whether every region of this answer was measured by boolean, which is exact.

    `measured_by: "boolean"` is the engine saying it cut and measured rather than sampled, and a
    side's total is those regions added up — measured, bit for bit, on three pairs whose exact
    answer is known: 500.000/500.000, 920.000/80.000 and 936.000/0.000, at the default spacing and
    at half of it, in 0.40.0-rc.3. So there is nothing for a second sampling to confirm: the second
    answer is the first one by construction.

    0.30.1 never says how it measured (`measured_by` is `None` in all 205 of the corpus's
    comparisons there), so on that engine this was `False` by construction. Since 0.40.1 is the floor,
    an answer that is not exact and warns is refused (spec section 147).
    """
    regions = answer.get("regions") or []
    return bool(regions) and all(r.get("measured_by") == "boolean" for r in regions)


def _volume(answer: dict, side: str) -> float | None:
    """The volume `inspect.compare` states for one side, or None where it states none."""
    stated = answer.get(side)
    value = stated.get("volume_mm3") if isinstance(stated, dict) else None
    return None if value is None else float(value)


def _compared(answer: dict, side: str) -> float:
    """The volume `inspect.compare` put on one side of a comparison, in mm³.

    Only ever called past `unmeasured`, which is what refuses the answer that states none: a zero
    invented here would be read as a perfect candidate.
    """
    return _volume(answer, side) or 0.0


def _discard(engine: Engine, component: object) -> None:
    """Best-effort delete of a component the oracle is about to stop tracking."""
    try:
        engine.execute("component.delete", {"component": component})
    except EngineError:
        pass  # Already raising about something worse; there is nothing left to try.


@dataclass
class Oracle:
    engine: Engine
    component: int
    bodies: list[str]
    feature: int | None = None  # timeline id of the import, so callers can prove it is gone
    asked: int = 0  # questions put to the oracle: the budget is counted in these
    spent: float = 0.0  # seconds those questions took, reported and never spent
    _edges: list[dict] | None = field(default=None, repr=False)
    _volumes: dict[str, float] | None = field(default=None, repr=False)
    _measured: list[dict] | None = field(default=None, repr=False)
    _closed: bool = field(default=False, repr=False)

    @classmethod
    def open(cls, engine: Engine, step: Path, strict: bool = False) -> Oracle:
        """Import the original as a reference component in this same session.

        Raises `OracleUnavailable` when the import is refused, or when it left nothing usable to
        ask about — an empty reference would answer every overlap with zero — after deleting
        whatever it did leave behind in the design. A design the oracle cannot measure still
        converts: every inference-needing feature simply falls back.

        Only a refusal becomes `OracleUnavailable`; a dead or out-of-step engine stays an
        `EngineError`, because that is not a question anyone can still fall back from.
        """
        # Listed, not measured — and the same listing the verification reads the design with, which
        # is the invariant section 90 was really about. `inspect_design` reports a body per
        # **definition** and `measure` with no body one per **placement**: on a real package of the
        # user's, `corpus-09.f3z`, 462 against 494. What broke there was not the
        # enumeration but the **mismatch** — a reference list of 462 against a design reading of 494
        # left 32 bodies unpaired and told a correct conversion it had 32 `extra`. Both sides now go
        # through `body_readings`, which measures per body and so enumerates definitions on both
        # sides; an all-bodies `measure` is no longer taken anywhere, because on a big assembly it
        # does not answer at all (spec section 98).
        before = set(engine.body_names())
        try:
            out = engine.execute("file.insert_step", {"path": str(step), "name": "f3d2solidcraft reference"})
        except CommandRefused as exc:
            raise OracleUnavailable(f"the reference import of {step} was refused: {exc.message}") from exc
        if not isinstance(out, dict):
            raise OracleUnavailable(f"file.insert_step answered {out!r}")
        component = out.get("component")
        try:
            component_id = int(component)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            if component is not None:
                _discard(engine, component)  # unusable as an id, but still something to clean up
            raise OracleUnavailable(
                f"file.insert_step returned no usable component id ({component!r})"
            ) from None
        # The difference against what was measured before the insert, so this is exact however deep
        # the import nests and whatever the design already had — where `created_bodies`, which this
        # used to trust, reported only the 462.
        reading = engine.body_names()
        mine = [name for name in reading if name not in before]
        if not mine:
            _discard(engine, component_id)
            raise OracleUnavailable(f"the reference import of {step} produced no bodies")
        # Names, and **nothing measured yet**. Opening used to measure every reference body up
        # front, on the grounds that the reference never changes so one reading answers everything.
        # It does, but the design does not always have to pay for it: measured on `Hypercube
        # Evo+.f3z`, one of that original's 74 bodies —
        # `NEMA17_Stepper_Motor+Readscrew_F01 (3)`, whose two siblings of the same definition
        # measure in seconds — does not answer in **610 s**, so opening the oracle at all cost the
        # whole conversion. Each question now measures what it needs and caches that, and a design
        # that never asks about that body never waits for it (spec section 98).
        oracle = cls(engine, component_id, mine, feature=out.get("feature"))
        # Counted **before** anything that can cost the session: a dead engine cannot answer, and
        # asking it inside the refusal below replaced the reason the caller needs with "engine is
        # not running" (spec section 98).
        placements = engine.placement_count() if strict and not before else len(mine)
        if strict and not before and placements != len(mine):
            # The reference places a body more than once, so one body per definition is no longer
            # the whole of it, and the only call that enumerates placements is the all-bodies
            # `measure`. It is taken **here**, bounded, where the design holds nothing but the
            # reference — so what it reads *is* the reference — and its result is kept rather than
            # taken again. Both sides of the verification then count placements, which is the
            # invariant of section 90; `read_placements` is what carries that to the other side.
            #
            # `not before`: in strict mode the design has to be empty, because the placements of
            # bodies that were already there cannot be told from the reference's without a second
            # all-bodies measure. The conversion opens its oracle before building anything, so this
            # holds where it matters and a caller that does not meet it simply reads per definition
            # (spec section 98).
            try:
                reading = engine.call("measure", {}, timeout=STRICT_READING_SECONDS)
            except EngineError as exc:
                # No `_discard` here: the call timed out, which closes the session, so there is no
                # engine left to delete the reference from and asking would raise "engine is not
                # running" over the reason the caller needs (spec section 98).
                raise PlacementsUnmeasurable(
                    f"the original places {placements} bodies of {len(mine)} definitions and this "
                    f"engine could not measure them ({exc})"
                ) from None
            measured = list(reading.get("bodies") or [])
            engine.read_placements = True
            oracle.bodies = [b["name"] for b in measured]
            oracle._measured = measured
        return oracle

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.close()
        except EngineError:
            if exc_type is None:
                raise
            # Never let a failed cleanup hide why the block failed.

    def close(self) -> None:
        """Delete the reference component, its bodies and its import feature.

        A no-op once it has succeeded, so a `try/finally` and a `with` block may guard the same
        oracle without the second delete being refused.
        """
        if self._closed:
            return
        self.engine.execute("component.delete", {"component": self.component})
        self._closed = True

    @property
    def clock_exceeded(self) -> bool:
        """Whether this run's questions have taken longer than the ceiling allows.

        A property rather than a flag set once: `spent` only grows, so the answer never goes back,
        and nothing has to remember to set it.
        """
        return self.spent > CLOCK_CEILING_SECONDS

    def questions_left(self) -> int:
        """How many more questions this conversion may put, which is what every gate asks.

        **Questions, not seconds.** A question costs one unit whatever the machine was doing while
        the engine answered it, so the same file converts to the same design on a loaded machine
        and on an idle one. With a wall-clock budget it did not: spec section 130 reproduced
        `corpus-08.f3z` at 21 `exact` idle and 17 with 16 busy loops on an
        18-core Mac, one feature refusing for the budget and taking three more with it.

        The clock is still read, and it can still end a run — but only past `CLOCK_CEILING_SECONDS`,
        and `out_of_budget` then says that is what happened.
        """
        if self.clock_exceeded:
            return 0
        return max(BUDGET_QUESTIONS - self.asked, 0)

    def charge(self, questions: int = 1) -> None:
        """Spend budget on engine work that put the oracle no question at all.

        Two callers, both charging the trial the oracle did not get asked about. `_trial` in either
        mapper, when the engine refused to build the candidate: the build and the undo cost the
        engine all the same, and measured on `corpus-04` a search of 38 such candidates takes
        360 s while putting 13 questions, so a budget that only counted questions would not bound
        it (spec section 131). And `_one_at_a_time` (section 120), which applies a declared point
        and undoes it, per point: the engine half of a trial without the measurement.

        Both were charged their wall clock before, by wrappers that wrote `spent` themselves, which
        is exactly what section 130 took out.
        """
        self.asked += questions

    def budget(self) -> dict:
        """What this conversion spent of the oracle, for the report to carry.

        In the report because the alternative is instrumenting the code again: section 131's
        numbers came from a patch that counted questions by hand, and the next question about the
        budget should be answerable from a run that already happened. `clock_exceeded` is the one
        field that says a run's counts are its machine's rather than the budget's.
        """
        return {
            "asked": self.asked,
            "budget": BUDGET_QUESTIONS,
            "seconds": round(self.spent, 3),
            "clock_ceiling": CLOCK_CEILING_SECONDS,
            "clock_exceeded": self.clock_exceeded,
        }

    def measured(self) -> list[dict]:
        """Every reference body as the engine measures it, read once and cached.

        The reference is immutable for the whole conversion — the replay builds beside it and never
        touches it — which is what `volumes` has relied on since it began caching a reading taken
        mid timeline. One reading therefore answers every question asked of the original, the
        verification's own view of it included: measured over the twelve corpus files, this reading
        and the one from opening the STEP again at the end agree body for body on name, volume,
        area, faces, edges, vertices, box and centre of mass, and on the heaviest file that second
        reading costs 17 s of the file's 108.
        """
        if self._closed:
            raise OracleUnavailable("the reference is closed; it can no longer be measured")
        if self._measured is None:
            wanted = set(self.bodies)
            found = [b for b in self.engine.body_readings(sorted(wanted)) if b["name"] in wanted]
            if len(found) != len(wanted):
                raise OracleUnavailable(
                    f"the engine measured {len(found)} of the {len(wanted)} reference bodies"
                )
            self._measured = found
        return self._measured

    def volumes(self) -> dict[str, float]:
        """Volume of each reference body, in mm³, measured once and cached.

        Per body rather than only in total, because the scale a comparison is read against is the
        body being compared with: on a 10-body original the total is two orders of magnitude above
        its smallest body, and `noise` of the total would call a 131 mm³ body contained on 2 % of
        its own volume.
        """
        if self._closed:
            raise OracleUnavailable("the reference is closed; its volume can no longer be measured")
        if self._volumes is None:
            # Only the reference bodies that have a volume. The engine reports one for a closed,
            # consistently oriented body and nothing for an open one, and since section 90 made the
            # reference every *placement* of the import, an open body among them is reachable:
            # measured on a 74.8 MB sheet metal design of the user's, `corpus-12.f3z`,
            # reading them all crashed five extrudes with `float() argument must be ... not
            # 'NoneType'` (spec section 93). An open body has no volume to compare against, so it is
            # absent here rather than given a zero that would read as a measurement — `volume_of`
            # is what callers ask when they need one.
            self._volumes = {
                str(b["name"]): float(b["volume_mm3"])
                for b in self.measured()
                if b.get("volume_mm3") is not None
            }
        return self._volumes

    def volume_of(self, body: str) -> float:
        """The volume of one reference body, or `OracleUnavailable` when it has none.

        Every caller uses it as the **scale** a shortfall is read against, and an open body gives no
        scale: a zero would make `noise(0)` meaningless and the comparison brittle, and the body's
        area is not a volume. So the inference refuses with a reason, which is what it does with
        every other reading it cannot take.
        """
        found = self.volumes().get(body)
        if found is None:
            raise OracleUnavailable(
                f"the original's {body} is open, so it has no volume to measure a candidate against"
            )
        return found

    def volume(self) -> float:
        """Total volume of the reference bodies, in mm³, measured once and cached.

        This is the scale a shortfall against the whole original is read against: `noise(volume())`
        is how much of it a candidate may fail to cover and still count as covering it.
        """
        return sum(self.volumes().values())

    def overlap(self, body: str) -> float:
        """Volume of `body` that lies inside the reference, in mm³.

        Raises `OracleUnavailable` when the reference is closed, when the engine refused the
        question, or when it could not check every pair: only a real measurement comes back as a
        number.
        """
        if self._closed:
            raise OracleUnavailable(f"the reference is closed; {body!r} cannot be measured against it")
        asked = [body, *self.bodies]
        started = time.monotonic()
        try:
            out = self.engine.call(
                "execute",
                {"command": "inspect.interference", "params": {"bodies": asked}},
            )
        except CommandRefused as exc:
            raise OracleUnavailable(f"inspect.interference refused for {body!r}: {exc.message}") from None
        finally:
            self.spent += time.monotonic() - started
            self.asked += 1
        if not isinstance(out, dict):
            raise OracleUnavailable(f"inspect.interference answered {out!r} for {body!r}")
        if reason := interference_incomplete(out, len(set(asked)), about=body):
            raise OracleUnavailable(f"interference for {body!r} is incomplete: {reason}")
        total = 0.0
        for hit in out.get("interferences") or []:
            if body in (hit.get("a"), hit.get("b")):
                total += float(hit.get("volume_mm3") or 0.0)
        return total

    def standing_for(self, body: str, declared: str | None = None) -> str:
        """The one reference body `body` stands in for, measured by containment.

        With a single-body original there is nothing to choose and this is that body, unmeasured:
        whether the candidate covers it is the caller's own question, and the message a search
        already has for failing to cover it says more than anything here could.

        With several, the pairing is measured. A body mid timeline contains every final body
        descended from it, because a cut only ever removes material — so containment is the
        evidence, and exactly one body has to show it. None contained means this candidate is not an
        earlier step of any of them. More than one means the question of which it stands in for is
        genuinely open, and measurement has nothing left to choose with.

        `declared` is the reference body **the file names** for this candidate, from
        `pairing.declared_reference`, and it is asked first. It is never believed on its own, but
        what confirms it is weaker than what the survey needs, and deliberately: the survey has to
        *isolate* one body out of several, so it wants containment, one direction only. A
        declaration only has to be *not contradicted*, and both directions do that, because a body
        mid timeline sits on whichever side of its finished self the rest of the timeline puts it —
        it contains it when only cuts and rounds follow, and lies inside it when a later join adds
        to it. Measured on `corpus-02`, the body `Extruir 11` builds lies inside its finished
        body, which three later joins add to, so containment alone called the declaration false and
        refused a feature the file had already answered.

        Both readings come out of the one comparison `shortfall` already makes, so this costs
        nothing beyond it — one comparison instead of one per reference body. Where the survey is
        ambiguous, the declaration is what resolves it: being contained by a body that also contains
        others is no contradiction, and which of them the candidate becomes is a fact the file
        records and no measurement can recover. A declaration contradicted on both sides is dropped,
        and the survey goes on through the rest.

        A pairing whose comparison contradicts itself is not evidence either way, and it says
        nothing about the other reference bodies — so the survey goes on past it rather than ending
        on it. Measured: on `Caja parametrica v2`'s `Extrude 1`, comparing `Body1` with `Bottom`
        states the shared volume as 5.032 from one side and **-1476.043** from the other, 1481.075
        mm³ apart. Letting that end the survey replaced this method's own verdict ("contains 0 of
        the 10 reference bodies") with one unreadable pair, and cut the ten comparisons to one —
        which is why that file ran in 3.3 s and 1.3 GB instead of 5.6 s and 2.4 GB. The outcome was
        the same `failed` there, but only by luck: had one of the nine it never reached been the
        contained one, a sound answer would have become a refusal.

        What an unreadable pairing does cost is the conclusion. "Exactly one contains it" cannot be
        established while one of them could not be measured — the unmeasured one might have been
        contained too — so a survey with any unreadable pairing is refused even when the readable
        ones leave exactly one standing. Measure or refuse; there is no third reading.

        Costs one comparison per reference body, once per search — one in all when a declaration is
        confirmed — and none at all on a single-body original.
        """
        if len(self.bodies) == 1:
            return self.bodies[0]
        volumes = self.volumes()
        order = list(self.bodies)
        if declared in self.bodies:
            order = [declared, *(r for r in order if r != declared)]
        contained: list[str] = []
        unreadable: list[str] = []
        # How much of each reference body the candidate does cover, which is the one reading left
        # when neither containment holds. Comes out of the same comparison, so it costs nothing.
        covered: dict[str, float] = {}
        for r in order:
            try:
                missing, outside = self.shortfall(body, r)
                covered[r] = volumes[r] - missing
                if r == declared and min(missing, outside) <= noise(volumes[r]):
                    return r  # not contradicted: the rest of the survey would change nothing
                if missing <= noise(volumes[r]):
                    contained.append(r)
            except ReadingIncoherent:
                unreadable.append(r)
        if len(contained) == 1 and not unreadable:
            return contained[0]
        # Nothing containment could isolate. Two readings before refusing, in this order, because
        # the first is the file's own answer and the second is ours (spec section 105).
        declared_share = covered.get(declared, 0.0) / volumes[declared] if declared in volumes else 0.0
        if declared in self.bodies and declared_share >= PAIRING_SHARE:
            # The file names this body for the candidate and the candidate really is part of it.
            # Containment one way round is what the *survey* needs, to isolate one body out of
            # several; a declaration only has to be borne out, and a timeline that both cuts and
            # joins after the candidate leaves it neither containing nor contained — measured on
            # `corpus-02`, 425 mm³ of leftover against a 22133 mm³ body, and on
            # `corpus-06`, 185 against 4232. Sharing volume is what the far-away case of
            # `test_a_declaration_neither_side_bears_out_is_dropped` fails, and it still fails it.
            return declared
        dominant = _dominant(covered, volumes)
        if dominant is not None:
            return dominant
        # Why the declaration did not settle it, so a refusal never reads as the file saying nothing.
        said = ""
        if declared in self.bodies:
            said = (
                f"; the file declares {declared}, and comparing the two contradicts itself"
                if declared in unreadable
                else f"; the file declares {declared}, and neither contains the other"
            )
        if len(contained) > 1:
            # Already open on the readable ones alone: what could not be measured changes nothing.
            raise OracleUnavailable(
                f"{body!r} contains {len(contained)} of the {len(self.bodies)} reference bodies: "
                f"which one it stands in for is not defined{said}"
            )
        if unreadable:
            raise OracleUnavailable(
                f"comparing {body!r} with {len(unreadable)} of the {len(self.bodies)} reference "
                f"bodies ({', '.join(unreadable)}) contradicts itself, so that exactly one contains "
                f"it cannot be established; {len(contained)} of the rest do{said}"
            )
        raise OracleUnavailable(
            f"{body!r} contains {len(contained)} of the {len(self.bodies)} reference bodies and "
            f"covers no one of them decisively more than the rest: which one it stands in for is "
            f"not defined{said}"
        )

    def shortfall(self, body: str, reference: str | None = None) -> tuple[float, float]:
        """(reference not covered by `body`, material `body` has that the reference lacks), in mm³.

        A feature only ever removes material from what came before it, so a body that is missing part of
        the reference cannot be an earlier step of it. Leftover is expected: later cuts take it away.

        `reference` is the reference body to compare with, which `standing_for` measures. Left out on
        a single-body original it is that body. Left out on a multi-body one the question is refused:
        there is no sound reading without the pairing, because taking the best of them — the body the
        candidate covers most completely — lets a candidate that covers body #2 and ignores body #1
        read as "nothing missing" and be accepted. Whoever passes a `reference` is saying it stays
        fixed for the whole search: re-deriving it per candidate would bring that same hole back, with
        a candidate that drops a reference body simply no longer being asked about it.

        Raises `OracleUnavailable` once the reference is closed: there is nothing left to compare with.
        """
        if self._closed:
            raise OracleUnavailable(f"the reference is closed; {body!r} cannot be compared with it")
        if reference is None:
            if len(self.bodies) > 1:
                raise OracleUnavailable(
                    f"the original has {len(self.bodies)} bodies: comparing one candidate with it is not "
                    f"defined, because which reference body {body!r} stands in for is not known"
                )
            reference = self.bodies[0]
        elif reference not in self.bodies:
            raise OracleUnavailable(f"{reference!r} is not one of the reference bodies")
        started = time.monotonic()
        try:
            answer = self.engine.call(
                "execute",
                {
                    "command": "inspect.compare",
                    "params": {
                        "a": body,
                        "b": reference,
                        "max_regions": COMPARE_LISTED_REGIONS,
                    },
                },
            )
        except CommandRefused as exc:
            raise OracleUnavailable(f"comparing {body} with the original: {exc}") from None
        finally:
            self.spent += time.monotonic() - started
            self.asked += 1
        if not isinstance(answer, dict):
            raise OracleUnavailable(f"inspect.compare answered {answer!r} for {body!r} against {reference}")
        # Before anything is read off it: these two are answers that state no number, or state one
        # built from part of what they found. Both would read as a measurement and neither is one,
        # and like the contradiction below they are facts about *this* pair — the candidate is ruled
        # out and the search goes on (spec section 132).
        if why := unmeasured(answer):
            raise ReadingIncoherent(f"comparing {body!r} with {reference}: {why}")
        if why := truncated(answer):
            raise ReadingIncoherent(f"comparing {body!r} with {reference}: {why}")
        reading = _compared(answer, "only_in_b"), _compared(answer, "only_in_a")
        # Before the warnings, because this one is not about how much of the reading is missing: an
        # answer that contradicts itself is not a reading at all, and a second sampling could not
        # tell — a body that is open is open at both spacings, so the two samplings agree.
        if why := incoherent(answer, reading):
            raise ReadingIncoherent(f"comparing {body!r} with {reference}: {why}")
        if measured_exactly(answer):
            # The engine cut and measured every region rather than sampling them, so the reading is
            # exact and a second sampling would be the same number by construction: the warnings
            # below are about lines left out of an integration this answer did not do.
            return reading
        if warnings := answer.get("warnings") or []:
            # Sampled and warned of a gap in a surface: some of the integration lines were left out,
            # and how much of the reading that eats is not stated. `_confirm` used to re-sample and
            # keep it when the two agreed; on solidcore it fired zero times across the corpus, so a
            # reading in this state is refused instead of being kept on a second sampling's word
            # (spec section 147).
            raise OracleUnavailable(f"comparing {body!r} with {reference} was sampled and warned: {warnings}")
        return reading

    def edges(self) -> list[dict]:
        """Every edge of every reference body, measured once and cached.

        Raises `OracleUnavailable` once the reference is closed: its bodies are gone, so neither a
        fresh read nor a stale cache should be passed off as the reference's topology.
        """
        if self._closed:
            raise OracleUnavailable("the reference is closed; its edges can no longer be read")
        if self._edges is None:
            found: list[dict] = []
            for name in self.bodies:
                if name in self.engine.unmeasurable:
                    raise OracleUnavailable(
                        f"the topology of {name!r} could not be read in an earlier attempt"
                    )
                topology = self.engine.topology_of(name)
                found.extend(topology.get("edges") or [])
            self._edges = found
        return self._edges
