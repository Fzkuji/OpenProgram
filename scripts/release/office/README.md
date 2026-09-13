# Local Office resources

The build uses `agentbridges-ai/onlyoffice-browser` commit
`d15d12b6945be4d8b0f3aa1806120e740d2950ee`, package `0.3.34`, and the
`adoption.patch` and npm lock shipped in this directory. Preparation checks
both digests. The patch serializes export acknowledgements and preserves native
undo when switching between viewing and editing. It does not enable macros.

Prepare fonts with `font-generation.mjs` and the official Document Server 9.3.0
font generator. The pinned full pack contains 126 generated font files and
10 thumbnails, generated from 127 input font files with 29 accompanying
license files. Supply only font families accompanied by their original
licenses. System fonts and user-installed fonts are excluded. The Python
validator checks the generated files against the input bytes, generated layout
pins and licenses; an incomplete pack cannot be published.

```sh
python scripts/release/office/prepare.py \
  --source /path/to/pinned-onlyoffice-browser \
  --font-pack /path/to/generated-font-pack \
  --font-input /path/to/licensed-font-input \
  --output /path/to/prepared-office
```

Preparation runs the pinned build and library build, removes demo pages and
fixtures, and includes upstream source, the applied patch, build inputs and
licenses. `onlyoffice-runtime-assets.json` is the editor's native manifest;
`openprogram-office-assets.json` inventories every installed file with size and
SHA-256. Its host identity must match the actual editor handshake.

Office support is optional. Default release staging, native runtime builds and
local App refresh do not download, prepare or bundle the Office pack. Opening
a document checks availability only; the user chooses Install before the
fixed release archive is downloaded (about 699 MiB). Runtime installation
verifies its pinned archive digest, validates every resource and publishes to
the profile cache. App and source workers use that same cache; old App-bundled
packs are no longer selected. No restart is required after installation.

For explicit editor development or testing, `stage.py --source /path/to/pack
--output /path/to/output` still stages a verified pack. `--web-root` optionally
copies the small parent module for standalone tests. Production serves the
parent module from the authenticated API after installation. The large runtime
does not enter the Python wheel or initial chat bundle.

Installations contain immutable `versions/<manifest-sha256>` directories.
All copied bytes are validated and flushed before `current.json` atomically
selects a version. Repeated installation reuses that version. Earlier versions
remain available to existing readers and for rollback; failed copies never
replace the selected version. The parent module is staged separately under
`public/document-assets/office/<patch-sha256>/public-api.js` and loads lazily.
The large runtime does not enter the Python wheel or initial chat bundle.

The bundled upstream archive and adoption patch are the corresponding editor
source. Run the copied preparation scripts from the matching OpenProgram
checkout: they import its standard-library-only `openprogram.office_assets`
validator and installer. The source archive does not constitute a standalone
OpenProgram checkout. Font inputs and their original licenses are explicit
build inputs, and their relative names and hashes are recorded in
`source/font-provenance.json`.

The optional installer downloads `OfficeAssets-d15d12b-dc31dd9d.zip` from
`v0.9.0`. The fixed URL, archive size and SHA-256 are declared in
`openprogram/office_install.py`. Default release jobs do not fetch that asset.
