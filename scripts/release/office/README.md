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

Release staging accepts `OPENPROGRAM_OFFICE_PACK` pointing to this prepared
installation. Alternatively, set `OPENPROGRAM_OFFICE_SOURCE`,
`OPENPROGRAM_OFFICE_FONT_PACK` and `OPENPROGRAM_OFFICE_FONT_INPUT` to build it
at staging time. With no explicit inputs it uses an already verified build
output or the matching profile cache. A missing pack stops release staging.
There are no downloads when opening a document.

The runtime stores resources under `assets/office`. Source and PATH workers
read the matching version under the profile's `cache/office` directory. Local
App refresh installs the same frozen pack into both locations. An arbitrary
`OPENPROGRAM_RUNTIME_ROOT` environment value cannot override server assets.

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

GitHub release builders download the pinned `OfficeAssets-d15d12b-dc31dd9d.zip`
build input from `v0.9.0`. The workflow verifies its archive SHA-256 and the
complete Office manifest before staging; it does not depend on a runner's
profile cache. The input archive is included in the release artifact manifest.
A rebuild dispatch selects an existing immutable source tag. A newer workflow
controller can prepare the build environment without changing that tag's code.
The first publication finalizes its draft only after all native jobs pass.
