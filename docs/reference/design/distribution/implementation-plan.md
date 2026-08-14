# Installation and distribution implementation plan

This engineering record is intentionally separate from the authoritative HTML design.
It tracks the bounded implementation, verification, and review evidence for the current distribution work.

## Release gate repair for v0.6.1

- Removed four unreferenced legacy Channel modules after the implementations had moved under `openprogram/channels/implementations/`; the runtime HTTP inventory now scans only active Channel code.
- Renamed the Research writer's dropped `context` parameter to the runtime-supported `project_context` field.
- Recorded Browser Agent as an explicitly deferred internal tool loop without changing its current behavior.
- Local acceptance: the previously failing four tests pass; affected tests report 461 passed; the full non-integration suite reports 5274 passed, 11 skipped, and 1 expected failure. Desktop, Web, release-script, runtime-HTTP, Ruff, and documentation gates pass.
- Windows native packaging remains deferred for a later release decision. This repair does not add a Windows artifact or introduce constraints that prevent a later implementation.

### Native release result and v0.6.2 correction

- Tag `v0.6.1` remained immutable after release run `31820999574` failed while resolving the complete macOS x86_64 runtime. No GitHub Release was published from that tag.
- Root cause: `semble 0.2.0` constrained `tree-sitter-language-pack` below 1.8, and the locked 1.6.2 package published no macOS x86_64 wheel.
- Correction: require `semble>=0.5.3`, whose grammar dependency is `semble-grammars`; the locked grammar package publishes native wheels for macOS x86_64/arm64 and Linux x86_64/arm64 while preserving the search capability.
- Regression gate: resolve the complete locked product requirements for CPython 3.12 on macOS x86_64 and assert the locked grammar artifact includes that platform. The release retry uses the higher patch version `v0.6.2`.
- Tag `v0.6.2` remained immutable after run `31822787529` passed the search dependency step but found that `torch 2.13.0` no longer publishes macOS x86_64 wheels. No GitHub Release was published from that tag.
- The `v0.6.3` retry uses the last upstream macOS x86_64-compatible pair, `torch 2.2.2` and `torchvision 0.17.2`, together with `numpy 1.26.4` for NumPy ABI compatibility. All four release targets must resolve this same GUI stack; Linux continues to use the official CPU wheel index.
- Tag `v0.6.3` remained immutable after run `31823941178` showed that unconstrained GUI harness installation upgraded NumPy back to 2.x through the current OpenCV dependency, invalidating the Torch 2.2.2 NumPy ABI. No GitHub Release was published from that tag.
- The `v0.6.4` retry pins `opencv-python 4.11.0.86` and applies one constraints file to every first-party Program installation so later dependency resolution cannot replace the verified NumPy, OpenCV, Torch, or Torchvision stack.
- Tag `v0.6.4` remained immutable after run `31824996497` built and installed all four complete runtimes but exposed an architecture-name mismatch in the Intel macOS Desktop command: release archives use `x86_64`, while electron-builder accepts `x64`. No GitHub Release was published from that tag.
- The `v0.6.5` retry keeps runtime artifact naming unchanged and maps the Intel Desktop builder argument to `x64`; a release-workflow regression test enforces the explicit mapping for both macOS architectures.

## Short public installer batch

- Base commit: `c1886a3fdf7ba196c42ec9a2c19dca7fe86c12e7`.
- Public command: `curl -fsSL https://openprogram.io/install | sh` for normal macOS/Linux CLI and server installations.
- Boundary: the root script resolves the latest stable GitHub Release, validates a three-part numeric version, downloads the immutable tagged installer, and forwards the version. It does not assemble a second installer and does not weaken runtime checksum, capability-manifest, or worker cold-start verification.
- Reproducibility: advanced users and CI may pass `OPENPROGRAM_VERSION=X.Y.Z` to the `sh` process. The tagged `scripts/install-release.sh` remains the authoritative versioned installer.
- Publication: `docs/_static_root/install.sh` must be renamed to the deployed site root as `/install`; `/docs/install/` remains the installation documentation directory.
- Tests: execute the public root script with a fake `curl` for automatic latest-version resolution and explicit pinning, assert the tagged installer handoff, build the docs site, verify the assembled root file, and run the existing distribution release suite.
- RED evidence: the two public-entry tests initially failed because the root bootstrap did not exist and the Pages workflow did not publish `/install`.
- GREEN evidence: the distribution release file reports 25 passed; docs build reports 509 pages; landing check passes; link check reports 0 broken links; an assembled-site probe preserves `/docs/install/` and validates the root `/install` script.
- Release-state evidence on 2026-08-15: GitHub `latest` resolves to `v0.6.0`, whose release has no assets and whose tag has no `scripts/install-release.sh`; `v0.6.1` has not been tagged. The bootstrap therefore fails without installing a reduced product until a complete release is published. This batch does not create or move a tag.

## Unified complete-product batch

- Base commit: `e6ec8694977080153a3c94e50a5080d2ff43b69b`.
- Product contract: every supported non-developer installation contains the same complete capability set for its platform and architecture. A Desktop artifact must use the same runtime archive as CLI/server and may differ only by its Electron shell. If that complete packaged entry does not pass, the Desktop artifact is absent rather than reduced.
- Required capabilities: Web, providers, MCP, memory, channels, search, default Playwright Chromium, default OCR, the GUI detector model, and the GUI, Research, and Wiki first-party Programs.
- Developer installations add editable sources, tests, diagnostics, local frontend builds, and replaceable OCR/browser backends. They do not define a smaller or different product edition.
- Ordinary users install from GitHub Release artifacts. PyPI wheels remain internal build inputs and developer artifacts, not a product installation path.
- macOS artifacts are explicitly unsigned DMG/ZIP files. Apple Developer ID signing and notarization are not release requirements. Linux publishes complete x86_64/arm64 CLI/server runtimes; no Linux Desktop artifact is published after the complete AppImage failed its packaging gate.
- Windows native packaging is deferred from this release, while the runtime/Desktop separation must preserve later implementation feasibility. OS credential-store integration remains excluded.

### Current-batch files

- Product contract: `docs/reference/design/distribution/installation-packaging.html` and this implementation record.
- Runtime assembly: a checked-in product manifest, runtime builder/archive scripts, desktop runtime staging, and the CLI release installer.
- Launchers: Electron and CLI launch paths that set bundled browser/OCR/model locations and validate the capability manifest.
- Release: `.github/workflows/release.yml`, desktop artifact naming, runtime archives, checksums, and public-entry smoke checks.
- Product documentation: install, desktop, server, upgrade, Programs, and README entry points that describe one complete product rather than optional first-party components.
- Tests: `tests/unit/test_distribution_release.py` plus focused Program/runtime and packaged-entry probes.

### Current-batch public-entry acceptance

1. Each platform runtime archive contains a manifest with `present` and `verified` entries for `web`, `providers`, `mcp`, `memory`, `channels`, `search`, `browser.playwright`, `ocr.default`, `model.gpa_detector`, `program.gui`, `program.research`, and `program.wiki`.
2. Supported Desktop packaging consumes the already-built runtime archive. The CLI installer consumes the byte-identical archive from GitHub Release. Neither path resolves product dependencies independently.
3. A normal-user install performs no PyPI dependency resolution, repository clone, npm build, or first-use download for the default browser, OCR, detector model, or first-party Programs.
4. Public-entry probes verify the worker, Web assets, first-party Program registration, channel/search imports, Playwright Chromium executable, OCR model data, and detector model before an artifact is published or a CLI `current` link is switched.
5. macOS artifact names and documentation state `unsigned`; the release workflow requires no Apple or PyPI credentials and does not run signing, notarization, or PyPI publication.
6. Documentation does not present `pip install openprogram`, optional GUI/Research/Wiki installation, component-selection prompts, or an unverified Linux Desktop package as normal product installation.

### Current-batch gate manifest

```text
python -m pytest tests/unit/test_distribution_release.py tests/unit/test_desktop_packaged_files.py tests/unit/test_webui_frontend.py
python -m tools.docs_site.checklinks
python -m tools.docs_site.build
python -m pytest tests/ --ignore=tests/integration
npm run check --prefix desktop
npm run check --prefix web
bash -n scripts/build-product-runtime.sh scripts/install-release.sh scripts/prepare-desktop-runtime.sh
git diff --check
git status --short
```

The platform runtime and public desktop artifact probes run on native release runners. A platform artifact is absent rather than published with a reduced capability manifest.

### Current-batch evidence

| Field | Evidence |
|---|---|
| RED | The first focused distribution run reported 6 expected failures: no product manifest, no unified runtime builder, the CLI installer still resolved a wheel, Desktop and CLI assembled dependencies independently, and the workflow still required Apple/PyPI publication paths. |
| Static GREEN | Distribution, packaged-file, and Web frontend suite: 35 passed after removing the unverified Linux Desktop target. Desktop and Web checks, shell syntax, Ruff, docs build (507 pages), link check (0 broken), and diff checks passed. |
| Real macOS arm64 runtime | CPython 3.12.10, locked OpenProgram dependencies, GUI/Research/Wiki pinned commits, Playwright Chromium, EasyOCR English/Chinese data, GPA detector, and all 12 capabilities built successfully. The complete runtime was about 2.5 GB before compression. |
| Runtime verification | The schema 2 verifier launched a real headless Chromium page, imported channels/search/OCR/PDF dependencies, checked Web assets and model files, and required GUI/Research/Wiki registration. It records the product-manifest hash, `uv.lock` hash, exact installed distributions, platform, and architecture. |
| Archive and CLI entry | A macOS arm64 archive was checksum-verified, extracted into a new CLI version directory, re-verified, cold-started and stopped a worker, switched `current`, and produced a working `openprogram 0.6.1` launcher. |
| Native Linux run | GitHub Actions run `31809407776` at `c49596ef` built and verified the complete Linux x86_64 and arm64 runtimes. Both CLI installer jobs passed checksum, extraction, capability verifier, worker cold-start, atomic activation, and version probes. The AppImage job failed during electron-builder's embedded block-map stage after the complete runtime itself passed; no AppImage reached public-entry or Debian 11 verification. |
| Packaging decision | Linux AppImage build and publication were removed. Linux remains supported through the complete x86_64/arm64 CLI/server archives with Web UI and TUI. No reduced Linux Desktop artifact is offered. |
| Final Linux gate | GitHub Actions run `31811091609` at `08a9a19a` passed all four remaining jobs: complete x86_64 and arm64 runtime build/archive plus x86_64 and arm64 CLI public-entry installation. The workflow contains no Linux Desktop packaging job. |
| Full local gate | `tests/ --ignore=tests/integration`: 5285 passed, 11 skipped, 1 xfailed, 5 failed. Four deterministic failures are outside this batch (two existing channel HTTP inventory failures, one Research `context` parameter failure, and one browser-agent migration inventory failure). The fifth was a multiprocessing timeout and passed immediately in isolation. |
| Review | Ponytail full review removed the first-party selection menu and reduced the model to one manifest, one builder, one verifier, and existing shell/workflow entry points. Manual specification review found and fixed unlocked dependency resolution, non-canonical archive roots, missing archive checksum/path validation, Playwright cleanup warnings, and the Research PDF extra. |
| Incremental commits | Design commit `e2a1b691`, implementation commit `c75099d2`, and complete-install follow-up `685833cc` were merged and pushed incrementally; the follow-up reached `main` in `c49596ef`. |

## Prior packaging batch (historical evidence)

- Base commit: `717d4e176307e08cc4ae4facd3c484511684746c`.
- Public behavior: released wheels serve prebuilt Web assets without Node.js; packaged Electron apps start an embedded CPython runtime; macOS builds DMG and ZIP artifacts; Linux builds AppImage artifacts; release CI verifies versions and checksums.
- Product documentation describes only behavior whose acceptance checks pass.
- Windows native packaging was not implemented in this historical batch; it remains a deferred product decision rather than a rejected direction.
- OS credential-store integration remains excluded.

### Prior-batch files

- Production: `openprogram/webui/frontend.py`, `pyproject.toml`, `desktop/main.js`, `desktop/package.json`, release staging scripts, and the release workflow.
- Tests: frontend package-resource tests, desktop packaged-runtime checks, and release configuration checks.
- Documentation: the distribution HTML design, related design links, and install/upgrade/desktop/server product pages.

### Prior-batch public-entry acceptance

1. A wheel built after release asset staging contains `openprogram/webui/_frontend/index.html` and hashed Next.js assets; an isolated wheel install serves `/chat` without repository sources or Node.js.
2. A packaged Electron launch resolves the Python executable exclusively from `process.resourcesPath`, invokes `-I -B -m openprogram worker start`, does not fall back to `PATH`, and does not write bytecode into the signed application.
3. `electron-builder` declares macOS DMG/ZIP and Linux AppImage targets and includes the staged runtime as an immutable resource.
4. A tag-triggered release workflow builds each platform on its native runner, runs focused acceptance checks, and publishes checksums only after artifacts exist.

### Prior-batch full gate manifest

```text
python -m pytest tests/component/webui/test_webui_frontend.py tests/unit/webui/test_desktop_packaged_files.py tests/component/config/test_distribution_release.py
python -m tools.docs_site.checklinks
python -m tools.docs_site.build
python -m pytest tests/ --ignore=tests/integration
npm run check --prefix desktop
npm run check --prefix web
git diff --check
git status --short
```

Platform artifact builds run in the release workflow because a macOS host cannot validate a Linux CPython/AppImage runtime and a Linux host cannot sign or notarize a macOS app.

### Prior Linux completion batch

- Base commit: `540591e9f628498dee87a1c2ebb30ab4c5e757f6`.
- Production files: `desktop/package.json`, `scripts/smoke-packaged-runtime.sh`, `scripts/install-release.sh`, `.github/workflows/release.yml`, and `.github/workflows/linux-release-smoke.yml`.
- Test file: `tests/unit/test_distribution_release.py`.
- Linux x86_64 acceptance: build the AppImage on a native x86_64 runner, execute its public entry under Xvfb, let Electron start the embedded worker, verify `/healthz`, `/chat`, immutable Program behavior, and matching freedesktop filename/`StartupWMClass` metadata.
- Linux CLI acceptance: on native x86_64 and arm64 runners, install the release wheel with the pinned uv and managed CPython, cold-start the worker before switching `current`, and verify the installed launcher version.
- Pre-release execution: the manually dispatched Linux smoke workflow requires no Apple signing or PyPI credentials and uploads the verified wheel and AppImage only as CI artifacts. It does not create a stable release.
- This historical batch did not add Linux arm64 desktop artifacts, distro-native deb/rpm packages, Windows implementation, or OS credential-store integration. Windows remains deferred for a later product decision.

### Prior-batch ledger

| Field | Evidence |
|---|---|
| Base | `717d4e176307e08cc4ae4facd3c484511684746c` |
| CodeGraph | Repository index was available in the shared checkout for initial exploration; the isolated worktree had no `.codegraph/`, so implementation lookup used targeted `rg` and direct reads. |
| RED | Initial focused run: 5 failed and 10 passed; failures covered missing package-resource frontend selection, desktop targets/runtime resolution, release installer, and release workflow. |
| GREEN | Focused distribution/desktop suite: 25 passed; docs build produced 503 pages with 0 broken links; desktop and Web npm checks passed; clean wheel import, managed CLI install, and rebuilt macOS arm64 packaged worker smoke passed. |
| Specification review | Local design-to-implementation audit passed after adding final-DMG notarization/stapling, runtime-manifest schema/version validation, and removal of remaining public Windows-native/any-platform claims. Two bounded CodeBuddy review attempts returned no output and were terminated, so no external-review pass is claimed. |
| Quality review | Ponytail full audit found no new dependency or packaging abstraction to remove. Shell/YAML syntax checks, diff checks, focused tests, the rebuilt app smoke, and runtime signature-stability check passed. The release workflow now writes the App Store Connect key with owner-only permissions and uses the protected `release` environment. |
| Full gate | `tests/ --ignore=tests/integration`: 5254 passed, 11 skipped, 1 xfailed, 3 failed. The failures are outside the distribution changes: two runtime HTTP inventory tests report unregistered channel transports, and one agent-tool test reports a dropped `context` parameter in the research program. Change-specific Python, docs, desktop, Web, shell, YAML, wheel, CLI installer, and packaged-runtime gates are green. |
| Dependency audit | No dependency was added. Existing lockfiles report 7 desktop npm advisories (2 moderate, 5 high) and 8 Web npm advisories (high); remediation is separate dependency-maintenance work. |
| Release-only evidence | Developer ID signing, Apple notarization, macOS x64 artifacts, GitHub Release creation, and PyPI publication still require the tag workflow and protected credentials. Linux x86_64 AppImage startup and Linux x86_64/arm64 CLI installation now have separate native-runner evidence. |
| Implementation commits | Initial batches: `714981e1`, `9e1e5e0a`, and `dddea787`. Linux completion batches: `a170ca65`, `78e78921`, `41b39e86`, `63f15e65`, and `b7220c89`; all were merged and pushed incrementally to `main`. |

### Prior Linux completion evidence

| Field | Evidence |
|---|---|
| RED | The old Linux smoke extracted the AppImage and launched embedded Python directly, so it did not test the public Electron entry. The first native workflow run then exposed electron-builder's implicit CI publish attempt. A clean arm64 container exposed zombie PID handling during worker stop. A running-worker upgrade probe exposed shared user-state interference. |
| GREEN | Distribution suite: 18 passed. Desktop and Web checks passed. Documentation link check reported 0 broken links and the static builder produced 503 pages. |
| Native Linux | GitHub Actions run `31798379681` at pushed `main` commit `17db67dc` passed the x86_64 AppImage job, x86_64 CLI job, and arm64 CLI job. The AppImage job executed the public AppImage/Electron entry and repeated the smoke in Debian 11/glibc 2.31 with no system Python, Node.js, Git, or external network. |
| CLI isolation | A native arm64 Debian 12 container with no system Python, Node.js, or Git installed uv 0.11.16, managed CPython 3.12.10, and the 0.6.1 wheel. A second install while the existing worker remained active completed its isolated cold-start and preserved the existing worker PID. |
| Specification review | Manual design-to-implementation review found and fixed public-entry coverage, freedesktop window association, implicit publishing, probe state isolation, and the clean-install doctor wording. A bounded CodeBuddy review attempt exhausted its turn limit without a verdict, so no external specification pass is claimed. |
| Quality review | Ponytail full review kept the implementation in existing shell scripts and workflows, added no runtime dependency, and rejected a separate doctor mode. A bounded CodeBuddy quality review returned tool-call text without a verdict, so no external quality pass is claimed. |
| Stable release | No `v0.6.1` tag was created. The GitHub `release` environment and Apple signing secrets are absent; the external PyPI trusted-publisher configuration is not verifiable from this repository. Signed macOS artifacts and the atomic GitHub/PyPI stable release therefore remain blocked. |
