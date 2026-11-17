"""Rewrite Spanish Fusion feature labels so cadmpeg and ezf3d can type the features.

Both decoders type a feature by its visible label and know no Spanish aliases. The labels sit in
the design segment's streams as UTF-16LE; swapping each for an English label of the same length
keeps every offset in place, so the rewritten copy decodes exactly like an English file.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from f3d2solidcraft.reader.fusion import design_streams, open_archive

LABELS: dict[str, str] = {"Boceto": "Sketch", "Extruir": "Extrude", "Chaflán": "Chamfer"}
# Spanish labels cadmpeg leaves untyped for want of a same-length English spelling of their
# family. cadmpeg does type features from localized names — its table offers Congé, Abrundung and
# Arredondamento for Fillet, Schale for Shell, Maßstab for Scale, and more — but it carries no
# Spanish, and none of the aliases it does carry is the length of the Spanish label, so no in-place
# rewrite can reach them: Empalme (7) against Fillet 6 / Congé 5 / Abrundung 9 / Arredondamento 14,
# PlanoDeObra (11) against WorkPlane 9, Escala (6) against Scale 5 / Maßstab 7, Solevación (10)
# against Loft 4, Vaciado (7) against Shell 5 / Schale 6.
# Padding an alias to the Spanish length does not reach them either, and that is measured rather
# than assumed: rewriting `Empalme` as `"Fillet "` makes cadmpeg report the family verbatim as
# `"Fillet "` with `{"definition": "native"}` — the label is not trimmed, the table is not hit and
# the recipe stays opaque, where the same file's `Chaflán` -> `Chamfer` comes back
# `{"definition": "chamfer", "groups": [{"edges": …, "spec": {"distance": 0.2}}]}`. So an alias
# table on cadmpeg's side is the only way in (its #447), not a cleverer rewrite on ours.
# `Lienzo` and `EsferaPrimitiva` are left out on purpose: Canvas and SpherePrimitive do match them
# in length, but cadmpeg gives those kinds no family, so the swap would name the feature and still
# leave it without a recipe — and we map Lienzo ourselves.
#
# **An untranslated label does not mean the feature is lost.** The warning used to say those
# features "stay unmapped", and measured over the corpus that is false for 9 of the 13 it covers:
# cadmpeg types a feature's *definition* even where its label stays Spanish, and section 82's
# routing takes it from there.
#
# | label | definición de cadmpeg | ¿lo pierde el label? |
# | --- | --- | --- |
# | `Empalme` x8 | `fillet` | no |
# | `Solevación` x1 | `loft` | no |
# | `PlanoDeObra` x2 | `native` | sí |
# | `Escala` x1 | `native` | sí |
# | `Vaciado` x1 | `native` | sí |
#
# So the warning says what is true of all five: the label reaches the decoder untranslated, and
# whether its features map depends on the definition (spec section 113).
UNTRANSLATED: tuple[str, ...] = ("Empalme", "PlanoDeObra", "Escala", "Solevación", "Vaciado")

# Sketch labels cadmpeg carries no spelling for, so it hands the feature back `native` and the
# rewrite above cannot reach them either (`スケッチ` is 4 UTF-16 units against Sketch 6, Skizze 6,
# Esboço 6 and Esquisse 8 — §48 has the arithmetic). The family is all that is missing: cadmpeg
# decodes the sketch records themselves whatever the label says, so naming the family here is
# enough for `map_sketch` to run, once `cadir` works out which record the feature holds.
#
# Only what the corpus proves. `Boceto` is deliberately absent: the in-place rewrite reaches it
# first, and it buys that file's extrudes as well, which this path cannot.
SKETCH_LABELS: frozenset[str] = frozenset({"スケッチ"})

for _es, _en in LABELS.items():
    assert len(_es) == len(_en), f"{_es} and {_en} must have the same UTF-16 length"


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def normalize_labels(f3d: Path, workdir: Path) -> tuple[Path, dict[str, int], list[str]]:
    """A copy of f3d with English labels in its design streams, or f3d itself when none is Spanish."""
    with open_archive(f3d) as archive:
        _, meta, bulk = design_streams(archive)
        streams = {name: archive.read(name) for name in (meta, bulk)}
        counts = {es: sum(d.count(_utf16(es)) for d in streams.values()) for es in LABELS}
        counts = {es: n for es, n in counts.items() if n}
        untranslated = [es for es in UNTRANSLATED if any(_utf16(es) in d for d in streams.values())]
        warnings = [
            f"Spanish label '{es}' has no same-length English alias, so it reaches the decoder "
            "untranslated; its features map only where cadmpeg types them by definition"
            for es in untranslated
        ]
        if not counts:
            return f3d, {}, warnings
        if f3d.suffix.lower() == ".f3z":
            # The rewrite replaces labels inside the design streams and writes the archive back.
            # Inside a package those streams sit in a member, so putting them back means rebuilding
            # the nested zip too. No package in the corpus has Spanish labels (the one we have is a
            # Japanese design with an English component), so this says so instead of guessing.
            summary = ", ".join(f"{es}\u00d7{n}" for es, n in counts.items())
            return (
                f3d,
                {},
                [
                    *warnings,
                    f"Spanish labels in a .f3z are not normalized ({summary}); those features stay unmapped",
                ],
            )
        for name, data in streams.items():
            for es in counts:
                data = data.replace(_utf16(es), _utf16(LABELS[es]))
            streams[name] = data
        out = workdir / "normalized.f3d"
        try:
            # Python's zipfile cannot write Fusion's zstd entries; stored entries read back the same.
            # Mode "w" truncates a leftover copy from an earlier run.
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as copy:
                for directory in archive.directories():
                    copy.writestr(directory, b"")
                for entry in archive.entries():
                    data = streams[entry.name] if entry.name in streams else archive.read(entry.name)
                    copy.writestr(entry.name, data)
        except BaseException:
            out.unlink(missing_ok=True)
            raise
    summary = ", ".join(f"{es}×{n}" for es, n in counts.items())
    return out, counts, [f"Spanish Fusion labels normalized for decoding: {summary}", *warnings]
