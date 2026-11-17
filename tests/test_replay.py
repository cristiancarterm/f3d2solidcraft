import dataclasses

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.fallback import original_step
from f3d2solidcraft.mappers import MAPPERS, Unsupported
from f3d2solidcraft.oracle import Oracle
from f3d2solidcraft.replay import replay


def test_the_whole_sample_replays_without_aborting(design, tools, sample_f3d, tmp_path):
    """With an oracle, as the conversion always has one.

    cadmpeg 0.6.8 types this sample's `Extrude 1` as a trimmed region: no loop to translate and no
    index to resolve. It used to fall back here, because the measured path is all it had and that
    path could not even start — the feature declares no output body, and the trial body it would be
    judged by contains none of the three reference bodies, so which one it stands in for is not
    defined. It is `exact` now, translated from the entity segments the boundary names, and the
    features behind it come with it (`test_trimmed_region`, spec section 112).
    """
    step, _ = original_step(tools.cadmpeg, sample_f3d, tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        with Oracle.open(eng, step) as oracle:
            result, _ctx = replay(design, eng, oracle)
    status = {r.name: r.status for r in result.features}
    assert len(result.features) == 26
    assert status["Sketch 1"] == "exact"
    assert status["Extrude 1"] == "exact"
    assert status["Extrude 2"] == "exact"
    assert all(s == "fallback" for n, s in status.items() if n.startswith("SurfaceDeleteFace"))
    # `historical_faces`: its own wall, and one this file only shows once ordinal 1 is built
    # (spec section 112).
    assert status["Extrude 7"] == "fallback"
    assert result.fallback_bodies
    assert result.parameter_warnings == []


def test_a_feature_failing_after_sketch_finish_rolls_back_exactly(design, tools):
    first_sketch = next(f for f in sorted(design.features, key=lambda f: f.ordinal) if f.tag == "Sketch")

    def after_finish(feature, ctx):
        MAPPERS["Sketch"](feature, ctx)
        raise Unsupported("after finish")

    one = dataclasses.replace(design, features=[dataclasses.replace(first_sketch, tag="__test_after_finish")])
    MAPPERS["__test_after_finish"] = after_finish
    try:
        with Engine(tools.solidcraft_cli) as eng:
            eng.call("new_design", {})
            result, ctx = replay(one, eng)
            params = eng.execute("parameters.list", {})["parameters"]
            sketches = eng.call("inspect_design", {})["sketches"]
    finally:
        del MAPPERS["__test_after_finish"]
    assert [r.status for r in result.features] == ["failed"]
    assert len([p for p in params if p["source"] == "user"]) == 32
    # Nothing is standing that the rollback should have taken: every sketch here is one the
    # unnamed-sketch step built and reported. This design is the file cut down to a single feature,
    # so the file's other seven sketches are named by no feature and that step builds them.
    assert len(sketches) == len(result.sketches) == 7
    sketch = design.sketches[first_sketch.sketch]
    assert sketch.id not in ctx.sketch_names
    assert not {e.id for e in sketch.entities} & set(ctx.curve_ids)


def test_a_mapper_bug_is_reported_and_the_replay_goes_on(design, tools):
    ordered = sorted(design.features, key=lambda f: f.ordinal)
    first_sketch, first_extrude = ordered[0], ordered[1]

    def buggy(feature, ctx):
        MAPPERS["Sketch"](feature, ctx)
        return {}["no such key"]

    one = dataclasses.replace(
        design, features=[dataclasses.replace(first_sketch, tag="__test_buggy"), first_extrude]
    )
    MAPPERS["__test_buggy"] = buggy
    try:
        with Engine(tools.solidcraft_cli) as eng:
            eng.call("new_design", {})
            result, ctx = replay(one, eng)
            sketches = eng.call("inspect_design", {})["sketches"]
    finally:
        del MAPPERS["__test_buggy"]
    first, second = result.features
    assert first.status == "failed"
    assert first.detail == "internal mapper error: KeyError: 'no such key'"
    # As above: what stands is exactly what the unnamed-sketch step built, so the buggy mapper's
    # own sketch was rolled back.
    assert len(sketches) == len(result.sketches)
    assert first_sketch.sketch not in ctx.sketch_names
    # the conversion carried on: the extrude after it was tried and fell back cleanly
    assert second.status == "fallback"
    assert second.detail == "not mapped: its sketch was not rebuilt"


def test_a_mapper_can_report_inferred(design, tools):
    import dataclasses

    from f3d2solidcraft.mappers import MAPPERS

    first = design.features[0]
    marked = dataclasses.replace(first, tag="__test_inferred")
    MAPPERS["__test_inferred"] = lambda feature, ctx: ("inferred", ["score 0.98"])
    try:
        with Engine(tools.solidcraft_cli) as eng:
            eng.call("new_design", {})
            result, _ = replay(dataclasses.replace(design, features=[marked]), eng)
    finally:
        del MAPPERS["__test_inferred"]
    assert [r.status for r in result.features] == ["inferred"]
    assert result.features[0].warnings == ["score 0.98"]


@pytest.mark.parametrize(
    "bad",
    [("inferred", ["score 0.98"], "extra"), ("infered", ["score 0.98"]), ("inferred", "score 0.98")],
    ids=["a 3-tuple", "an unknown status", "warnings that are not a list"],
)
def test_a_malformed_mapper_return_is_rolled_back_instead_of_believed(
    design, tools, bad, sample_f3d, tmp_path
):
    """A mapper that answers in a shape the replay does not know takes the fallback path.

    The mapper builds the extrude for real first, so the rollback has something to undo: a bad
    return value must never leave that body behind nor invent a status for the report.
    """
    # `Sketch 3` + `Extrude 3`, not the sample's first pair: cadmpeg 0.6.8 types that first extrude
    # as a trimmed region, so building it at all means measuring the regions against the original —
    # and whether that finishes depends on how fast the machine is, which is no basis for a test
    # (spec section 97). This extrude resolves from the index the file gives, on any machine, so the
    # malformed value really is reached.
    by_ordinal = {f.ordinal: f for f in design.features}
    sketch_feature, extrude_feature = by_ordinal[4], by_ordinal[5]

    def malformed(feature, ctx):
        MAPPERS["Extrude"](feature, ctx)
        return bad

    two = dataclasses.replace(
        design, features=[sketch_feature, dataclasses.replace(extrude_feature, tag="__test_malformed")]
    )
    MAPPERS["__test_malformed"] = malformed
    try:
        with Engine(tools.solidcraft_cli) as eng:
            eng.call("new_design", {})
            result, _ctx = replay(two, eng)
            bodies = eng.measure()["bodies"]
    finally:
        del MAPPERS["__test_malformed"]
    sketch, extrude = result.features
    assert sketch.status == "exact"
    assert extrude.status == "fallback"  # the extrude has an output body, so it falls back
    assert extrude.detail.startswith("internal mapper error: ")
    assert bodies == [], "the malformed return skipped the rollback and left its body behind"
