"""Fusion's colours on the rebuilt bodies, and a rebuilt body identical to its original kept native.

Spec 0.4, items 3 and 4, which go together: a body that stays native loses the paint the import's
STEP carries, and the binding in the file is what gives it back.
"""

import copy
import json
import os
from pathlib import Path

import pytest

from f3d2solidcraft.cli import main
from f3d2solidcraft.engine import Engine
from f3d2solidcraft.paint import hex_colour

CORPUS = Path(os.environ.get("F3D2SC_CORPUS", "~/.f3d2solidcraft/corpus/propios")).expanduser()


def test_a_colour_becomes_the_hex_the_engine_takes():
    assert hex_colour((0.9639762, 0.9639762, 0.9546872, 1.0)) == "#f6f6f3"
    assert hex_colour((0.6262097, 0.6262097, 0.6262097, 1.0)) == "#a0a0a0"
    assert hex_colour((1.2, -0.1, 0.0, 1.0)) == "#ff0000"  # out of range is clamped, not wrapped


def test_the_sample_binds_one_colour_to_each_body(design):
    assert len(design.paints) == 5
    assert {p.body for p in design.paints} == {b.id for b in design.bodies}
    assert all(p.face is None for p in design.paints)
    assert all(p.rgba == pytest.approx((0.6262097, 0.6262097, 0.6262097, 1.0)) for p in design.paints)


def _looks(tools, path: Path) -> dict[str, tuple[str | None, str | None]]:
    """Each body of the saved design: (which step of its appearance chain is active, that colour)."""
    with Engine(tools.solidcraft_cli, [path.parent]) as eng:
        eng.call("open", {"path": str(path)})
        looks = {}
        for name in eng.body_names():
            chain = eng.execute("appearance.chain", {"target": {"body": name}})
            active = next((s for s in chain["steps"] if s["step"] == chain["active"]), None)
            looks[name] = (chain["active"], ((active or {}).get("look") or {}).get("color"))
    return looks


def test_the_rebuilt_bodies_of_the_sample_carry_their_fusion_colour(sample_f3d, tools, tmp_path):
    """The 2 bodies the sample rebuilds natively get their binding's colour; the imported one keeps
    the STEP's paint, which is not one of the chain's steps."""
    out, report_path = tmp_path / "box.solidcraft", tmp_path / "box.report.json"
    assert main(["convert", str(sample_f3d), "-o", str(out), "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["bodies"] == {"native": 2, "imported": 1}
    looks = _looks(tools, out)
    assert sorted(looks.values(), key=str) == [("body", "#a0a0a0"), ("body", "#a0a0a0"), (None, None)]


@pytest.fixture(scope="module")
def sample_cadir(sample_f3d, tools, tmp_path_factory) -> dict:
    """The sample's own CADIR dump, as cadmpeg writes it: 6 appearances and 5 body bindings."""
    from f3d2solidcraft.reader.cadir import dump_cadir

    out = dump_cadir(tools.cadmpeg, sample_f3d, tmp_path_factory.mktemp("paint") / "s.cadir.json")
    return json.loads(out.read_text())


def _bound_and_spare(model: dict) -> tuple[int, int]:
    """The index of the appearance the 5 bindings name, and of one with a colour that none names."""
    named = {b["appearance"] for b in model["appearance_bindings"]}
    bound = next(i for i, a in enumerate(model["appearances"]) if a["id"] in named)
    spare = next(
        i
        for i, a in enumerate(model["appearances"])
        if a["id"] not in named and isinstance(a.get("base_color"), dict)
    )
    return bound, spare


def _corrupt_spare_not_a_dict(model):
    # Declared modification: the unbound appearance is replaced by a string.
    model["appearances"][_bound_and_spare(model)[1]] = "not an appearance"


def _corrupt_spare_without_id(model):
    # Declared modification: the unbound appearance loses its id.
    del model["appearances"][_bound_and_spare(model)[1]]["id"]


def _corrupt_spare_null_channel(model):
    # Declared modification: the unbound appearance's red channel is null.
    model["appearances"][_bound_and_spare(model)[1]]["base_color"]["r"] = None


def _corrupt_first_binding_not_a_dict(model):
    # Declared modification: the first binding is replaced by null.
    model["appearance_bindings"][0] = None


def _corrupt_first_binding_target_without_id(model):
    # Declared modification: the first binding's target loses its id.
    del model["appearance_bindings"][0]["target"]["id"]


def _corrupt_first_binding_null_target(model):
    # Declared modification: the first binding's target is null.
    model["appearance_bindings"][0]["target"] = None


@pytest.mark.parametrize(
    ("corrupt", "kept"),
    [
        (_corrupt_spare_not_a_dict, 5),
        (_corrupt_spare_without_id, 5),
        (_corrupt_spare_null_channel, 5),
        (_corrupt_first_binding_not_a_dict, 4),
        (_corrupt_first_binding_target_without_id, 4),
        (_corrupt_first_binding_null_target, 4),
    ],
)
def test_a_malformed_appearance_record_is_skipped_and_the_rest_come_through(sample_cadir, corrupt, kept):
    """One record of the sample's real dump is corrupted per case; the parse never raises."""
    from f3d2solidcraft.reader.cadir import parse_cadir

    data = copy.deepcopy(sample_cadir)
    corrupt(data["model"])
    paints = parse_cadir(data).paints
    assert len(paints) == kept
    assert all(p.rgba == pytest.approx((0.6262097, 0.6262097, 0.6262097, 1.0)) for p in paints)


@pytest.mark.parametrize(
    "channel_value",
    [None, "0.5", True],
    ids=["null", "text", "bool"],
)
@pytest.mark.parametrize("channel", ["r", "g", "b"])
def test_a_bound_colour_without_a_numeric_channel_paints_nothing(sample_cadir, channel, channel_value):
    """Declared modification of the sample's real dump: one channel of the one bound colour is
    corrupted. Its 5 bindings are then skipped — never painted white by a default."""
    from f3d2solidcraft.reader.cadir import parse_cadir

    data = copy.deepcopy(sample_cadir)
    bound, _ = _bound_and_spare(data["model"])
    data["model"]["appearances"][bound]["base_color"][channel] = channel_value
    assert parse_cadir(data).paints == ()


def test_a_bound_colour_without_a_channel_paints_nothing(sample_cadir):
    """Declared modification of the sample's real dump: the bound colour loses its green channel."""
    from f3d2solidcraft.reader.cadir import parse_cadir

    data = copy.deepcopy(sample_cadir)
    bound, _ = _bound_and_spare(data["model"])
    del data["model"]["appearances"][bound]["base_color"]["g"]
    assert parse_cadir(data).paints == ()


def test_a_null_alpha_is_read_as_opaque(sample_cadir):
    """Declared modification of the sample's real dump: the bound colour's alpha is null."""
    from f3d2solidcraft.reader.cadir import parse_cadir

    data = copy.deepcopy(sample_cadir)
    bound, _ = _bound_and_spare(data["model"])
    data["model"]["appearances"][bound]["base_color"]["a"] = None
    paints = parse_cadir(data).paints
    assert len(paints) == 5
    assert all(p.rgba[3] == 1.0 for p in paints)


@pytest.mark.parametrize("key", ["appearances", "appearance_bindings"])
def test_a_null_appearance_list_does_not_stop_the_parse(sample_cadir, key):
    """Declared modification of the sample's real dump: one of the two lists is null."""
    from f3d2solidcraft.reader.cadir import parse_cadir

    data = copy.deepcopy(sample_cadir)
    data["model"][key] = None
    parts = parse_cadir(data)
    assert parts.paints == ()
    assert len(parts.bodies) == 5
