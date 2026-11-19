"""Read parameters and their owners with the vendored ezf3d reader (zstandard only)."""

from __future__ import annotations

import io
import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from f3d2solidcraft._vendor.ezf3d.container.archive import F3DArchive
from f3d2solidcraft._vendor.ezf3d.model import parameters as _parameters
from f3d2solidcraft._vendor.ezf3d.model.parameters import (
    LITERAL_RE,
    UNIT_FACTORS,
    Parameters,
    find_table,
    read_parameters,
)
from f3d2solidcraft._vendor.ezf3d.model.sketch import read_sketches
from f3d2solidcraft._vendor.ezf3d.model.timeline import attribute, read_feature, read_timeline
from f3d2solidcraft._vendor.ezf3d.streams.segment import Segment, read_segment
from f3d2solidcraft.model import Parameter

_META = "/MetaStream.dat"

# Where a parameter record's length-prefixed expression starts. ezf3d assumes 31 and every design of
# Cristián's corpus puts it at 35: four more bytes of leading zeros, and the byte that reads 05 in
# the public sample reads 04 there. At 31 the reader lands on a zero length, takes the empty string
# as the expression and the expression as the role, and the name check inside the library then
# rejects the record — so 9 of 10 designs read 0 parameters where 28 and 32 exist.
#
# That same name check is what makes probing safe: a wrong preamble cannot publish a parameter, it
# can only fill `Parameters.unreadable`. Measured: the sample reads 32 of 32 at 31 and none at 35;
# `corpus-04` 28 of 28 and `corpus-01` 32 of 32 at 35, with values matching their
# expressions. The constant is set on the vendored module rather than edited into it, so
# `scripts/vendor_ezf3d.sh` can still refresh that tree from upstream. Reported as
# docs/upstream/ezf3d-parameter-record-preamble.md.
PREAMBLES = (31, 35)

LENGTH_UNITS = {"mm", "cm", "m", "in", "ft"}

#: Longest string read while walking a record, as in ezf3d's own table walk.
_MAX_STRING = 512


@dataclass
class FusionParts:
    parameters: list[Parameter]
    by_feature: dict[int, tuple[Parameter, ...]]
    by_sketch: dict[int, tuple[Parameter, ...]]
    feature_count: int
    # Timeline item oid -> (the label of the feature record before it, the parameters that record
    # owns). Keyed by the item because that is the one number cadmpeg's feature ids also carry.
    by_item: dict[int, tuple[str, tuple[Parameter, ...]]] = field(default_factory=dict)


def _convert(p) -> Parameter:
    # ezf3d keeps Fusion's internal units: centimetres for every length.
    value_mm = float(p.value) * 10.0 if p.unit in LENGTH_UNITS else None
    return Parameter(p.name, p.expression, p.role, p.unit, value_mm)


def open_archive(source: Path) -> F3DArchive:
    """The design archive of a `.f3d`, or of the root member inside a `.f3z` package.

    A `.f3z` is a ZIP of `.f3d` members: `Manifest.json` names the root and `DesignDescription.json`
    is the XREF graph of the rest. Only the root carries the design's own timeline — ezf3d reads a
    member from memory, so nothing is unpacked to disk. cadmpeg reads the package itself and merges
    the referenced members as occurrences, which is why only the root is needed here.
    """
    if source.suffix.lower() != ".f3z":
        return F3DArchive(str(source))
    with zipfile.ZipFile(source) as package:
        root = json.loads(package.read("Manifest.json"))["root"]
        return F3DArchive(io.BytesIO(package.read(root)))


def design_streams(archive: F3DArchive) -> tuple[Segment, str, str]:
    """The design segment and the entry names of its meta and bulk streams."""
    # The folder name varies ("FusionDesignSegmentType1", "Design1"); the meta stream declares the type.
    metas = [e.name for e in archive.entries() if e.name.endswith(_META)]
    metas.sort(key=lambda m: "[Active]" not in m)
    for meta in metas:
        bulk = meta.removesuffix("MetaStream.dat") + "BulkStream.dat"
        segment = read_segment(meta.rsplit("/", 1)[0], archive.read(meta), archive.read(bulk))
        if segment.is_design:
            return segment, meta, bulk
    raise ValueError("no design segment in this archive")


def _parameters_of(segment: Segment) -> Parameters:
    """Parameters read with whichever known record preamble this document uses (see `PREAMBLES`).

    Only the preamble is probed, and that is safe because ezf3d's name check rejects a record read
    at the wrong one. The rest of the record shifts by the same four bytes, and `NUMBER_AT` is *not*
    covered by that check: at preamble 35 it would have to move 16 -> 20, and left at 16 it reads a
    wrong `number` for every record with nothing to signal it (measured: 0 of 28 and 0 of 32 agree
    with the digits of their own auto name, against all of them at 20). We drop `number` in
    `_convert`, so nothing here depends on it — read it and this has to become a base offset.
    """
    best = Parameters()
    for preamble in PREAMBLES:
        original = _parameters.PREAMBLE
        _parameters.PREAMBLE = preamble
        try:
            found = read_parameters(segment)
        finally:
            _parameters.PREAMBLE = original
        if len(found) > len(best):
            best = found
        if len(found) and not found.unreadable:
            break
    return best


def _wide_strings(body: bytes, start: int, end: int) -> list[tuple[int, str]]:
    """Every length-prefixed wide string in `body[start:end]`, with its offset."""
    found: list[tuple[int, str]] = []
    pos = start
    while pos + 4 <= end:
        count = struct.unpack_from("<I", body, pos)[0]
        if 0 < count <= _MAX_STRING and pos + 4 + 2 * count <= end:
            raw = body[pos + 4 : pos + 4 + 2 * count]
            if not any(raw[2 * index + 1] for index in range(count)):
                text = raw.decode("utf-16-le")
                if all(char >= " " for char in text):
                    found.append((pos, text))
                    pos += 4 + 2 * count
                    continue
        pos += 1
    return found


def _literal_disagrees(expression: str, unit: str, value: float) -> bool:
    """Whether a literal expression converts to something other than the stored value.

    The check ezf3d applies to the records it reads, applied here to the ones it does not: a record
    read at the wrong place would have to name itself *and* carry a value that matches its own
    expression to pass, and a misread does neither.
    """
    match = LITERAL_RE.fullmatch(expression)
    if match is None:
        return False  # a formula: ezf3d does not evaluate one either
    factor = UNIT_FACTORS.get(match.group(2) or unit)
    if factor is None:
        return False
    return abs(float(match.group(1)) / factor - value) > 1e-9 * max(1.0, abs(value))


def _unread_table_entries(segment: Segment, read: Parameters) -> list[Parameter]:
    """The parameters the table names and ezf3d could not read, read here.

    ezf3d walks the name table — "the authoritative list, every name a design has, including any
    the user typed" — and then reads each record at a fixed offset. **A user parameter's record is
    not laid out like a model parameter's**: measured on `Caja parametrica v2`, it opens with the
    designer's comment at +16 and its expression follows at an offset that moves with that comment
    (+69, +113, +231 in three records of one document), where a model parameter has its expression
    at a fixed +35. So those records land in `Parameters.unreadable`: 19 of the 109 names this file
    declares, 12 of 77 in `corpus-03`, 1 of 57 in `mango para lima`.

    What they have in common with a model record is the tail, and that is enough to read them: the
    strings run `comment … expression, role, unit, name` and the `f64` value sits immediately after
    the name. Anchoring on the name — the one string that must equal what the table filed the
    record under — gives the three before it and the value after it, without assuming any offset.
    Strings that follow the value are left alone (one record carries a GUID and two labels there).

    Two checks keep a misread out, the same two ezf3d relies on: the name has to be the table's own
    key, and a literal expression has to convert to the stored value. A record that fails either is
    not published.
    """
    found = find_table(segment)
    if found is None:
        return []
    _table, entries = found
    missing = {name: oid for name, oid in entries if name not in {p.name for p in read.values}}
    if not missing:
        return []
    body = segment.bulk.body
    by_oid = {item.oid: item for item in segment.objects()}
    rescued: list[Parameter] = []
    for name, oid in missing.items():
        item = by_oid.get(oid)
        if item is None:
            continue
        strings = _wide_strings(body, item.offset, item.end)
        at = next((index for index, (_, text) in enumerate(strings) if text == name), None)
        if at is None or at < 3:
            continue
        expression, role, unit = (text for _, text in strings[at - 3 : at])
        end = strings[at][0] + 4 + 2 * len(name)
        if end + 8 > item.end:
            continue
        value = struct.unpack_from("<d", body, end)[0]
        if _literal_disagrees(expression, unit, value):
            continue
        rescued.append(
            Parameter(
                name=name,
                expression=expression,
                role=role,
                unit=unit,
                value_mm=float(value) * 10.0 if unit in LENGTH_UNITS else None,
            )
        )
    return rescued


def _by_item(segment: Segment, raw: Parameters) -> dict[int, tuple[str, tuple[Parameter, ...]]]:
    """Every named feature record, keyed by the timeline item that follows it in the object index.

    ezf3d's own rule (`timeline.py`): a feature is the object right before its timeline item, and a
    parameter belongs to the nearest named feature record before it. The list ezf3d picks as *the*
    timeline is not needed for this, and that is the point: on `corpus-03` it picks a list of 5
    where cadmpeg reads 51, and every one of the 51 still has its record right before its item.
    """
    items = segment.objects()
    body = segment.bulk.body
    records = {e.oid: found for e in items if (found := read_feature(body, e)) is not None}
    owned = attribute(records, raw)
    return {
        items[i + 1].oid: (records[e.oid][0], tuple(_convert(p) for p in owned.get(e.oid, ())))
        for i, e in enumerate(items[:-1])
        if e.oid in records and records[e.oid][0]
    }


def read_fusion(f3d: Path) -> FusionParts:
    with open_archive(f3d) as archive:
        segment, _, _ = design_streams(archive)
    raw = _parameters_of(segment)
    timeline = read_timeline(segment, raw)
    sketches = read_sketches(segment, timeline, raw)
    # The table's own entries first, then the ones ezf3d could not read: a design's parameters are
    # one table, and which record layout a parameter happens to use is not the model's business.
    parameters = [_convert(p) for p in raw] + _unread_table_entries(segment, raw)
    by_feature = {f.index: tuple(_convert(p) for p in f.parameters) for f in timeline}
    by_sketch = {s.index: tuple(_convert(p) for p in s.parameters) for s in sketches}
    return FusionParts(parameters, by_feature, by_sketch, len(timeline), _by_item(segment, raw))
