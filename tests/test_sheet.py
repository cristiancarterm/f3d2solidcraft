"""Sheet metal: what the one file with it says, and what the engine builds from that.

The subject is `corpus-12.f3z`, the corpus's only design with sheet metal. It is kept
out of the bench corpus because converting it costs about 5 minutes and peaks near 9 GB (spec
section 92), so the tests here split in two: what the file *says* is read from the real file (34 s,
once per session, skipped where the file is not present), and what the engine *does* is measured on
designs built here, which costs nothing and runs in CI.
"""

import pytest

from f3d2solidcraft.engine import CommandRefused


def _flanges(design, tag):
    return sorted((f for f in design.features if f.tag == tag), key=lambda f: f.ordinal)


def _rules(engine):
    """The design's sheet metal rules as {name: thickness}."""
    return {r["rule"]["name"]: r["values"]["thickness"] for r in engine.execute("sheet.rules", {})["rules"]}


def _sketch_of(ctx, feature):
    return next(
        f
        for f in sorted(ctx.design.features, key=lambda f: f.ordinal)
        if f.tag == "Sketch" and f.sketch == feature.base_flange.sketch
    )


def test_the_engine_builds_a_base_flange_of_the_rule_thickness(tools):
    """The measurement the mapper rests on: a sketch plus a rule is a sheet metal body.

    A 60x40 rectangle on XY with a 2.5 mm rule comes back as exactly 6000.000 mm³ — length x width
    x thickness, so the plate is flat and the rule's bend radius and K-factor, which the file says
    nothing about for a base flange, change nothing here. The material goes from z=0 up to z=2.5,
    along the sketch plane's normal: that is the side the file calls `forward`, and the engine's
    default with no `flip`.
    """
    from f3d2solidcraft.engine import Engine

    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("sheet.manage_rules", {"name": "f3d 2.5 mm", "thickness": "2.5 mm"})
        eng.execute("sketch.create", {"plane": "XY", "name": "base"})
        eng.execute("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [60, 40]})
        eng.execute("sketch.finish", {})
        out = eng.execute(
            "sheet.flange",
            {"type": "base", "sketch": "base", "profiles": "all", "rule": "f3d 2.5 mm"},
        )
        name = out["created_bodies"][0]
        body = next(b for b in eng.body_readings([name]) if b["name"] == name)
        assert body["volume_mm3"] == pytest.approx(6000.0, rel=1e-9)
        assert body["bbox"]["min"][2] == pytest.approx(0.0, abs=1e-9)
        assert body["bbox"]["max"][2] == pytest.approx(2.5, abs=1e-9)
        assert body["faces"] == 6
        assert body["sheet_metal"] is True


def test_the_engine_refuses_a_thickness_passed_to_the_flange(tools):
    """Why the mapper makes a rule at all, kept as a tripwire.

    The thickness is the one number the file does state for a base flange, and it cannot be handed
    to the feature: the engine refuses it and says the rule sets it. If that ever changes, the rule
    this mapper creates becomes needless ceremony and this test is how we find out.
    """
    from f3d2solidcraft.engine import Engine

    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("sketch.create", {"plane": "XY", "name": "base"})
        eng.execute("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [60, 40]})
        eng.execute("sketch.finish", {})
        with pytest.raises(CommandRefused, match="the sheet metal rule sets it"):
            eng.execute(
                "sheet.flange",
                {"type": "base", "sketch": "base", "profiles": "all", "thickness": "2.5 mm"},
            )


def test_the_engine_flips_the_material_to_the_other_side(tools):
    """What `flip` does, measured, so the side the file states is never guessed at.

    The corpus only ever says `forward`, which is the default. This measures what the opposite
    would be — material below the plane instead of above it — so the day a file says the other word
    there is a measurement to map it onto rather than an assumption.
    """
    from f3d2solidcraft.engine import Engine

    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        eng.execute("sheet.manage_rules", {"name": "f3d 2.5 mm", "thickness": "2.5 mm"})
        eng.execute("sketch.create", {"plane": "XY", "name": "base"})
        eng.execute("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [60, 40]})
        eng.execute("sketch.finish", {})
        out = eng.execute(
            "sheet.flange",
            {"type": "base", "sketch": "base", "profiles": "all", "rule": "f3d 2.5 mm", "flip": True},
        )
        name = out["created_bodies"][0]
        body = next(b for b in eng.body_readings([name]) if b["name"] == name)
        assert body["volume_mm3"] == pytest.approx(6000.0, rel=1e-9)
        assert body["bbox"]["min"][2] == pytest.approx(-2.5, abs=1e-9)
        assert body["bbox"]["max"][2] == pytest.approx(0.0, abs=1e-9)
