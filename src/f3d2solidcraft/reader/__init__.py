"""Read a Fusion design into the neutral model."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from pathlib import Path

from f3d2solidcraft.model import Design, Parameter
from f3d2solidcraft.reader.cadir import CadirParts, _foreign, dump_cadir, parse_cadir
from f3d2solidcraft.reader.fusion import read_fusion
from f3d2solidcraft.reader.locale import LABELS, normalize_labels


def _restore_label(name: str, substituted: dict[str, str]) -> str:
    """Give a feature back the Spanish label the user saw ("Extrude 3" -> "Extruir 3")."""
    # Limitation: a name the user typed in English ("Extrude 3") in a Spanish file also gets the Spanish label.
    for en, es in substituted.items():
        if name.startswith(en):
            return es + name[len(en) :]
    return name


def _same_label(record: str, tag: str) -> bool:
    """Whether ezf3d's record and cadmpeg's feature name the same kind of operation.

    The record carries the label Fusion writes (`Extrude`, `Base Feature`) and cadmpeg's `source_tag`
    is that same label; spaces are ignored because the record of `OffsetFaces` and its id spell it
    without one. Measured over the 13 `.f3d` of the corpus and the sample: equal in every pairing.
    """
    return record.replace(" ", "") == tag.replace(" ", "")


def _sketch_dimensions(
    cad: CadirParts, parameters: list[Parameter], positional: dict[str, tuple[Parameter, ...]]
) -> dict[str, tuple[Parameter, ...]]:
    """The parameters each sketch owns: by cadmpeg's owner, and by ezf3d's position where only it sees one.

    ezf3d gives a parameter to the nearest feature record before it, which is right for most and
    wrong for a dimension added after later features: that one lands on an Extrude, a Mirror or
    another sketch. Measured over the corpus's 13 `.f3d`, 43 of 157 sketch dimensions did, and
    cadmpeg's `model.parameters[].owner` names the right sketch feature for all 43 — so where the file
    lists a parameter, its owner places it.

    A parameter cadmpeg does not list at all stays where ezf3d puts it, because ezf3d is then the
    only reader that sees it: `corpus-04`'s `d5` and `d6` (`10.00 mm`) and `corpus-03`'s `asdf`,
    `docklength`, `dockphoneheight`, `topoverage` and `d325` are such. Only the root's own list counts:
    a merged `.f3z` member's parameters (`f3d:xref/…` ids) are another document's, and a name there
    says nothing about the root's parameter of that name. A file whose dump has no `model.parameters` keeps the positional rule
    whole, as before.
    """
    if not cad.parameters:
        return dict(positional)
    listed = {p.name for p in cad.parameters.values() if not _foreign(p.id)}
    by_name = {p.name: p for p in parameters}
    out: dict[str, tuple[Parameter, ...]] = {}
    for sid in cad.sketches:
        owned = [by_name[name] for name in cad.owned.get(sid, ()) if name in by_name]
        unlisted = [p for p in positional.get(sid, ()) if p.name not in listed]
        out[sid] = tuple(owned + unlisted)
    return out


def read_design(f3d: Path, cadmpeg: Path, workdir: Path) -> Design:
    decodable, counts, warnings = normalize_labels(f3d, workdir)
    try:
        cad = parse_cadir(
            json.loads(
                dump_cadir(cadmpeg, decodable, workdir / "design.cadir.json").read_text(encoding="utf-8")
            )
        )
        fusion = read_fusion(decodable)
    finally:
        if decodable != f3d:
            decodable.unlink(missing_ok=True)
    substituted = {LABELS[es]: es for es in counts}
    if cad.not_features:
        warnings.append(
            f"{len(cad.not_features)} record(s) of the features array are not timeline features "
            f"and were skipped: they carry no name ({', '.join(cad.not_features[:3])})"
        )
    # Each feature is paired with its parameter record by the timeline item cadmpeg's id ends in,
    # and only where the record's label is the feature's: a pairing the two readers do not agree on
    # leaves the feature without parameters, counted, rather than bound to the wrong numbers.
    claims = Counter(f.item for f in cad.features if f.item is not None)
    features = []
    sketches = dict(cad.sketches)
    positional: dict[str, tuple[Parameter, ...]] = {}
    unpaired: list[str] = []
    for f in cad.features:
        record = fusion.by_item.get(f.item) if f.item is not None and claims[f.item] == 1 else None
        paired = record is not None and _same_label(record[0], f.tag)
        owned = record[1] if paired else ()
        if not paired:
            unpaired.append(f.name)
        extrude = f.extrude
        if extrude is not None:
            along = next((p.name for p in owned if p.role == "AlongDistance"), None)
            extrude = dataclasses.replace(extrude, distance_param=along)
        if paired and f.sketch in sketches:
            positional[f.sketch] = owned
        features.append(
            dataclasses.replace(
                f,
                name=_restore_label(f.name, substituted),
                extrude=extrude,
                parameters=tuple(p.name for p in owned),
            )
        )
    for sid, dimensions in _sketch_dimensions(cad, fusion.parameters, positional).items():
        sketches[sid] = dataclasses.replace(sketches[sid], dimensions=dimensions)
    if unpaired:
        warnings.append(
            f"{len(unpaired)} of {len(cad.features)} feature(s) could not be paired with a parameter "
            f"record of the file, so they carry no parameters ({', '.join(unpaired[:3])})"
        )
    return Design(
        source=str(f3d),
        parameters=fusion.parameters,
        sketches=sketches,
        features=features,
        bodies=cad.bodies,
        warnings=warnings,
        components=cad.components,
        occurrences=cad.occurrences,
        unpaired=[_restore_label(name, substituted) for name in unpaired],
        paints=list(cad.paints),
    )
