"""Each feature takes its parameters from the record the timeline item names (spec 0.4, item 1).

The two readers of a file used to be paired by count: ezf3d's timeline and cadmpeg's features had to
hold exactly as many entries, or no parameter was attached anywhere. Measured, 6 of the corpus's 15
files do not agree on the count and lost every parameter that way. The item oid at the end of each
cadmpeg feature id names the record directly, so the pairing is per feature now.
"""

import json
import os
import re
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine
from f3d2solidcraft.mappers.params import add_parameters, follows, solidcraft_name

# What the `saved` fixture saw of each converted file: every parameter name, and the saved design.
_tables: dict[str, set[str]] = {}
_outputs: dict[str, Path] = {}
CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()
_LITERAL = re.compile(r"^\s*-?[0-9.]+(e-?\d+)?\s*(mm|cm|m|in|deg|rad)?\s*$")


def test_every_feature_of_the_sample_is_paired_and_nothing_is_said_about_it(design):
    assert design.unpaired == []
    assert not any("could not be paired" in w for w in design.warnings)
    by_name = {f.name: f for f in design.features}
    assert by_name["Extrude 1"].parameters == ("d5", "d6")
    assert by_name["Extrude 1"].item is not None


@pytest.fixture(scope="module")
def saved(tools, tmp_path_factory):
    """Convert a corpus file once per module: (exit code, report, the saved design's linked parameters).

    The linked parameters are the model parameters of the saved design — the ones a feature or a
    sketch owns, not the user's — whose expression names another parameter, as `(source, feature,
    expression)`: `("feature", "Extrude 7", "fusion_d125")`.
    """
    done: dict[str, tuple[int, dict, list[tuple[str, str, str]]]] = {}

    def convert(filename: str):
        if filename not in done:
            source = CORPUS / filename
            if not source.is_file():
                pytest.skip(f"private corpus file missing: {source}")
            folder = tmp_path_factory.mktemp("saved")
            out, report_path = folder / "out.solidcraft", folder / "out.report.json"
            code = main(["convert", str(source), "-o", str(out), "--report", str(report_path)])
            with Engine(tools.solidcraft_cli, [folder]) as eng:
                eng.call("open", {"path": str(out)})
                listed = eng.execute("parameters.list", {"filter": "all"})["parameters"]
            linked = [
                (p["source"], p.get("feature") or "", p["expression"])
                for p in listed
                if p.get("source") != "user" and not _LITERAL.match(p.get("expression") or "")
            ]
            done[filename] = (code, json.loads(report_path.read_text()), linked)
            _tables[filename] = {p["name"] for p in listed}
            _outputs[filename] = out
        return done[filename]

    return convert


def test_the_saved_caja_carries_its_extrude_distances_and_sketch_dimensions_as_parameters(saved):
    """5 extrude distances follow a parameter (`d125` reads `LatchFrameThickness`, the name it
    restates) and 8 sketch dimensions do; the counts and the verification are what they were before
    the pairing."""
    code, report, linked = saved("Caja parametrica v2.f3d")
    assert code == 0 and report["verification"]["ok"] is True
    assert report["unpaired_features"] == 0
    assert report["counts"] == {"exact": 21, "inferred": 0, "fallback": 2, "failed": 14, "skipped": 0}
    extrudes = [
        (feature, expression) for source, feature, expression in linked if feature.startswith("Extrude")
    ]
    assert len(extrudes) == 5
    assert ("Extrude 7", "LatchFrameThickness") in extrudes
    assert len([x for x in linked if x[0] == "sketch"]) == 8


def test_the_saved_mango_carries_its_sketch_dimensions_as_parameters(saved):
    code, report, linked = saved("mango para lima.f3d")
    assert code == 0 and report["verification"]["ok"] is True
    assert report["counts"] == {"exact": 8, "inferred": 0, "fallback": 16, "failed": 0, "skipped": 0}
    assert len([x for x in linked if x[0] == "sketch"]) == 16


def test_the_saved_caja_fillets_and_chamfers_follow_their_parameters(saved):
    """`Fillet 3` follows `X_ComputedCornerRadius` through `d22`, `Chamfer 4` follows
    `X_ComputedEdgeChamfer` through `d23`, in both components (the second copy's are `d22_1` and
    `d23_1`): each reads the name its parameter restates, not the parameter."""
    code, _report, linked = saved("Caja parametrica v2.f3d")
    assert code == 0
    features = {(feature, expression) for source, feature, expression in linked if source == "feature"}
    assert {
        ("Fillet1", "X_ComputedCornerRadius"),
        ("Chamfer1", "X_ComputedEdgeChamfer"),
        ("Fillet2", "X_ComputedCornerRadius"),
        ("Chamfer2", "X_ComputedEdgeChamfer"),
    } <= features


def test_the_saved_mango_revolves_follow_their_angle(saved):
    """Both `Revolve 2` turn by their `AlongAngle`; the one revolved the other way carries the sign.

    `d21` and `d30` are plain numbers (`360.0 deg`), so they keep a `fusion_dN` of their own."""
    code, _report, linked = saved("mango para lima.f3d")
    assert code == 0
    revolves = sorted(expression for source, feature, expression in linked if feature == "Revolve 2")
    assert revolves == ["-fusion_d30", "fusion_d21"]


# --- 0.4.1: a model parameter that only restates the user's names is not a parameter of its own ---
#
# Reported on `Caja parametrica v2` and `corpus-03`: after converting, every dimension and feature
# read `fusion_d7`, `fusion_d34`... where the designer had written `CaseExteriorWidth` and
# `HingeDiameter / 2`. An alias of one name now carries that name, a formula that reaches one of the
# user's names carries the formula, and only plain numbers keep a `fusion_dN` of their own.

_CAJA_ALIASES = {
    "d7": "CaseExteriorWidth",
    "d16": "CaseThickness",
    "d22": "X_ComputedCornerRadius",
    "d23": "X_ComputedEdgeChamfer",
    "d125": "LatchFrameThickness",
    "d22_1": "X_ComputedCornerRadius",
}
_CAJA_FORMULAS = {
    "d34": "HingeDiameter / 2",
    "d36": "-X_ComputedCornerRadius",
    "d51": "HingeDiameter - 1.5 mm",
    "d196": "CaseExteriorHeight * ( 2 / 3 ) - LatchDiameter / 2",
}
_CAJA_PLAIN = {"d10": "fusion_d10", "d201": "fusion_d201", "d234": "fusion_d234"}
_CORPUS_03_ALIASES = {
    "d326": "phonewidth",
    "d327": "Phonelength",
    "d328": "shelfthickness",
    "d331": "phonecornerradius",
    "d353": "magsafediameter",
}
_CORPUS_03_FORMULAS = {
    "d320": "-( subtractfromdocksides )",
    "d324": "-( shelfthickness )",
    "d334": "-( removefromtop )",
    "d352": "phonecornerradius + 3 mm",
    "d363": "-phonethicknessdepth",
    "d389": "-magsafedepth",
}


def test_an_alias_or_a_formula_of_the_users_names_is_used_as_written_and_not_created(tools, corpus_design):
    """On `Caja`, the engine holds no `fusion_dN` for an alias or a formula, and what a feature or a
    dimension is handed is the name or the formula itself, valued by the engine as the file says."""
    design = corpus_design("Caja parametrica v2.f3d")
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        from f3d2solidcraft.mappers import Context

        ctx = Context(eng, design, {}, {}, {}, {}, {})
        warnings = add_parameters(ctx)
        listed = {p["name"] for p in eng.execute("parameters.list", {"filter": "user"})["parameters"]}
    assert warnings == []
    for fusion, expected in {**_CAJA_ALIASES, **_CAJA_FORMULAS, **_CAJA_PLAIN}.items():
        assert ctx.param_names[fusion] == expected, fusion
    inlined = {solidcraft_name(n) for n in {**_CAJA_ALIASES, **_CAJA_FORMULAS}}  # d22_1 stays d22_1
    assert not inlined & listed
    assert {"fusion_d10", "fusion_d201", "CaseExteriorWidth", "HingeDiameter"} <= listed
    # valued by the engine without a parameter left behind: an alias takes its name's value
    assert ctx.param_values["d7"] == (150.0, "mm")
    assert ctx.param_values["d34"] == (pytest.approx(2.5), "mm")
    assert ctx.param_values["d36"] == (pytest.approx(-4.6), "mm")
    assert ctx.param_values["d221"] == (pytest.approx(45.0), "deg")


def test_a_formula_is_negated_whole():
    """A reversed feature negates the whole formula, not its first term; a negated name stays bare."""
    from f3d2solidcraft.mappers.params import negated

    assert negated("fusion_d30") == "-fusion_d30"
    assert negated("CaseOverlap") == "-CaseOverlap"
    assert negated("HingeDiameter / 2") == "-(HingeDiameter / 2)"
    assert negated("-X_ComputedCornerRadius") == "X_ComputedCornerRadius"
    assert negated("-( shelfthickness )") == "shelfthickness"
    assert negated("-(a) + (b)") == "-(-(a) + (b))"
    assert negated("-CaseThickness / 2 - Tolerance") == "-(-CaseThickness / 2 - Tolerance)"
    assert negated("a + b") == "-(a + b)"
    assert negated("-(a + b)") == "(a + b)"
    assert negated("5") == "-5"
    assert negated("-5") == "5"
    assert negated("5 mm") == "-5 mm"
    assert negated("-5 mm") == "5 mm"


def test_follows_refuses_a_formula_whose_value_does_not_match(tools, corpus_design):
    """`follows` checks the engine's value of a formula as it checks a parameter's."""
    design = corpus_design("Caja parametrica v2.f3d")
    with Engine(tools.solidcraft_cli) as eng:
        eng.call("new_design", {})
        from f3d2solidcraft.mappers import Context

        ctx = Context(eng, design, {}, {}, {}, {}, {})
        add_parameters(ctx)
        fillet = next(f for f in design.features if "d22" in f.parameters)
        assert follows(ctx, fillet, "Radius", 4.6, "mm") == "X_ComputedCornerRadius"
        assert follows(ctx, fillet, "Radius", -4.6, "mm") == "-X_ComputedCornerRadius"
        assert follows(ctx, fillet, "Radius", 4.0, "mm") is None


def test_the_saved_caja_shows_the_users_names_and_formulas(saved):
    """Converted, saved and reopened: the extrude and the dimensions read the designer's names."""
    code, report, linked = saved("Caja parametrica v2.f3d")
    assert code == 0 and report["verification"]["ok"] is True
    expressions = {expression for _source, _feature, expression in linked}
    assert ("Extrude 7", "LatchFrameThickness") in {(f, e) for _s, f, e in linked}
    assert "CaseExteriorWidth" in expressions  # a sketch dimension, d7
    assert "HingeDiameter / 2" in expressions  # a formula dimension, d34
    assert not [e for e in expressions if re.search(r"\bfusion_d\d+\b|\bd\d+_\d+\b", e)], expressions


def test_the_saved_caja_keeps_plain_numbers_as_fusion_parameters(saved_table):
    names = saved_table("Caja parametrica v2.f3d")
    assert {"fusion_d10", "fusion_d201", "fusion_d234"} <= names
    inlined = {solidcraft_name(n) for n in {**_CAJA_ALIASES, **_CAJA_FORMULAS}}  # d22_1 stays d22_1
    assert not inlined & names


@pytest.fixture(scope="module")
def saved_table(saved):
    """Every parameter name in the saved design (the conversion is the `saved` fixture's)."""

    def names(filename: str) -> set[str]:
        saved(filename)
        return _tables[filename]

    return names


def test_changing_a_users_parameter_in_the_saved_caja_reaches_the_features_that_name_it(saved, tools):
    """`CaseExteriorWidth` 150 -> 160 mm on the saved design: every feature or dimension parameter
    that names it comes out at the new width, and a formula over another name does not move.

    Geometry is not asserted: where a body was imported rather than rebuilt it stays as it was.
    """
    code, _report, _linked = saved("Caja parametrica v2.f3d")
    assert code == 0
    out = _outputs["Caja parametrica v2.f3d"]
    with Engine(tools.solidcraft_cli, [out.parent]) as eng:
        eng.call("open", {"path": str(out)})

        def by_expression():
            listed = eng.execute("parameters.list", {"filter": "all"})["parameters"]
            return [(p["expression"], p["value"]) for p in listed if p.get("source") != "user"]

        before = by_expression()
        eng.call("set_parameter", {"name": "CaseExteriorWidth", "value": "160 mm"})
        after = by_expression()
    widths = [value for expression, value in before if expression == "CaseExteriorWidth"]
    assert widths and set(widths) == {150.0}
    assert {value for expression, value in after if expression == "CaseExteriorWidth"} == {160.0}
    halves = [value for expression, value in after if expression == "HingeDiameter / 2"]
    assert halves and all(value == pytest.approx(2.5) for value in halves)


# --- review of 0.4.1: what the rebuild check nudges, and an alias of a plain model parameter ---


def _parameters_of(eng, design):
    from f3d2solidcraft.mappers import Context

    ctx = Context(eng, design, {}, {}, {}, {}, {})
    return ctx, add_parameters(ctx)
