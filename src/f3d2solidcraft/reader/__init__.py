"""Read a Fusion design into the neutral model."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from pathlib import Path

from f3d2solidcraft.model import Design
from f3d2solidcraft.reader.cadir import dump_cadir, parse_cadir
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
            sketches[f.sketch] = dataclasses.replace(sketches[f.sketch], dimensions=owned)
        features.append(
            dataclasses.replace(
                f,
                name=_restore_label(f.name, substituted),
                extrude=extrude,
                parameters=tuple(p.name for p in owned),
            )
        )
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
