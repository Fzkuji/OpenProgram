# Reproducible Office asset preparation

`prepare.py` builds the pinned OnlyOffice browser source at preparation time,
applies `adoption.patch`, prunes the public demo and split packs, overlays the
reviewed generated font output, and publishes a validated directory atomically.
The server reads that directory from a prepared runtime; it never downloads or
builds Office assets while opening a file.

The required source checkout must resolve to `d15d12b6945be4d8b0f3aa1806120e740d2950ee`
and package version `0.3.34`. `adoption.patch` has SHA-256
`0abfb281c7f523d5d0b9dc2f0dc60f6af4751920754d0a54cb14755b9478b088` and the
reviewed `package-lock.json` has SHA-256
`7b71a099e703545a80d06454ae2af6f52e52f0c3f1fbe5ead31dfdf11faf2590`.

Example preparation (all paths are explicit inputs):

```sh
python scripts/release/office/prepare.py \
  --source /path/to/onlyoffice-browser \
  --output /path/to/runtime/assets/office \
  --font-pack /path/to/output-open-fontpack \
  --font-input /path/to/font-input
```

The output contains `openprogram-office-assets.json` and the native
`onlyoffice-runtime-assets.json`. Every served path and license is recorded
with byte count and SHA-256. The manifest's `expectedHostIdentity` is the
SHA-256 of the native runtime manifest. Failed validation or build leaves the
previous output untouched.
