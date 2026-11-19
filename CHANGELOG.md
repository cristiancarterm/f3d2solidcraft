# Changelog

## 0.4.0

- Parameters: every feature now takes its parameters from its own record in the file, even when the two readers
  of the file count the timeline differently. Before, such a design got no parameter on any feature; now its
  extrude distances and sketch dimensions are linked to their parameters in the SolidCraft timeline too. Where
  those features' bodies still come in as imported bodies, changing the parameter updates the timeline but does
  not move that geometry yet. The report counts the features that could not be paired (`unpaired_features`)
  instead of a general "timeline mismatch" warning.
- Fillet radii, chamfer distances and revolve angles follow the parameter that drives them in Fusion, when the
  file names exactly one and its value matches.
- Components: the saved design shows Fusion's component tree. A part the converter could not rebuild arrives with
  Fusion's name and every placement, without an empty copy beside it, and the empty components the conversion
  used to leave are removed. The container of the imported geometry is now called "Imported from Fusion". Known
  limit: when the import arrives with the assembly flattened (seen in some `.f3z` packages), its bodies stay at
  the level of "Imported from Fusion" and nested parts may carry generated names.
- Colours: the bodies rebuilt as native SolidCraft features take the colour Fusion binds to them. Imported bodies
  already kept theirs. Per-face colours are not applied yet: a rebuilt body gets its body colour only, and the
  report warns when Fusion colours single faces of it.
- A rebuilt body that comes out identical to the original is kept as a native body instead of being replaced by
  the imported one.
- Known limits: some empty components with repeated names can remain under "Imported from Fusion". They are the
  import's own definitions whose bodies moved into the design's component; SolidCraft refuses to delete them
  because it recorded that body transfer. They hold no geometry and change nothing but the tree.

## 0.3.2

- Windows app: loading a file failed with "Could not read the file: exit code 0", whatever the file. The
  installed app runs its own converter in a second process, and the launcher Briefcase builds sent that process's
  output to a log file instead of back to the window whenever it was started without a console, which is always
  the case from the window. The window now reads the converter's output and errors from one pipe, which survives
  that launcher, and the converter writes back to it.
- Release: before packaging, each installer's built app is run as the window runs it (from a parent with no
  console on Windows): `--check`, inspecting the public sample and a full conversion with progress, which must
  end verified. A build whose app prints nothing fails the release.

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
