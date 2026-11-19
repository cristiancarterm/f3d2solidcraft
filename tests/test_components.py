"""Fusion's component tree, rebuilt in SolidCraft. Every case runs on a real corpus design."""

import dataclasses
import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.engine import Engine
from f3d2solidcraft.fallback import apply_fallback, original_step
from f3d2solidcraft.mappers.components import IMPORT_HOLDER, reconcile_components, world_transform
from f3d2solidcraft.replay import replay


def _tree(engine):
    components = engine.execute("component.list", {"compact": True, "bodies": True})["components"]
    return components, {c["id"]: c for c in components}


def _scaled(name: str) -> str:
    """The report a component gets whose placement resizes it, which the engine's move cannot do.

    This used to be the report for **any** placement that was not the identity, scale or not; a
    placement is applied now and only the scale is left (spec section 101). No occurrence of the
    corpus is scaled, so the text is reached the way both halves used to be: by resizing one real
    occurrence and changing nothing else.
    """
    return (
        f"component {name}: this placement scales its component by (2.0, 2.0, 2.0), which the "
        "engine's move does not do, so the component is built at the size it was modelled"
    )


def test_components_are_built_with_fusion_nesting_and_own_their_sketches(tools, corpus_design, tmp_path):
    """`mango para lima` keeps Collet inside File Handle, each sketch in the component that has it.

    The timeline is cut to the two sketches that sit in different components (ordinals 1 and 5), so
    the test asks only about placement: whether a sketch is authored where Fusion authored it.

    Cutting the timeline leaves the file's other four sketches named by no feature, so
    `replay._unnamed_sketches` builds those too — and this is where their placement is checked. The
    file declares `Collet` with four sketches and `File Handle` with two, and all six come out: the
    Collet sketch whose spline has unevenly spaced interior knots used to be the one refusal left,
    and is drawn as a chain of Bézier segments since spec section 77.
    """
    whole = corpus_design("mango para lima.f3d")
    design = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal in (1, 5)])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, _ctx = replay(design, eng)
        components, by_id = _tree(eng)
    assert [r.status for r in result.features] == ["exact", "exact"]
    # `File Handle` carries the file's own placement, which is applied and so reported about.
    assert result.component_warnings == []
    handle = next(c for c in components if c["name"] == "File Handle")
    collet = next(c for c in components if c["name"] == "Collet")
    assert by_id[collet["parent"]] is handle  # nested, not side by side
    assert by_id[handle["parent"]]["parent"] is None  # and the root holds File Handle
    assert (handle["sketches"], collet["sketches"]) == (2, 4)
    assert [s.status for s in result.sketches] == ["exact"] * 4


def test_imported_bodies_join_the_component_fusion_owns_them_in(tools, corpus_design, tmp_path):
    """The fallback import carries Fusion's components, so its bodies go into the tree we built.

    The timeline is cut to nothing: every body falls back, which is the only path that imports
    them, and the question is purely where they land. `mango para lima` has both of its bodies in
    Collet, nested inside File Handle.
    """
    design = dataclasses.replace(corpus_design("mango para lima.f3d"), features=[])
    step, _warnings = original_step(tools.cadmpeg, Path(design.source), tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(design, eng)
        warnings = apply_fallback(ctx, result, step) + reconcile_components(ctx, result.imported_bodies)
        components, by_id = _tree(eng)
        bodies = {b["name"] for b in eng.measure()["bodies"]}
    assert warnings == []
    # cadmpeg 0.6.8 attributes one body to each component where 0.6.7 put both in `Collet`, and the
    # import names each body after the component that holds it, so each lands in its own
    # (spec section 95).
    assert sorted(result.imported_bodies) == ["Collet", "File Handle"]
    holding = {c["name"]: c for c in components if c["bodies"]}
    assert sorted(holding) == ["Collet", "File Handle"]
    assert {b for c in holding.values() for b in c["bodies"]} == bodies  # every body of the design
    assert holding["Collet"]["bodies"] == ["Collet"]
    assert holding["File Handle"]["bodies"] == ["File Handle"]
    # And the nesting is still Fusion's: Collet inside File Handle.
    assert by_id[holding["Collet"]["parent"]]["name"] == "File Handle"
    # The import's own holder stays, emptied: SolidCraft refuses to delete what bodies left.
    assert any(c["name"] == IMPORT_HOLDER and not c["bodies"] for c in components)


def test_a_design_without_components_builds_none(design, tools, tmp_path):
    """A single-component design is authored in the root, as before: no component is created."""
    one = dataclasses.replace(design, features=[f for f in design.features if f.ordinal == 0])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(one, eng)
        components, _by_id = _tree(eng)
    assert ctx.component_ids == {} and result.component_warnings == []
    assert len(components) == 1 and components[0]["parent"] is None


IDENTITY_ROWS = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))


def _placed_as(design, name, **change):
    """The same design with one named component's occurrence placed differently, nothing else touched."""
    moved = [
        dataclasses.replace(occ, **change)
        if (held := design.components.get(occ.component or "")) is not None and held.name == name
        else occ
        for occ in design.occurrences
    ]
    return dataclasses.replace(design, occurrences=moved)


def _sketches(engine):
    return json.dumps(engine.call("inspect_design", {})["sketches"], sort_keys=True)


def _planes(engine):
    """Each sketch's plane as the engine reports it: its **own component's** coordinates."""
    return {
        s["name"]: [round(c, 6) for c in s["plane"]["origin"]]
        for s in engine.call("inspect_design", {})["sketches"]
    }


def _placements(engine):
    """Every occurrence the design holds, by component name, as the engine reports its matrix."""
    placed = {}
    for component in engine.execute("component.list", {"compact": True})["components"]:
        for occ in component.get("occurrences") or []:
            placed[component["name"]] = occ.get("transform")
    return placed


def test_the_placement_the_file_states_is_applied_and_read_back(tools, corpus_design, tmp_path):
    """`mango para lima` places `File Handle` at (-4.448, -2.309, 2.139) mm, and so is it here.

    The engine answers with a 4x4 for row vectors — the rotation transposed, the translation in the
    last row — and `place` compares it with the file's own 3x4 term for term before keeping it. So
    this asserts the same thing the converter does, from the other side: the matrix in the design is
    the matrix in the file, and no warning is reported for it.

    **The geometry moves with it, and this measures that too.** A sketch reaches the engine as an
    explicit plane, and an explicit plane is placed in world coordinates whatever component is
    active — measured in the engine: a `solid.box` in a component moved 100 mm along x lands at
    x 100..110, while a sketch created there with `origin: [0,0,0]` and extruded lands at x 0..3. So
    the plane is carried into the world by the placement of the component holding it before it is
    built (spec section 102). The engine stores it back in the component's own coordinates, which
    that placement undoes, so what this asserts is the **consistency**: every sketch plane comes out
    at the coordinates the file gives it inside its component, placed or not. Without the frame
    being carried, a moved component's sketches land at the placement's inverse instead —
    `File Handle`'s three would sit 4.448, 2.309 and 2.139 mm off inside their own component. Where
    they land in the *world* is what `test_a_placed_component_builds_its_geometry_where_the_original_has_it`
    measures, against the original itself.
    """
    whole = corpus_design("mango para lima.f3d")
    cut = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal in (1, 5)])
    runs = {}
    for label, design in (
        ("placed", cut),
        ("identity", _placed_as(cut, "File Handle", transform=IDENTITY_ROWS)),
    ):
        with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
            eng.call("new_design", {})
            result, _ctx = replay(design, eng)
            runs[label] = (result, _sketches(eng), _placements(eng), _planes(eng))
    assert runs["placed"][0].component_warnings == []
    handle = runs["placed"][2]["File Handle"]
    # The file's own numbers, transposed into the engine's row-vector answer.
    assert handle[3][:3] == pytest.approx([-4.447834227105019, -2.3086530688813123, 2.1391811582237086])
    for row, wanted in zip(handle[:3], ([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        assert row[:3] == pytest.approx(wanted, abs=1e-12)
    # Collet is at the identity in the file, so the engine reports no matrix for it either way.
    assert runs["placed"][2]["Collet"] is None
    assert runs["placed"][3] == runs["identity"][3], (
        "the placement left its component's sketches off inside their own component"
    )
    # And those are the file's own numbers: `Sketch2` and `Sketch4` of Collet sit 51 and 73 mm up.
    assert runs["placed"][3]["Sketch 1"] == [0.0, 0.0, 0.0]
    assert sorted(runs["placed"][3].values()) == [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 51.0],
        [0.0, 0.0, 73.0],
    ]


def test_a_placement_that_resizes_its_component_is_reported_and_not_applied(tools, corpus_design, tmp_path):
    """The engine's move turns and shifts; it does not resize, and a scale is said rather than lost.

    No occurrence of the corpus is scaled, so this is reached by resizing Collet's and changing
    nothing else. The point is that nothing is approximated: the sketches match a run without it.
    """
    whole = corpus_design("mango para lima.f3d")
    cut = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal in (1, 5)])
    runs = {}
    for label, design in (("plain", cut), ("scaled", _placed_as(cut, "Collet", scale=(2.0, 2.0, 2.0)))):
        with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
            eng.call("new_design", {})
            result, _ctx = replay(design, eng)
            runs[label] = (result, _sketches(eng), _placements(eng), _planes(eng))
    assert runs["scaled"][0].component_warnings == [_scaled("Collet")]
    assert [r.status for r in runs["scaled"][0].features] == ["exact", "exact"]
    # A placement that is not applied does not reach the geometry either: the component and its
    # sketches stay consistent with each other instead of one of the two moving on its own.
    assert runs["scaled"][1] == runs["plain"][1], "the scale moved the geometry"
    assert runs["scaled"][2]["Collet"] is None, "the scale was half-applied"


def test_a_half_turn_placement_is_applied(tools, corpus_design, tmp_path):
    """`Caja parametrica v2` places its `Top` at exactly 180° about z and 65.6 mm along y.

    A half turn is where the direct axis-and-angle formula divides by zero, so this is the file
    that proves the quaternion branch: the matrix has to come back as the file's, and only the
    read-back says so.
    """
    design = corpus_design("Caja parametrica v2.f3d")
    cut = dataclasses.replace(design, features=[])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, _ctx = replay(cut, eng)
        placed = _placements(eng)
    assert result.component_warnings == []
    top = placed["Top"]
    assert top[3][:3] == pytest.approx([0.0, 65.6, 0.0], abs=1e-9)
    for row, wanted in zip(top[:3], ([-1, 0, 0], [0, -1, 0], [0, 0, 1])):
        assert row[:3] == pytest.approx(wanted, abs=1e-9)


def test_a_grounded_first_component_is_still_placed(tools, corpus_design, tmp_path):
    """A design's first part is grounded when it is made, and a grounded occurrence does not move.

    Which component that is depends on what reached the design first: with the reference original
    inserted the oracle's component takes the ground, and `replay` on its own leaves it to the
    design's. So without the ground being lifted this placement was refused — and only in the runs
    that have no oracle, which is the sort of thing that goes unnoticed. `mango para lima`'s
    `File Handle` is that first component here.
    """
    whole = corpus_design("mango para lima.f3d")
    cut = dataclasses.replace(whole, features=[])
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, _ctx = replay(cut, eng)
        placed = _placements(eng)
        grounded = {
            occ["name"]: occ["grounded"]
            for component in eng.execute("component.list", {"compact": True})["components"]
            for occ in component.get("occurrences") or []
        }
    assert result.component_warnings == []
    assert placed["File Handle"][3][:3] == pytest.approx(
        [-4.447834227105019, -2.3086530688813123, 2.1391811582237086]
    )
    # The ground is lifted for the move and put back, so the design is left as the engine made it.
    assert grounded["File Handle:1"] is True


PACKAGES = Path(os.environ.get("F3D2SC_PACKAGES", "~/.f3d2solidcraft/corpus/propios-f3z")).expanduser()
CORPUS_DIR = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def test_a_placed_component_builds_its_geometry_where_the_original_has_it(tools, corpus_design, tmp_path):
    """The whole point, measured against the original rather than against a number written down.

    `mango para lima`'s `File Handle` is placed at (-4.448, -2.309, 2.139) mm, and `Revolve 2` at
    ordinal 3 is the body that component is born as. The original is exported and imported into the
    same session, so the two are measured by the same engine at the same time: the body the replay
    builds must occupy the same box as the body the original has.

    Until the frame was carried into the world this body stood at x/y symmetric about the axis and
    z 0..73 — the component's own coordinates — while the original's sits at z 2.139..75.139. The
    bodies matched in volume and sat 5 mm apart, which no body count would ever have reported.
    """
    whole = corpus_design("mango para lima.f3d")
    design = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal in (1, 3)])
    step, _warnings = original_step(tools.cadmpeg, Path(whole.source), tmp_path / "original.step")
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(design, eng)
        built = ctx.body_names[next(f for f in design.features if f.ordinal == 3).outputs[0]]
        ours = next(b for b in eng.measure()["bodies"] if b["name"] == built)
        eng.execute("file.insert_step", {"path": str(step), "name": "original"})
        theirs = next(b for b in eng.measure()["bodies"] if b["name"] == "File Handle" and b["name"] != built)
    assert [r.status for r in result.features] == ["exact", "exact"]
    # The revolve is the first feature of this component: the original's body carries the whole
    # timeline after it, so the two share a box rather than a volume. The box is what placement is.
    for corner in ("min", "max"):
        assert ours["bbox"][corner] == pytest.approx(theirs["bbox"][corner], abs=0.001), corner


def test_a_chain_of_placements_composes_from_the_inside_out(tools, corpus_design, tmp_path):
    """Nested placements, which the corpus does not have: no component sits under two moved ones.

    So the composition is exercised by placing `File Handle` **and** `Collet` inside it — a 90° turn
    about z outside, 10 mm along x inside — and measuring where Collet's own plane lands in the
    world. Turning (10, 0, 0) a quarter turn about z gives (0, 10, 0), so Collet's origin has to
    come out there and not at (10, 0, 0), which is what a chain composed the other way round, or
    not composed at all, would give.

    The engine is the judge rather than the arithmetic: the plane is read back from the design.
    """
    quarter = ((0.0, -1.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    along_x = ((1.0, 0.0, 0.0, 10.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    whole = corpus_design("mango para lima.f3d")
    cut = dataclasses.replace(whole, features=[f for f in whole.features if f.ordinal in (1, 5)])
    nested = _placed_as(_placed_as(cut, "File Handle", transform=quarter), "Collet", transform=along_x)
    with Engine(tools.solidcraft_cli, [tmp_path]) as eng:
        eng.call("new_design", {})
        result, ctx = replay(nested, eng)
        placed = _placements(eng)
        # `Sketch 2` is File Handle's own; `Sketch1` is one of Collet's, at its component origin.
        planes = _planes(eng)
    assert result.component_warnings == []
    # Each occurrence carries its own placement, local to its parent, as the file states it.
    assert placed["File Handle"][3][:3] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    assert placed["Collet"][3][:3] == pytest.approx([10.0, 0.0, 0.0], abs=1e-9)
    # And the sketch planes stay at the file's own coordinates inside their component, as ever.
    assert planes["Sketch1"] == [0.0, 0.0, 0.0]
    # The composition itself: Collet's origin in the world, which is the chain applied outside-in.
    composed = world_transform(ctx, next(c.id for c in ctx.design.components.values() if c.name == "Collet"))
    assert [row[3] for row in composed] == pytest.approx([0.0, 10.0, 0.0], abs=1e-9)
