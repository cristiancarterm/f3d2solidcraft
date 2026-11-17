# f3d2solidcraft

Convert Autodesk Fusion designs (`.f3d` and `.f3z`) into editable [SolidCraft](https://github.com/tangency-labs/solidcraft)
designs. Parameters, sketches and features are rebuilt as a SolidCraft timeline you can keep editing; whatever cannot
be rebuilt yet comes in as an imported body, and a report says, feature by feature, which was which.

Every conversion is checked against the original before you get it: same bodies, same volume (within 0.1 %), same
bounding box and centre of mass (within 0.01 mm), same face count.

*Guía en español: [docs/guia.md](docs/guia.md).*

## Status

Early, and honest about it. On the corpus of 15 real designs it is developed against:

- **every conversion verifies** — the part you get is the part you had;
- **about half of the features are rebuilt as editable SolidCraft features** (139 rebuilt from what the file says,
  15 rebuilt with a choice measured against the original); the rest come in as imported bodies;
- one design comes out entirely native, with nothing imported.

What is rebuilt today: parameters; sketches (lines, arcs, circles and splines, with the common constraints and
dimensions); extrudes (new body, join, cut, intersect; to a distance, through all, or to a face); revolves; lofts;
fillets and chamfers; shells; combines; mirrors and patterns where the file resolves them; split, remove and copy
body; sheet-metal base flanges; primitives; datum planes and axes; and the component tree with each component's
placement.

What is not, and why it falls back: features the file stores only as an opaque recipe (base features, some
`Assemble`, `Thread`, `Delete Face`), features whose faces the file does not record (`Offset Faces`), and a few the
SolidCraft kernel cannot build yet. Those come in as imported bodies, and the report names each one and its reason.

## Requirements

- Python 3.13 or newer.
- [SolidCraft](https://github.com/tangency-labs/solidcraft/releases) **0.43.0** or newer — the converter drives its
  `solidcraft-cli`. On macOS it is inside the app (`/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli`); on
  Linux it ships in the release tarball, under `bin/`.
- [cadmpeg](https://github.com/cadmpeg/cadmpeg/releases) **0.6.11** or newer — it reads the Fusion file. Prebuilt
  binaries are on its releases page, or `cargo install cadmpeg --version 0.6.11 --locked`.

Both are found on your `PATH`, at `~/.local/bin/solidcraft-cli` and `~/.cargo/bin/cadmpeg`, or wherever
`F3D2SC_SOLIDCRAFT_CLI` and `F3D2SC_CADMPEG` point. If one is missing, the error says where it looked.

Tested on macOS and Linux. Windows is untested.

## Install

```bash
uv tool install f3d2solidcraft
```

or `pipx install f3d2solidcraft`, or `pip install f3d2solidcraft` into an environment of your own.

## Desktop app

If you would rather not use a terminal, there is a desktop program that walks you through a conversion. Download
its installer from the assets of a release on [the Releases page](https://github.com/cristiancarterm/f3d2solidcraft/releases):

- **macOS**: `.dmg`, universal (Apple silicon and Intel), macOS 14 or newer. It is about 220 MB.
- **Windows**: `.msi`. Not tested on Windows yet.
- **Linux**: `.AppImage`, x86_64, for systems with glibc 2.35 or newer (Ubuntu 22.04 and later). Tested on fresh
  Ubuntu 22.04 and 24.04.

The program brings its own cadmpeg and does not need Python. You only need
[SolidCraft](https://github.com/tangency-labs/solidcraft/releases) **0.43.0** or newer installed. The program looks
for its `solidcraft-cli` on your `PATH`, at `~/.local/bin/solidcraft-cli`, and then:

- macOS: `/Applications/SolidCraft.app` and `~/Applications/SolidCraft.app`;
- Windows: the usual install folder of the SolidCraft installer;
- Linux: `/usr/bin/solidcraft-cli`, where the `.deb` puts it.

It uses the first copy it finds that is 0.43.0 or newer. If SolidCraft is somewhere else, click
**I have it: locate it…** and point to it. **Download SolidCraft** opens SolidCraft's releases page in your browser;
it is the only link the program opens, and it sends nothing over the internet. Advanced users can set
`F3D2SC_SOLIDCRAFT_CLI` and `F3D2SC_CADMPEG`; when set, they win over everything else, including a
location chosen with **I have it: locate it…** and the cadmpeg that comes with the program.

### Opening it the first time

The installers are not signed by Apple or Microsoft, so the system asks once before opening them:

- **macOS**: open the `.dmg` and drag the program to Applications. The first time, right-click it and choose
  **Open**, or go to **System Settings → Privacy & Security** and click **Open Anyway**.
- **Windows**: when SmartScreen shows its warning, click **More info**, then **Run anyway**.
- **Linux**: make the file executable and open it:

  ```bash
  chmod +x f3d2solidcraft-*.AppImage
  ./f3d2solidcraft-*.AppImage
  ```

### The three steps

1. **① File**: drop a `.f3d` or `.f3z` file, or click **Choose file…**; the program shows what the file holds and
   where it will save the `.solidcraft`.
2. **② Convert**: a progress bar goes feature by feature; some features are measured against the original, so it
   can take a couple of minutes, and **Cancel** goes back to step ①.
3. **③ Result**: **✓ Verified** when the part matches the original, an amber warning when it was saved but does
   not fully match, or an error with **Copy details to report it**. In every case you can see the details per
   feature and save the report; when a design was saved, **Open in SolidCraft** opens it.

## Use

See what a file holds and what would be rebuilt, without converting anything:

```bash
f3d2solidcraft inspect design.f3d
```

Convert it:

```bash
f3d2solidcraft convert design.f3d -o design.solidcraft --report design.report.json
```

```
design.f3d
  features: 14 exact, 0 inferred, 12 fallback, 0 failed, 0 skipped
  bodies: 3 matched, 0 missing, 0 extra
  bodies: 2 native, 1 imported
  verified: yes
  rebuild: fusion_d11 ok
```

- `exact`: rebuilt from what the file says.
- `inferred`: rebuilt too, but with a choice the file does not record (which profiles an extrude used, which edges a
  fillet rounded), measured against the original solid. Its warnings say what was chosen and how clearly.
- `fallback`: not rebuilt; its body comes in as an imported body.
- `failed`: not rebuilt, with no body to fall back to (a construction plane, for example).
- `skipped`: suppressed in Fusion.
- `bodies: N native, M imported`: where the final bodies come from.
- `rebuild`: the converter changes one parameter and checks that the design still rebuilds.

The exit code is `0` when the result matches the original, `2` when it saved a design that differs, and `1` on
errors. `--report` writes all of the above per feature, with the reason for every fallback, as JSON. `--force`
overwrites an existing output.

### From an AI assistant (MCP)

`f3d2solidcraft mcp` serves the converter over MCP on stdio, so an assistant can import a design by asking:
`import_f3d` converts a file and answers with the counts and whether it verified; `inspect_f3d` lists what a file
holds. Register it next to SolidCraft's own MCP server:

```json
{"mcpServers": {"f3d2solidcraft": {"command": "f3d2solidcraft", "args": ["mcp"]}}}
```

## How it works

cadmpeg decodes the Fusion file into a description of the design: parameters, sketches and each feature's recipe.
The converter replays that timeline in SolidCraft, one feature at a time. Fusion does not store everything a
rebuild needs — which sketch profiles an extrude used, which edges a fillet rounded are kept as opaque kernel
identities — so for those the converter loads the original solid (exported by cadmpeg) into the same SolidCraft
session as a reference, tries the candidates, measures each one against it, and removes the reference before saving.
Those features are reported `inferred`, never `exact`.

Whatever cannot be rebuilt is brought in from the original B-rep, so the converted design is always the whole part.

## License

MIT. It includes a vendored copy of [ezf3d](https://github.com/AlexSabaka/ezf3d), also MIT, whose license ships alongside.
