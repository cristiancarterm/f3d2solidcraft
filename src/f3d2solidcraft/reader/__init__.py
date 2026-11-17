"""Read a Fusion design into the neutral model."""

from __future__ import annotations

import dataclasses
import json
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
    aligned = fusion.feature_count == len(cad.features)
    if not aligned:
        warnings.append(
            f"timeline mismatch: ezf3d reads {fusion.feature_count} features, cadmpeg "
            f"{len(cad.features)}; parameters are not attached to features"
        )
    features = []
    sketches = dict(cad.sketches)
    for f in cad.features:
        owned = fusion.by_feature.get(f.ordinal, ()) if aligned else ()
        extrude = f.extrude
        if extrude is not None:
            along = next((p.name for p in owned if p.role == "AlongDistance"), None)
            extrude = dataclasses.replace(extrude, distance_param=along)
        if aligned and f.sketch in sketches:
            sketches[f.sketch] = dataclasses.replace(
                sketches[f.sketch], dimensions=fusion.by_sketch.get(f.ordinal, ())
            )
        features.append(
            dataclasses.replace(
                f,
                name=_restore_label(f.name, substituted),
                extrude=extrude,
                parameters=tuple(p.name for p in owned),
            )
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
    )
