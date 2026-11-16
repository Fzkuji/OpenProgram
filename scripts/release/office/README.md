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
checkout: they load its standard-library-only
`openprogram/programs/applications/office_editor/assets.py` validator and
installer by file path. The source archive does not constitute a standalone
OpenProgram checkout. Font inputs and their original licenses are explicit
build inputs, and their relative names and hashes are recorded in
`source/font-provenance.json`.

The optional installer downloads the archive named by `OFFICE_ARCHIVE_NAME`
from the release of the installed version, then from the newest release. The
file name, archive size and SHA-256 are declared in
`openprogram/programs/applications/office_editor/install.py`, and the
`office.assets` download limit in `openprogram/host/security/safe_http.py` is
the same size; every release from 1.3.0 uploads that file once. The Release
workflow's `office` job carries the newest published release's copy forward
and stops every build when there is none.

A new pack needs a new file name: installed versions pin the bytes behind each
name. To build one:

1. Set `OFFICE_LOCK_SHA256` in
   `openprogram/programs/applications/office_editor/assets.py` to the SHA-256
   of this directory's `package-lock.json`, the lock `prepare.py` builds with;
   the pack's last validation refuses any other lock.
2. Run `prepare.py` as above.
3. Zip it and pin it:

   ```sh
   python scripts/release/office-archive.py pack /path/to/prepared-office /path/to/out
   ```

   The archive is named `OfficeAssets-<editor>-<patch>-<pack>.zip`, the last
   part from the pack's manifest digest, and the command writes its name,
   size and SHA-256 into `install.py` and the limit into `safe_http.py`.
4. Commit, promote, then tag with the file; before anything moves, the
   publish script puts it on the tag's draft release when GitHub has no copy:

   ```sh
   scripts/publishing/publish-github.sh -m "summary" --tag vX.Y.Z --office-archive /path/to/out/OfficeAssets-….zip
   ```

   `python scripts/release/office-archive.py seed vX.Y.Z FILE` does the
   upload step alone.
