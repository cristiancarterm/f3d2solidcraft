import time

import pytest

from f3d2solidcraft import oracle as oracle_module
from f3d2solidcraft.engine import CommandRefused, Engine
from f3d2solidcraft.fallback import original_step
from f3d2solidcraft.oracle import (
    BUDGET_QUESTIONS,
    COMPARE_LISTED_REGIONS,
    PAIRING_SHARE,
    Oracle,
    OracleUnavailable,
    incoherent,
    interference_incomplete,
    measured_exactly,
    missing_edges,
    out_of_budget,
    truncated,
    unmeasured,
)
from f3d2solidcraft.verify import original_bodies

REFERENCE_NAME = "f3d2solidcraft reference"


@pytest.fixture(scope="session")
def cube_step(tools, tmp_path_factory):
    """A 10 mm cube as STEP, exported by the engine itself.

    Real geometry from the real engine, not a hand-written fixture: it is the only way to know a
    reference's exact shape, which is what makes an overlap volume computable in advance.
    """
    out = tmp_path_factory.mktemp("cube") / "cube10.step"
    with Engine(tools.solidcraft_cli, [out.parent]) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 10, "width": 10, "height": 10})
        eng.call("export", {"path": str(out)})
    return out


def _count_calls(engine: Engine) -> list[str]:
    """Record every tool call made on this real engine instance (no mock library involved)."""
    seen: list[str] = []
    original = engine.call

    def counted(tool, args=None):
        seen.append(tool)
        return original(tool, args)

    engine.call = counted
    return seen


def test_missing_edges_matches_by_midpoint():
    current = [
        {"index": 0, "mid": [0.0, 0.0, 0.0]},
        {"index": 1, "mid": [10.0, 0.0, 0.0]},
        {"index": 2, "mid": [20.0, 0.0, 0.0]},
    ]
    reference = [{"mid": [0.0, 0.0, 0.005]}, {"mid": [20.0, 0.0, 0.0]}]
    gone = missing_edges(current, reference, tol=0.01)
    assert [e["index"] for e in gone] == [1]


def test_overlap_measures_the_exact_shared_volume(tools, cube_step):
    """A 4 mm cube sitting in the corner of the 10 mm reference shares exactly 4³ mm³ with it."""
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, cube_step) as oracle:
            eng.execute("solid.box", {"length": 4, "width": 4, "height": 4})
            probe = next(b for b in eng.measure()["bodies"] if b["name"] not in oracle.bodies)
            assert probe["volume_mm3"] == pytest.approx(64.0), "the probe is not the cube expected"
            wholly_inside = oracle.overlap(probe["name"])
            eng.execute("solid.move", {"bodies": [probe["name"]], "translate": [500, 0, 0]})
            clear_of_it = oracle.overlap(probe["name"])
    assert wholly_inside == pytest.approx(64.0)
    assert clear_of_it == pytest.approx(0.0)


def test_incomplete_results_are_never_read_as_an_answer():
    """Pure check over the keys `inspect.interference` really returns (no engine, no mock)."""
    clean = {
        "checked": 2,
        "count": 0,
        "interferences": [],
        "skipped_pairs": 0,
        "truncated": False,
        "unchecked": [],
    }
    assert interference_incomplete(clean, asked=2) == ""
    assert interference_incomplete(clean | {"truncated": True}, asked=2)
    assert interference_incomplete(clean | {"skipped_pairs": 1}, asked=2)
    assert interference_incomplete(clean | {"unchecked": ["Body7"]}, asked=2)
    # Narrowed to the body asked about, but only where the engine names the pair (spec §147).
    theirs = {"a": "ref", "b": "ref (3)", "reason": "SMLib's result is not a closed solid"}
    mine = {"a": "Body1", "b": "ref", "reason": "SMLib's result is not a closed solid"}
    assert interference_incomplete(clean | {"unchecked": [theirs]}, asked=2), "unnarrowed: refuses"
    assert interference_incomplete(clean | {"unchecked": [theirs]}, asked=2, about="Body1") == ""
    assert interference_incomplete(clean | {"unchecked": [mine]}, asked=2, about="Body1")
    assert interference_incomplete(clean | {"unchecked": ["Body7"]}, asked=2, about="Body1")
    # Narrowing never touches what does not name a pair.
    assert interference_incomplete(clean | {"skipped_pairs": 1}, asked=2, about="Body1")
    assert interference_incomplete(clean | {"checked": 1}, asked=2, about="Body1")
    # A name the engine could not resolve is dropped silently: `checked` is the only tell.
    assert interference_incomplete(clean | {"checked": 1}, asked=2)
    assert interference_incomplete(clean | {"checked": None}, asked=2)


def test_a_side_the_engine_states_no_volume_for_is_not_a_reading():
    """The false zero of spec section 132, over the keys `inspect.compare` really answers with.

    Copied field for field from a real answer of the corpus on 0.40.0-rc.3 — one of the 23 where a
    side comes back with no volume at all. Both totals are `null` there while the regions the engine
    *did* cut hold 384.845 mm³ of material that is only in the candidate, and the oracle used to
    read the pair as (0.0, 0.0): nothing missing, nothing added, a perfect candidate.

    No engine and no mock: the question is what this module does with an answer of that shape, and
    the shape is the engine's.
    """
    answer = {
        "a": {"volume_mm3": 2679.418},
        "b": {"volume_mm3": 2680.0},
        "only_in_a": {"volume_mm3": None, "regions": []},
        "only_in_b": {"volume_mm3": None, "regions": []},
        "regions": [
            {"side": "only_in_a", "volume_mm3": 384.845, "measured_by": "boolean"},
            {
                "side": "only_in_b",
                "volume_mm3": None,
                "measured_by": "not measured",
                "not_measured": (
                    "the cut b − a could not be made (the operation failed: SMLib boolean: "
                    "SMLib's check refuses the result): its regions' volumes are not measured"
                ),
            },
        ],
        "unlisted_regions": 0,
        "warnings": ["the cut b − a could not be made"],
    }
    said = unmeasured(answer)
    assert "states no volume for only_in_a and only_in_b" in said, said
    assert "the cut b − a could not be made" in said, said
    assert "hold 384.845 mm³" in said, said
    # And `incoherent` is what used to be relied on to catch it: on this answer it does not, because
    # with the reading at zero it only compares the two bodies' own volumes, which agree.
    assert incoherent(answer, (0.0, 0.0)) == ""
    # A stated side is a reading again, with nothing of this in the way.
    whole = {**answer, "only_in_a": {"volume_mm3": 1.0}, "only_in_b": {"volume_mm3": 2.0}}
    assert unmeasured(whole) == ""


def test_a_listing_cut_short_is_not_a_reading_either(tools):
    """A comparison that finds more regions than it lists states a partial sum, and says so nowhere.

    The engine's own `max_regions` (its default is 50) caps how many regions an answer **lists**, and
    a side's total is the cut it attributed to those — so the total truncates with the listing. The
    real geometry here is a comb of 60 separate overlaps against one box, where the exact answer is
    120 mm³ of material only in the comb: asked at the default the engine states **98.0** with
    `unlisted_regions: 11` and **no warning**, and asked for `COMPARE_LISTED_REGIONS` it states the
    whole of it. That is why the oracle passes the cap, and `truncated` is the guard for an answer
    that truncates anyway (spec section 132).
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 200, "width": 10, "height": 5, "corner": [0, 0, 0]})
        box = eng.body_names()[0]
        eng.execute("solid.box", {"length": 1, "width": 2, "height": 2, "corner": [1, 4, 4]})
        comb = next(b for b in eng.body_names() if b != box)
        for i in range(1, 60):
            eng.execute(
                "solid.box",
                {
                    "length": 1,
                    "width": 2,
                    "height": 2,
                    "corner": [1 + 3 * i, 4, 4],
                    "operation": "join",
                    "targets": [comb],
                },
            )

        def compare(**extra):
            return eng.call(
                "execute", {"command": "inspect.compare", "params": {"a": box, "b": comb, **extra}}
            )

        short = compare()
        assert short["unlisted_regions"] == 11, short["unlisted_regions"]
        assert short["warnings"] == [], short["warnings"]
        assert truncated(short), "11 regions left out and nothing said about it"
        assert short["only_in_b"]["volume_mm3"] < 100.0, "the stated total is the listed regions only"
        whole = compare(max_regions=COMPARE_LISTED_REGIONS)
        assert whole["unlisted_regions"] == 0
        assert truncated(whole) == ""
        # 120 mm³ exactly, up to whatever this engine's own reading costs: rc.3 answers 120.000 and
        # 0.30.1 answers 119.755, where the truncated one is 98.0 — 18 % low.
        assert abs(whole["only_in_b"]["volume_mm3"] - 120.0) < 1.2, whole["only_in_b"]


def test_an_answer_measured_by_boolean_is_exact_and_needs_no_second_sampling(tools):
    """What `measured_by` buys, asserted against a pair whose exact answer is known.

    Two 20x10x5 boxes 10 mm apart share 500 mm³ exactly, so each side is 500 mm³ exactly. Where the
    engine says it cut and measured — `measured_by: "boolean"`, which 0.40.0-rc.3 does and 0.30.1
    never does — the totals are that number and the spacing changes nothing. 0.30.1 sampled: it
    answered 501.0005 at the default spacing and 497.5031 at half of it, 0.70 % apart on a value
    that is 500 exact. Since 0.40.1 is the floor, exactness is asserted outright (spec section 147).
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 20, "width": 10, "height": 5, "corner": [0, 0, 0]})
        eng.execute("solid.box", {"length": 20, "width": 10, "height": 5, "corner": [10, 0, 0]})
        a, b = eng.body_names()[:2]
        answers = [
            eng.call(
                "execute",
                {
                    "command": "inspect.compare",
                    "params": {"a": a, "b": b, "max_regions": COMPARE_LISTED_REGIONS, **extra},
                },
            )
            for extra in ({}, {"spacing": 0.25})
        ]
        for answer in answers:
            assert unmeasured(answer) == "" and truncated(answer) == ""
            sides = [answer["only_in_a"]["volume_mm3"], answer["only_in_b"]["volume_mm3"]]
            # Whatever the engine, a side's total is the regions of that side added up.
            for side, stated in zip(("only_in_a", "only_in_b"), sides):
                summed = sum(r["volume_mm3"] for r in answer["regions"] if r["side"] == side)
                assert stated == summed, (side, stated, summed)
            # 0.40.1 is the floor: it cuts and measures, so the answer is exact and says so.
            assert measured_exactly(answer)
            assert sides == [500.0, 500.0], sides


def test_overlap_refuses_instead_of_reporting_a_false_zero(tools, cube_step):
    """A reference body the engine cannot resolve must not read as "nothing overlaps"."""
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        oracle = Oracle.open(eng, cube_step)
        eng.execute("solid.box", {"length": 4, "width": 4, "height": 4})
        probe = next(b for b in eng.measure()["bodies"] if b["name"] not in oracle.bodies)
        # Closing deletes the reference bodies, so the pair can no longer be measured at all.
        oracle.close()
        with pytest.raises(OracleUnavailable):
            oracle.overlap(probe["name"])
        # And a body that was never there is just as unanswerable.
        gone = Oracle(eng, component=oracle.component, bodies=["no such reference body"])
        with pytest.raises(OracleUnavailable):
            gone.overlap(probe["name"])
        assert gone.asked == 1, "a refused question still costs a unit of budget"
        assert gone.spent > 0, "and the clock still saw it, which is what the report says"


def test_a_question_costs_one_unit_of_budget_however_long_the_engine_takes(tools, cube_step):
    """The budget is counted in questions, so a loaded machine buys exactly as many as an idle one.

    This is the invariant spec section 130 found broken. With the budget in seconds, the same file
    on the same commit and the same engine came back 21 `exact` on an idle Mac and 17 with 16 busy
    loops on it: the loaded run did the same work in more seconds, so it could pay for fewer trials
    and one feature refused for the budget and took three more with it.

    Here the engine is the real engine and every measurement is its own — only its answer is handed
    back a second later, which is the one thing a loaded machine does to it. The charge has to be
    identical, and the clock has to be the only thing that notices.
    """
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, cube_step) as oracle:
            eng.execute("solid.box", {"length": 4, "width": 4, "height": 4})
            probe = next(b["name"] for b in eng.measure()["bodies"] if b["name"] not in oracle.bodies)
            oracle.overlap(probe)
            quick = oracle.spent
            assert (oracle.asked, oracle.questions_left()) == (1, BUDGET_QUESTIONS - 1)
            answered = eng.call

            def late(tool, args=None):
                out = answered(tool, args)
                time.sleep(1.0)
                return out

            try:
                eng.call = late
                oracle.overlap(probe)
            finally:
                eng.call = answered
            assert (oracle.asked, oracle.questions_left()) == (2, BUDGET_QUESTIONS - 2)
            assert oracle.spent - quick > 1.0, "the clock saw the delay the budget did not"


def test_the_clock_is_a_valve_that_says_so_instead_of_changing_a_count(tools, monkeypatch):
    """Past the ceiling the oracle stops asking — and the refusal says that is why.

    The clock is kept for the run whose engine has stopped making progress, which no count of
    questions can catch. What it may not do is what section 130 caught it doing: move a count with
    the load of the machine and say nothing. So the two refusals are two different sentences, and
    the report carries the flag.

    Nothing is asked of the engine here: the gate is arithmetic over what the oracle already spent,
    and that is what is checked.
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        oracle = Oracle(eng, component=0, bodies=["none of this is asked for"])
        monkeypatch.setattr(oracle_module, "CLOCK_CEILING_SECONDS", 10.0)
        oracle.spent = 9.0
        assert not oracle.clock_exceeded and oracle.questions_left() == BUDGET_QUESTIONS
        assert "questions of budget left" in out_of_budget(oracle)
        oracle.spent = 11.0
        assert oracle.clock_exceeded and oracle.questions_left() == 0
        said = out_of_budget(oracle)
        assert "over the 10 s ceiling" in said, said
        assert "not what the budget alone decides" in said, said
        assert oracle.budget()["clock_exceeded"] is True
        # And a spent budget is still its own sentence, on a clock well inside the ceiling.
        oracle.spent = 1.0
        oracle.asked = BUDGET_QUESTIONS
        assert oracle.questions_left() == 0
        assert "questions of budget left" in out_of_budget(oracle)


def test_engine_work_that_asks_nothing_is_charged_explicitly(tools):
    """`charge` is how work that puts no question still costs the inferences after it.

    `_one_at_a_time` (section 120) applies a declared point and undoes it, per point: the engine
    half of a trial without the measurement. A wall clock charged it before, which is exactly what
    section 130 took out, so now it says what it spends.
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        oracle = Oracle(eng, component=0, bodies=["none of this is asked for"])
        oracle.charge()
        oracle.charge(3)
        assert (oracle.asked, oracle.questions_left()) == (4, BUDGET_QUESTIONS - 4)
        assert oracle.spent == 0.0, "no question was put, so no clock ran"


@pytest.mark.parametrize(
    "filename",
    ["not a step.txt", "not a step.step"],
    ids=["refused on its extension", "refused by the STEP parser"],
)
def test_a_refused_reference_import_is_an_unavailable_oracle(tools, tmp_path, filename):
    """A file the engine will not import must leave one "cannot infer", not a crash.

    `cli.convert` carries on with `oracle=None` and a warning, so the design still converts with
    every inference-needing feature falling back. The two names fail at different depths: the .txt
    never passes parameter validation, while the .step reaches the parser ("not a STEP file (no
    ISO-10303-21 header)") — the corrupt-original case that path actually exists for.
    """
    not_a_step = tmp_path / filename
    not_a_step.write_text("this file is not a STEP model at all\n")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        before = eng.call("inspect_design", {"measure": False, "sketches": False})
        with pytest.raises(OracleUnavailable) as refused:
            Oracle.open(eng, not_a_step)
        after = eng.call("inspect_design", {"measure": False, "sketches": False})
    assert isinstance(refused.value.__cause__, CommandRefused), "the refusal must stay chained"
    assert before["timeline"] == [] and after["timeline"] == [], "the import must leave no feature"
    assert after["bodies"] == []


def test_close_removes_the_reference_from_the_design(tools, cube_step):
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        oracle = Oracle.open(eng, cube_step)
        feature = oracle.feature
        assert feature is not None, "the import did not report its timeline id"
        populated = eng.call("inspect_design", {"measure": False, "sketches": False})
        oracle.close()
        emptied = eng.call("inspect_design", {"measure": False, "sketches": False})
        # inspect_design has no components key at all, so the id itself is proved gone by asking
        # the engine to delete it again: it refuses, because there is no such component any more.
        with pytest.raises(CommandRefused):
            eng.execute("component.delete", {"component": oracle.component})
    # The import is there to begin with: one timeline entry, and bodies that belong to it.
    assert [t["id"] for t in populated["timeline"]] == [feature]
    assert [b["name"] for b in populated["bodies"] if b["feature"] == feature] == oracle.bodies
    # The design's whole record of the reference is that timeline entry plus the bodies pointing at
    # the feature. Both must be gone.
    assert emptied["timeline"] == []
    assert emptied["bodies"] == []
    assert all(t["name"] != REFERENCE_NAME for t in emptied["timeline"])


def test_a_closed_oracle_answers_nothing(tools, cube_step):
    """Both questions refuse once the reference is gone, without asking the engine at all."""
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        oracle = Oracle.open(eng, cube_step)
        assert oracle.edges(), "the edges should be readable while the reference is there"
        oracle.close()
        calls = _count_calls(eng)
        with pytest.raises(OracleUnavailable):
            oracle.overlap("Body1")
        with pytest.raises(OracleUnavailable):
            oracle.edges()  # a stale cache must not pass as the reference's topology either
        assert calls == [], "a closed oracle must not put the question to the engine"


def test_closing_twice_does_not_raise(tools, cube_step):
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        oracle = Oracle.open(eng, cube_step)
        oracle.close()
        oracle.close()  # the component is already gone; deleting it again would be refused
        assert eng.measure()["bodies"] == []


def test_the_context_manager_closes_the_reference(tools, cube_step):
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, cube_step) as oracle:
            assert oracle.bodies
            assert eng.measure()["bodies"], "the reference should be in the design inside the block"
        assert eng.measure()["bodies"] == []


def test_the_context_manager_closes_even_when_the_block_raises(tools, cube_step):
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        oracle = Oracle.open(eng, cube_step)
        with pytest.raises(ZeroDivisionError), oracle:
            raise ZeroDivisionError("the caller blew up mid-inference")
        assert eng.measure()["bodies"] == [], "a failing block must not leave the reference behind"


def test_edges_are_measured_once_and_cached(tools, cube_step):
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, cube_step) as oracle:
            first = oracle.edges()
            calls = _count_calls(eng)
            second = oracle.edges()
            assert calls == [], "edges() asked the engine again instead of using its cache"
    assert len(first) == 12, "the 10 mm cube reference should have 12 edges"
    assert all("mid" in e for e in first)
    assert second == first


def test_shortfall_sees_what_a_body_fails_to_cover(tools, cube_step):
    """One reference body, exact numbers: a 30 mm box around the 10 mm cube covers all of it."""
    with Engine(tools.solidcraft_cli, [cube_step.parent]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, cube_step) as oracle:
            assert len(oracle.bodies) == 1
            reference = next(b for b in eng.measure()["bodies"] if b["name"] in oracle.bodies)
            low = reference["bbox"]["min"]
            eng.execute(
                "solid.box",
                {
                    "length": 30,
                    "width": 30,
                    "height": 30,
                    "corner": [low[0] - 10, low[1] - 10, low[2] - 10],
                    "body_name": "covers",
                },
            )
            box = next(b["volume_mm3"] for b in eng.measure()["bodies"] if b["name"] == "covers")
            missing, leftover = oracle.shortfall("covers")
            assert oracle.volume() == pytest.approx(1000.0, rel=1e-6)
    assert missing == pytest.approx(0.0, abs=1e-6)
    assert box == pytest.approx(27000.0, rel=1e-9)
    # The box less the cube inside it — 26000 mm³ — but `inspect.compare` answers 26142.083 on this
    # exactly-nested pair: its `only_in_a` is 0.55 % high here, where its `only_in_b` is exact. So the
    # band is 1 %, and that error is the reason the mappers compare two readings of the same body
    # against each other and never a reading against an arithmetic expectation.
    assert leftover == pytest.approx(box - 1000.0, rel=1e-2), (leftover, box)


def test_shortfall_refuses_a_multi_body_original(tools, sample_f3d, tmp_path):
    """Several reference bodies: there is no sound one-body comparison, so it refuses to make one.

    The public sample's STEP holds 3 bodies. Taking the reference body a candidate covers best — what
    this used to do — lets a candidate that covers body 2 and ignores body 1 read as `missing ≈ 0` and
    be accepted as an earlier step of the original. Four files of the private corpus have 2 to 10
    bodies, so the unsound reading was reachable. `overlap` still answers: it sums over every
    reference body and needs no pairing.
    """
    step, _ = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, step) as oracle:
            assert len(oracle.bodies) == 3, oracle.bodies
            eng.execute("solid.box", {"length": 4, "width": 4, "height": 4, "body_name": "probe"})
            with pytest.raises(OracleUnavailable, match="the original has 3 bodies"):
                oracle.shortfall("probe")
            # The volume of the whole original is still measurable: it needs no pairing either.
            assert oracle.volume() > 0


@pytest.fixture
def three_body_reference(tools, sample_f3d, tmp_path):
    """The public sample's 3-body STEP as a reference, with each body's measured box.

    Its bodies: #1 x 25..75 / y -25..25 / z 0..50, #2 x -49.87..50.13 / y -40.32..-25.32 / z 0..50,
    #3 x -75..75 / y -40.32..40 / z -10..50. Only #3's box encloses the other two.
    """
    step, _ = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, step) as oracle:
            assert len(oracle.bodies) == 3, oracle.bodies
            boxes = {b["name"]: b["bbox"] for b in eng.measure()["bodies"] if b["name"] in oracle.bodies}
            yield eng, oracle, boxes


def _box(eng: Engine, name: str, low: list[float], high: list[float], margin: float) -> None:
    eng.execute(
        "solid.box",
        {
            "length": high[0] - low[0] + 2 * margin,
            "width": high[1] - low[1] + 2 * margin,
            "height": high[2] - low[2] + 2 * margin,
            "corner": [c - margin for c in low],
            "body_name": name,
        },
    )


def test_standing_for_picks_the_one_reference_body_a_candidate_contains(three_body_reference):
    """The pairing is measured by containment: a cut only removes, so an earlier step contains its body."""
    eng, oracle, boxes = three_body_reference
    first = oracle.bodies[0]
    _box(eng, "around first", boxes[first]["min"], boxes[first]["max"], margin=5.0)
    assert oracle.standing_for("around first") == first
    # And with the pairing in hand the comparison is defined again: it covers that body entirely.
    missing, _leftover = oracle.shortfall("around first", first)
    assert missing == pytest.approx(0.0, abs=1.0), missing


def test_standing_for_refuses_a_candidate_that_contains_no_reference_body(three_body_reference):
    eng, oracle, _boxes = three_body_reference
    eng.execute(
        "solid.box",
        {"length": 4, "width": 4, "height": 4, "corner": [500, 500, 500], "body_name": "elsewhere"},
    )
    with pytest.raises(OracleUnavailable, match="contains 0 of the 3 reference bodies"):
        oracle.standing_for("elsewhere")


def test_standing_for_refuses_a_candidate_that_contains_several(three_body_reference):
    """More than one contained is exactly the open design question: it is not answered with a guess."""
    eng, oracle, boxes = three_body_reference
    low = [min(b["min"][i] for b in boxes.values()) for i in range(3)]
    high = [max(b["max"][i] for b in boxes.values()) for i in range(3)]
    _box(eng, "around all", low, high, margin=5.0)
    with pytest.raises(OracleUnavailable, match="contains 3 of the 3 reference bodies"):
        oracle.standing_for("around all")


def test_a_declaration_settles_the_candidate_that_contains_several(three_body_reference):
    """The case above with the file's answer in hand: measurement never had to choose, only check.

    Which of the three a candidate becomes is not in the geometry — all three are inside it — and it
    is in the file. So the ambiguity the survey cannot resolve is one the declaration does.
    """
    eng, oracle, boxes = three_body_reference
    low = [min(b["min"][i] for b in boxes.values()) for i in range(3)]
    high = [max(b["max"][i] for b in boxes.values()) for i in range(3)]
    _box(eng, "around all", low, high, margin=5.0)
    assert oracle.standing_for("around all", oracle.bodies[1]) == oracle.bodies[1]


def test_a_declaration_neither_side_bears_out_is_dropped(three_body_reference):
    """Measurement still has the last word: a declaration no containment supports is not an answer."""
    eng, oracle, _boxes = three_body_reference
    eng.execute(
        "solid.box",
        {"length": 4, "width": 4, "height": 4, "corner": [500, 500, 500], "body_name": "far away"},
    )
    with pytest.raises(OracleUnavailable, match="neither contains the other"):
        oracle.standing_for("far away", oracle.bodies[0])


# Answers `inspect.compare` really gave, recorded from the pairs named: the four sound ones and the
# one that is not a reading. No engine and no mock — the numbers are the measurement.


def test_an_answer_missing_a_volume_cannot_be_checked_against_itself():
    """Without both volumes nothing verifies the differences, so it is not a reading either."""
    sound = {
        "a": {"volume_mm3": 13297.897},
        "b": {"volume_mm3": 12084.479},
        "only_in_a": {"volume_mm3": 4383.249},
        "only_in_b": {"volume_mm3": 3157.418},
    }
    reading = (3157.418, 4383.249)
    assert incoherent(sound, reading) == ""
    for side in ("a", "b"):
        assert f"does not state the {side!r} volume" in incoherent(sound | {side: {}}, reading)
        assert f"does not state the {side!r} volume" in incoherent(sound | {side: None}, reading)


def test_the_reference_reading_is_what_opening_the_step_gives(tools, sample_f3d, tmp_path):
    """The conversion takes the verification's view of the original from this one reading.

    It used to open the STEP again after saving, which measures the same solid a second time: on
    the corpus's heaviest file that second reading costs 17 s of 108. The two readings have to be
    the same measurement, or the verification would be comparing against something else — asserted
    here on the sample, and measured over all twelve files in spec §62.
    """
    step, _warnings = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        oracle = Oracle.open(eng, step)
        reading = oracle.measured()
        assert oracle.measured() is reading  # read once, then cached
        oracle.close()
        opened = original_bodies(eng, step)
    assert reading == opened
    with pytest.raises(OracleUnavailable, match="the reference is closed"):
        oracle.measured()


def test_an_open_reference_body_has_no_volume_and_the_oracle_says_so(tools):
    """A surface among the reference bodies must not crash the inference, and must not read as zero.

    The engine reports a volume for a closed, consistently oriented body and nothing for an open
    one, and since spec section 90 made the reference every *placement* of the import, an open body
    among them is reachable. Measured on a 74.8 MB sheet metal design of the user's, reading them
    all crashed five extrudes with `float() argument must be a string or a real number, not
    'NoneType'`. So `volumes` leaves it out and `volume_of` refuses, because every caller uses that
    number as the scale a shortfall is read against and an open body gives no scale (section 93).

    The bodies here are the engine's own — a box and a patch over a sketch rectangle — so the
    measurement that `volumes` reads is a real one.
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 10, "width": 10, "height": 10, "body_name": "closed"})
        eng.batch(
            [
                {"command": "sketch.create", "params": {"plane": "XY", "name": "flat"}},
                {
                    "command": "sketch.rectangle.two_point",
                    "params": {"p0": [20, 0], "p1": [30, 10]},
                },
                {"command": "sketch.finish", "params": {}},
                {"command": "surface.patch", "params": {"sketch": "flat", "profiles": [0]}},
            ]
        )
        measured = eng.measure()["bodies"]
        openings = [b for b in measured if b.get("volume_mm3") is None]
        assert len(openings) == 1, [b["name"] for b in measured]
        surface = openings[0]["name"]
        oracle = Oracle(eng, component=0, bodies=[b["name"] for b in measured])
        oracle._measured = measured

        volumes = oracle.volumes()  # this is what used to raise
        assert surface not in volumes, "an open body has no volume to report"
        assert volumes["closed"] == pytest.approx(1000.0)
        assert oracle.volume() == pytest.approx(1000.0), "the total skips what has no volume"
        assert oracle.volume_of("closed") == pytest.approx(1000.0)
        with pytest.raises(OracleUnavailable, match="is open, so it has no volume"):
            oracle.volume_of(surface)


def test_a_comparison_of_nothing_reports_exactly_nothing_now(three_body_reference):
    """The error the share floor was sized against, gone on SolidCraft 0.40.1.

    Under 0.30.1 this measured why the pairing needs a share floor of its own: a 64 mm³ box 500 mm
    away from all three reference bodies, overlapping none of them, still reported tens and hundreds
    of mm³ of shared volume — up to 0.86 % of a body, against a `noise()` of under 5 mm³ (spec
    section 105). It asserted that error was there.

    On 0.40.1 the comparison is an exact boolean and reports **0.0** for all three. So the floor's
    reason is gone; the floor stays, still an order below the smallest real pairing (41.4 %), where
    it costs nothing (spec section 146).
    """
    eng, oracle, _boxes = three_body_reference
    eng.execute(
        "solid.box",
        {"length": 4, "width": 4, "height": 4, "corner": [500, 500, 500], "body_name": "far away"},
    )
    volumes = oracle.volumes()
    shares = {}
    for body in oracle.bodies:
        missing, _outside = oracle.shortfall("far away", body)
        shares[body] = (volumes[body] - missing) / volumes[body]
    assert all(share == 0.0 for share in shares.values()), shares
    assert all(abs(share) < PAIRING_SHARE for share in shares.values()), shares


def _compare(eng, a, b, **extra):
    return eng.call(
        "execute",
        {
            "command": "inspect.compare",
            "params": {"a": a, "b": b, "max_regions": COMPARE_LISTED_REGIONS, **extra},
        },
    )


def test_the_spacing_changes_nothing_on_solidcore(tools):
    """Why `_confirm` could go: on 0.40.1 a clean comparison reads the same at every spacing.

    `_confirm` kept a warned reading when a second sampling at another spacing agreed with the first.
    On 0.30.1 the spacing mattered and did not converge — two 20x10x5 boxes sharing 500 mm³ exactly
    read 0.80 / 0.20 / 0.50 / 0.90 / 0.48 % off at 1.0 / 0.5 / 0.25 / 0.125 / 0.0625 mm (spec
    section 134). On 0.40.1 the engine cuts and measures, every one of the five readings is 500 exact
    and says so, and a second sampling has nothing to add (spec section 147).
    """
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("solid.box", {"length": 20, "width": 10, "height": 5, "corner": [0, 0, 0]})
        eng.execute("solid.box", {"length": 20, "width": 10, "height": 5, "corner": [10, 0, 0]})
        a, b = eng.body_names()[:2]
        answers = [_compare(eng, a, b, spacing=sp) for sp in (1.0, 0.5, 0.25, 0.125, 0.0625)]
    assert all(measured_exactly(answer) for answer in answers)
    assert [answer["only_in_a"]["volume_mm3"] for answer in answers] == [500.0] * 5
