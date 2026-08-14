# Installation and distribution implementation plan

This engineering record is intentionally separate from the authoritative HTML design.
It tracks the bounded implementation, verification, and review evidence for the current distribution work.

## Approved scope

- Base commit: `717d4e176307e08cc4ae4facd3c484511684746c`.
- Public behavior: released wheels serve prebuilt Web assets without Node.js; packaged Electron apps start an embedded CPython runtime; macOS builds DMG and ZIP artifacts; Linux builds AppImage artifacts; release CI verifies versions and checksums.
- Product documentation describes only behavior whose acceptance checks pass.
- Windows-specific implementation, testing, packaging, and compatibility remain excluded.
- OS credential-store integration remains excluded.

## Files

- Production: `openprogram/webui/frontend.py`, `pyproject.toml`, `desktop/main.js`, `desktop/package.json`, release staging scripts, and the release workflow.
- Tests: frontend package-resource tests, desktop packaged-runtime checks, and release configuration checks.
- Documentation: the distribution HTML design, related design links, and install/upgrade/desktop/server product pages.

## Public-entry acceptance

1. A wheel built after release asset staging contains `openprogram/webui/_frontend/index.html` and hashed Next.js assets; an isolated wheel install serves `/chat` without repository sources or Node.js.
2. A packaged Electron launch resolves the Python executable exclusively from `process.resourcesPath`, invokes `-I -B -m openprogram worker start`, does not fall back to `PATH`, and does not write bytecode into the signed application.
3. `electron-builder` declares macOS DMG/ZIP and Linux AppImage targets and includes the staged runtime as an immutable resource.
4. A tag-triggered release workflow builds each platform on its native runner, runs focused acceptance checks, and publishes checksums only after artifacts exist.

## Full gate manifest

```text
python -m pytest tests/unit/test_webui_frontend.py tests/unit/test_desktop_packaged_files.py tests/unit/test_distribution_release.py
python -m tools.docs_site.checklinks
python -m tools.docs_site.build
python -m pytest tests/ --ignore=tests/integration
npm run check --prefix desktop
npm run check --prefix web
git diff --check
git status --short
```

Platform artifact builds run in the release workflow because a macOS host cannot validate a Linux CPython/AppImage runtime and a Linux host cannot sign or notarize a macOS app.

## Ledger

| Field | Evidence |
|---|---|
| Base | `717d4e176307e08cc4ae4facd3c484511684746c` |
| CodeGraph | Repository index was available in the shared checkout for initial exploration; the isolated worktree had no `.codegraph/`, so implementation lookup used targeted `rg` and direct reads. |
| RED | Initial focused run: 5 failed and 10 passed; failures covered missing package-resource frontend selection, desktop targets/runtime resolution, release installer, and release workflow. |
| GREEN | Focused distribution/desktop suite: 25 passed; docs build produced 503 pages with 0 broken links; desktop and Web npm checks passed; clean wheel import, managed CLI install, and rebuilt macOS arm64 packaged worker smoke passed. |
| Specification review | Local design-to-implementation audit passed after adding final-DMG notarization/stapling, runtime-manifest schema/version validation, and removal of remaining public Windows-native/any-platform claims. Two bounded CodeBuddy review attempts returned no output and were terminated, so no external-review pass is claimed. |
| Quality review | Ponytail full audit found no new dependency or packaging abstraction to remove. Shell/YAML syntax checks, diff checks, focused tests, the rebuilt app smoke, and runtime signature-stability check passed. The release workflow now writes the App Store Connect key with owner-only permissions and uses the protected `release` environment. |
| Full gate | `tests/ --ignore=tests/integration`: 5253 passed, 9 skipped, 1 xfailed, 4 failed. Three deterministic failures reproduced unchanged at the base commit (two runtime HTTP inventory tests and one dropped-tool-parameter test). The resource-governance multiprocessing failure passed on immediate targeted rerun and is classified as a timing-sensitive failure. Change-specific Python, docs, desktop, Web, shell, YAML, wheel, CLI installer, and packaged-runtime gates are green. |
| Dependency audit | No dependency was added. Existing lockfiles report 7 desktop npm advisories (2 moderate, 5 high) and 8 Web npm advisories (high); remediation is separate dependency-maintenance work. |
| Release-only evidence | Developer ID signing, Apple notarization, macOS x64 artifacts, Linux x86_64 AppImage startup, and Linux arm64 CLI installation require the tag workflow, protected credentials, and native runners. They are configured but not claimed from local verification. |
| Implementation commits | Batch 1: `714981e1`; merged to local `main` as `8da88f90`. The current follow-up commit records review fixes and final evidence. |
