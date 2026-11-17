# Changelog

## 0.3.1

- Desktop app: the "Save as" line and its "Change…" button only appear once a file is loaded.
- Desktop app on Windows: cancelling, closing the window or a converter that crashes now ends every process the
  conversion started, SolidCraft included. Before, an engine could be left running after a crash.
- File and folder names with accents, ñ or other non-ASCII letters convert from the command line and the desktop
  app on Windows, macOS and Linux. The command now writes its output as UTF-8 on every system.
- `f3d2solidcraft --version` prints the version.
- The report says "not supported yet" for sketch constraints and dimensions it cannot rebuild, instead of an
  internal milestone name.
- Release: the installers are attached to a draft release, published only once all three are built; the cadmpeg
  download for the installers times out instead of hanging, and refuses a Linux machine that is not x86_64.

## 0.3.0 — first public release

- A desktop program for Windows, macOS and Linux (`f3d2solidcraft[gui]`, or the installers attached to each
  release): a three-step wizard that converts one file at a time, verifies it and opens the result in SolidCraft.
  It ships its own cadmpeg; only SolidCraft 0.43.0 or newer has to be installed.
- `convert --progress` prints one JSON event per feature and a final `done` event; `inspect --json` prints what a
  file holds as JSON.
- A part placed more than once keeps every placement. If the saved design ever holds fewer placements than were
  measured, the verification fails (`placements_lost`, exit code 2) instead of passing.

- Converts Fusion `.f3d` designs and `.f3z` packages into SolidCraft designs: parameters, sketches and a feature
  timeline, with whatever cannot be rebuilt brought in as imported bodies.
- Every conversion is verified against the original (bodies, volume, bounding box, centre of mass, face count) and
  reported feature by feature as `exact`, `inferred`, `fallback`, `failed` or `skipped`.
- Choices the Fusion file does not record (an extrude's profiles, a fillet's edges) are measured against the
  original solid and reported as `inferred`.
- `inspect` to see what a file holds; `convert` to convert it; `mcp` to serve both over MCP.
- Requires SolidCraft 0.43.0 or newer and cadmpeg 0.6.11 or newer. Python 3.13 and 3.14.
