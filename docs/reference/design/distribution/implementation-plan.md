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

## Linux completion batch

- Base commit: `540591e9f628498dee87a1c2ebb30ab4c5e757f6`.
- Production files: `desktop/package.json`, `scripts/smoke-packaged-runtime.sh`, `scripts/install-release.sh`, `.github/workflows/release.yml`, and `.github/workflows/linux-release-smoke.yml`.
- Test file: `tests/unit/test_distribution_release.py`.
- Linux x86_64 acceptance: build the AppImage on a native x86_64 runner, execute its public entry under Xvfb, let Electron start the embedded worker, verify `/healthz`, `/chat`, immutable Program behavior, and matching freedesktop filename/`StartupWMClass` metadata.
- Linux CLI acceptance: on native x86_64 and arm64 runners, install the release wheel with the pinned uv and managed CPython, cold-start the worker before switching `current`, and verify the installed launcher version.
- Pre-release execution: the manually dispatched Linux smoke workflow requires no Apple signing or PyPI credentials and uploads the verified wheel and AppImage only as CI artifacts. It does not create a stable release.
- Exclusions remain unchanged: no Linux arm64 desktop artifact, distro-native deb/rpm packages, Windows implementation, or OS credential-store integration.

## Ledger

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

## Linux completion evidence

| Field | Evidence |
|---|---|
| RED | The old Linux smoke extracted the AppImage and launched embedded Python directly, so it did not test the public Electron entry. The first native workflow run then exposed electron-builder's implicit CI publish attempt. A clean arm64 container exposed zombie PID handling during worker stop. A running-worker upgrade probe exposed shared user-state interference. |
| GREEN | Distribution suite: 18 passed. Desktop and Web checks passed. Documentation link check reported 0 broken links and the static builder produced 503 pages. |
| Native Linux | GitHub Actions run `31798379681` at pushed `main` commit `17db67dc` passed the x86_64 AppImage job, x86_64 CLI job, and arm64 CLI job. The AppImage job executed the public AppImage/Electron entry and repeated the smoke in Debian 11/glibc 2.31 with no system Python, Node.js, Git, or external network. |
| CLI isolation | A native arm64 Debian 12 container with no system Python, Node.js, or Git installed uv 0.11.16, managed CPython 3.12.10, and the 0.6.1 wheel. A second install while the existing worker remained active completed its isolated cold-start and preserved the existing worker PID. |
| Specification review | Manual design-to-implementation review found and fixed public-entry coverage, freedesktop window association, implicit publishing, probe state isolation, and the clean-install doctor wording. A bounded CodeBuddy review attempt exhausted its turn limit without a verdict, so no external specification pass is claimed. |
| Quality review | Ponytail full review kept the implementation in existing shell scripts and workflows, added no runtime dependency, and rejected a separate doctor mode. A bounded CodeBuddy quality review returned tool-call text without a verdict, so no external quality pass is claimed. |
| Stable release | No `v0.6.1` tag was created. The GitHub `release` environment and Apple signing secrets are absent; the external PyPI trusted-publisher configuration is not verifiable from this repository. Signed macOS artifacts and the atomic GitHub/PyPI stable release therefore remain blocked. |
