import json
import os
from collections import Counter
from pathlib import Path

import pytest

from f3d2solidcraft.reader.cadir import dump_cadir, parse_cadir
from f3d2solidcraft.reader.fusion import (
    _parameters_of,
    _unread_table_entries,
    design_streams,
    open_archive,
    read_fusion,
)


@pytest.fixture(scope="session")
def cadir(sample_f3d, tools, tmp_path_factory):
    out = dump_cadir(tools.cadmpeg, sample_f3d, tmp_path_factory.mktemp("cadir") / "s.cadir.json")
    return parse_cadir(json.loads(out.read_text()))


def test_cadir_timeline(cadir):
    tags = Counter(f.tag for f in cadir.features)
    assert tags == {"Sketch": 8, "Extrude": 10, "SurfaceDeleteFace": 8}
    assert [f.ordinal for f in cadir.features] == list(range(26))


def test_cadir_extrudes(cadir):
    by_name = {f.name: f for f in cadir.features}
    e1 = by_name["Extrude 1"].extrude
    # cadmpeg 0.6.8 describes this one as a region, not as a profile index: `sketch_regions` with
    # one **trimmed** region — a boundary of entity segments rather than loops of the sketch, so it
    # names neither a loop to translate nor an index to resolve. What the boundary itself says, and
    # the translation it carries, are in `test_trimmed_region` (spec sections 95 and 112).
    assert e1.profile_kind == "sketch_regions"
    assert e1.profiles == () and e1.regions == () and e1.trimmed_regions == 1
    assert len(e1.trimmed_boundaries) == 1 and len(e1.trimmed_boundaries[0]) == 5
    assert e1.op == "new_body"
    assert e1.extent_kind == "one_sided"
    assert e1.length_mm == pytest.approx(50.0)
    assert e1.reversed is False
    e5 = by_name["Extrude 5"].extrude
    assert e5.reversed is True and e5.length_mm == pytest.approx(15.0)
    assert by_name["Extrude 7"].extrude.profile_kind == "historical_faces"


def test_cadir_sketches(cadir):
    assert len(cadir.sketches) == 8
    s1 = cadir.sketches[cadir.features[0].sketch]
    assert s1.frame.normal == (0.0, 0.0, 1.0)
    assert len(s1.profiles) == 4
    kinds = Counter(e.kind for s in cadir.sketches.values() for e in s.entities)
    assert kinds == {"point": 32, "line": 20, "circle": 4}
    assert len(cadir.bodies) == 5


def test_fusion_parameters(sample_f3d):
    parts = read_fusion(sample_f3d)
    assert len(parts.parameters) == 32
    d19 = {p.name: p for p in parts.parameters}["d19"]
    assert d19.expression == "-15.00 mm"
    assert d19.value_mm == pytest.approx(-15.0)
    assert [p.name for p in parts.by_feature[1]] == ["d5", "d6"]
    assert [p.name for p in parts.by_sketch[0]] == ["d1", "d2", "d3", "d4"]
    assert parts.feature_count == 26


def test_read_design_merges_sources(design):
    by_name = {f.name: f for f in design.features}
    assert by_name["Extrude 1"].extrude.distance_param == "d5"
    assert by_name["Extrude 1"].parameters == ("d5", "d6")
    first_sketch = design.sketches[design.features[0].sketch]
    assert [p.name for p in first_sketch.dimensions] == ["d1", "d2", "d3", "d4"]
    assert design.warnings == []


def test_english_file_is_not_rewritten(sample_f3d, tmp_path):
    from f3d2solidcraft.reader.locale import normalize_labels

    path, counts, warnings = normalize_labels(sample_f3d, tmp_path)
    assert path == sample_f3d
    assert counts == {}
    assert warnings == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "regions",
    [
        [{"region": "loops"}],
        [{"region": "loops", "outer": "x", "holes": []}],
        [{"region": "loops", "outer": 1, "holes": None}],
        [{"region": "loops", "outer": 1, "holes": ["a"]}],
        [{"region": "faces", "outer": 1, "holes": []}],
        [{"region": "loops", "outer": -1, "holes": []}],
        "loops",
    ],
)
def test_malformed_regions_are_empty(regions):
    from f3d2solidcraft.reader.cadir import _regions

    assert _regions({"regions": regions}) == ()


def test_two_distance_chamfer_keeps_its_angle_in_radians(corpus_design):
    design = corpus_design("mango para lima.f3d")
    chamfers = [f.edge_op for f in design.features if f.tag == "Chamfer"]
    assert len(chamfers) == 2
    for chamfer in chamfers:
        assert chamfer.kind == "chamfer"
        assert chamfer.value == pytest.approx(1.0)
        # cadmpeg calls it "second" but it is an angle in radians, not a distance. This test used
        # to assert 0.6981317 — 40° — because that is what 0.6.7 reported, and 0.6.7 was ten times
        # off: the file's own parameter for this chamfer reads `"4 deg"` and `"4.0 deg"`, and the
        # value below is 4.000000° (spec section 95).
        assert chamfer.angle_rad == pytest.approx(0.06981317007977318)


def test_fillets_sharing_a_name_each_carry_their_own_spec(corpus_design):
    # Three distinct Fillet features of this file are all named "Fillet 3" (different occurrences),
    # so a {name: feature} map silently drops two of them; later tasks must key by ordinal.
    design = corpus_design("Caja parametrica v2.f3d")
    fillets = [f for f in design.features if f.tag == "Fillet"]
    assert [f.name for f in fillets] == ["Fillet 3", "Fillet 3", "Fillet 3"]
    assert all(f.edge_op is not None and f.edge_op.kind == "fillet" for f in fillets)
    assert sorted(f.edge_op.value for f in fillets) == [
        pytest.approx(0.75),
        pytest.approx(4.6),
        pytest.approx(4.6),
    ]


MALFORMED_EDGE_OPS = [
    {"definition": "fillet", "groups": "nope"},
    {"definition": "fillet", "groups": [None]},
    {"definition": "fillet", "groups": []},
    # several groups means several sizes at once: not mapped in M1
    {
        "definition": "fillet",
        "groups": [
            {"radius": {"kind": "constant", "radius": 4.0}},
            {"radius": {"kind": "constant", "radius": 1.0}},
        ],
    },
    {"definition": "fillet", "groups": [{"radius": 4.0}]},  # non-dict radius
    {"definition": "fillet", "groups": [{"radius": {"kind": "constant"}}]},  # no radius value
    {"definition": "fillet", "groups": [{"radius": {"kind": "constant", "radius": "4"}}]},
    {"definition": "fillet", "groups": [{"radius": {"kind": "constant", "radius": None}}]},
    {"definition": "fillet", "groups": [{"radius": {"kind": "variable", "radius": 4.0}}]},
    {"definition": "chamfer", "groups": [{"spec": "distance"}]},  # non-dict spec
    {"definition": "chamfer", "groups": [{"spec": {"kind": "distance"}}]},  # no distance value
    {"definition": "chamfer", "groups": [{"spec": {"kind": "distance", "distance": None}}]},
    {"definition": "chamfer", "groups": [{"spec": {"kind": "two_distances", "first": 1.0}}]},
    {
        "definition": "chamfer",
        "groups": [{"spec": {"kind": "two_distances", "first": 1.0, "second": "0.7"}}],
    },
    {"definition": "chamfer", "groups": [{}]},
    {"definition": "extrude"},
    {},
]


@pytest.mark.parametrize(
    "value",
    [
        True,  # a bool is an int in Python: float(True) == 1.0 would read as a 1 mm size
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        "4",
        None,
        [4.0],
        {"radius": 4.0},
    ],
)
def test_non_finite_and_non_numeric_values_are_rejected(value):
    """`_number` exists to reject exactly these, so each one is pinned."""
    from f3d2solidcraft.reader.cadir import _number

    assert _number(value) is None


@pytest.mark.parametrize(("value", "expected"), [(4, 4.0), (4.6, 4.6), (0, 0.0), (-1.5, -1.5)])
def test_finite_numbers_come_back_as_floats(value, expected):
    from f3d2solidcraft.reader.cadir import _number

    assert _number(value) == pytest.approx(expected)


@pytest.mark.parametrize("defn", MALFORMED_EDGE_OPS)
def test_malformed_edge_ops_are_unmapped(defn):
    from f3d2solidcraft.reader.cadir import _edge_op

    assert _edge_op(defn) is None


def test_malformed_edge_set_still_reads_the_size_without_a_count():
    from f3d2solidcraft.reader.cadir import _edge_op

    op = _edge_op(
        {
            "definition": "fillet",
            "groups": [
                {
                    "radius": {"kind": "constant", "radius": 4.0},
                    "edges": {"kind": "historical", "value": "not-a-dict"},
                }
            ],
        }
    )
    assert op.value == pytest.approx(4.0)
    assert op.edge_count is None


@pytest.mark.parametrize(
    ("value", "groups"),
    [
        ({}, {}),
        ({"selections": "g1"}, {"g1": {"members": [1]}}),  # not a list
        ({"selections": ["g1"]}, {}),  # unknown group
        ({"selections": [["g1"]]}, {"g1": {"members": [1]}}),  # unhashable group id
        ({"selections": ["g1"]}, {"g1": "nope"}),  # non-dict group
        ({"selections": ["g1"]}, {"g1": {}}),  # no members
        ({"selections": ["g1"]}, {"g1": {"members": None}}),  # members not a list
        ({"selections": ["g1", "g2"]}, {"g1": {"members": [1]}}),  # one unknown spoils the count
    ],
)
def test_malformed_selections_count_zero(value, groups):
    from f3d2solidcraft.reader.cadir import _selection_count

    assert _selection_count(value, groups) == 0


def test_selection_count_sums_the_members_of_every_group():
    from f3d2solidcraft.reader.cadir import _selection_count

    groups = {"g1": {"members": [1, 2]}, "g2": {"members": [3]}}
    assert _selection_count({"selections": ["g1", "g2"]}, groups) == 3


@pytest.mark.parametrize(
    "native", [None, {}, {"f3d": None}, {"f3d": {"design_extrude_selection_groups": None}}]
)
def test_parse_cadir_survives_a_missing_selection_group_index(native):
    from f3d2solidcraft.reader.cadir import parse_cadir

    assert parse_cadir({"model": {}, "native": native}).features == []


def test_selection_groups_without_an_id_are_skipped_instead_of_raising():
    from f3d2solidcraft.reader.cadir import parse_cadir

    data = {
        "model": {
            "features": [
                {
                    "ordinal": 0,
                    "name": "Extrude 1",
                    "definition": {
                        "definition": "extrude",
                        "profile": {"kind": "sketch_selection", "value": {"selections": ["g1"]}},
                    },
                }
            ]
        },
        "native": {"f3d": {"design_extrude_selection_groups": [{"members": [1]}, "nope"]}},
    }
    assert parse_cadir(data).features[0].extrude.selection_count == 0


def test_parameters_are_read_whichever_record_preamble_the_document_uses(tools, sample_f3d, tmp_path):
    """The public sample puts a parameter's expression at +31; the corpus's designs put it at +35.

    ezf3d assumes 31, so nine of the ten corpus designs read 0 parameters where 28 and 32 exist —
    the reader lands on a zero length, takes the empty string for the expression and the expression
    for the role, and the library's own name check then files every record as unreadable. Probing is
    safe for exactly that reason: a wrong preamble cannot publish a parameter.
    """
    from f3d2solidcraft.reader import read_design

    design = read_design(sample_f3d, tools.cadmpeg, tmp_path)
    by_name = {p.name: p for p in design.parameters}
    assert len(design.parameters) == 32, len(design.parameters)
    assert by_name["d1"].expression == "50 mm", by_name["d1"]


def test_components_and_occurrences_are_read(corpus_design):
    """Fusion's component tree. `mango para lima` is the corpus's nested case.

    Collet sits inside File Handle's occurrence, not beside it, so the occurrences form a chain
    root -> File Handle -> Collet rather than a flat list under the root.
    """
    design = corpus_design("mango para lima.f3d")
    by_name = {c.name: c for c in design.components.values()}
    assert set(by_name) == {None, "Collet", "File Handle"}
    # One body each under cadmpeg 0.6.8, where 0.6.7 gave `Collet` two and `File Handle` none: the
    # same bodies, attributed differently (spec section 95).
    assert len(by_name["Collet"].bodies) == 1 and len(by_name["Collet"].sketches) == 4
    assert len(by_name["File Handle"].bodies) == 1 and len(by_name["File Handle"].sketches) == 2
    placed = {design.components[o.component].name: o for o in design.occurrences}
    assert placed[None].parent is None  # the root of the design
    assert placed["File Handle"].parent == placed[None].id
    assert placed["Collet"].parent == placed["File Handle"].id
    # Parents come before children: a rebuild can create each component inside one that exists.
    order = [design.components[o.component].name for o in design.occurrences]
    assert order.index("File Handle") < order.index("Collet")


def test_a_single_component_design_reads_no_component_tree(design):
    """A design Fusion kept in its root component declares neither components nor occurrences."""
    assert design.components == {} and design.occurrences == []


CORPUS_FILES = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def _segment_of(name: str):
    path = CORPUS_FILES / name
    if not path.exists():
        pytest.skip(f"private corpus file not found: {path}")
    with open_archive(path) as archive:
        segment, _meta, _bulk = design_streams(archive)
    return segment


def test_the_user_parameter_records_ezf3d_cannot_read_are_read_here():
    """A user parameter's record is not laid out like a model parameter's, and ezf3d drops it.

    It walks the name table — every name the design has — and then reads each record at a fixed
    offset from its start. A model parameter's expression sits at +35 in these documents; a user
    parameter's record opens with the designer's comment and its expression follows at an offset
    that moves with that comment, so the read lands on the wrong string and the record is filed
    unreadable: 19 of the 109 names `Caja parametrica v2` declares.

    What the two layouts share is the tail — `… expression, role, unit, name` and the value right
    after the name — so anchoring on the name, which has to be what the table filed the record
    under, reads them without assuming any offset. Both checks are asserted here: every rescued
    record names itself, and every literal expression converts to the value stored with it.
    """
    segment = _segment_of("Caja parametrica v2.f3d")
    read = _parameters_of(segment)
    assert (len(read.values), read.declared) == (90, 109)
    assert len(read.unreadable) == 19
    rescued = _unread_table_entries(segment, read)
    assert len(rescued) == 19
    assert {p.name for p in rescued} == set(read.unreadable)
    assert {p.role for p in rescued} == {"User Parameter"}
    by_name = {p.name: p for p in rescued}
    assert (by_name["CaseExteriorWidth"].expression, by_name["CaseExteriorWidth"].value_mm) == (
        "150 mm",
        150.0,
    )
    # the angle the witness rule could not reach: the file gives it no length, and it is read anyway
    assert by_name["LatchCutoutAngle"].unit == "deg"
    assert by_name["LatchCutoutAngle"].value_mm is None
    # and the formulas, as written
    assert by_name["X_ComputedHingeThickness"].expression == "max(HingeDiameter / 2 + 1.5 mm; HingeThickness)"
