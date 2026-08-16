# Repository structure implementation ledger

This ledger records implementation evidence for
[`repository-structure.html`](repository-structure.html). The HTML page is the
current design contract; this file is operational history.

## Scope

- Move the existing top-level CLI parser out of `openprogram/cli.py` without
  changing the public `openprogram.cli.build_parser` import or command grammar.
- Keep `docs/superpowers/` in Git but remove it from the public documentation
  build. Preserve linked `reference/design/plans/` pages in a separate design
  section.
- Group the existing UI design pages in navigation without moving their source
  files.
- Record, but do not perform, Desktop and Web bridge splits while those areas
  have active product changes.

## Workstreams

| Workstream | Branch / commit | Status | Integration note |
|---|---|---|---|
| Earlier repository inventory | `codex/file-structure-20260816` / `373af35c` | superseded | Useful inventory and repository contract remain review input; do not merge the old branch wholesale. |
| CLI parser split | `codex/code-doc-structure-20260817` / `e54bd425` | implemented | Move-only split; `openprogram.cli.build_parser` remains the public import. |
| Documentation information architecture | `codex/code-doc-structure-20260817` | review candidate | Based on `af5911b3`; integrate with the current main branch only after focused review passes. |

## Verification record

```text
4 passed — focused CLI parser and documentation navigation contracts
206 passed — existing CLI component/unit tests plus focused structure contracts
497 pages — python -m tools.docs_site.build
0 broken links — python -m tools.docs_site.checklinks docs/_site
ok — python -m tools.docs_site.check_landing
pass — Ruff and git diff --check
pass — desktop render at 1280 × 720, no horizontal overflow
pass — narrow render at 390 × 844, no page overflow; wide tables scroll locally
pending — independent specification review
pending — independent quality review
```

## Deferred boundaries

- `desktop/main.js`: extract existing window lifecycle, native WebView, tab
  transfer, and menu responsibilities after the current Desktop work lands.
- `web/lib/desktop-bridge.ts`: establish executable state/transfer tests before
  moving types, view state, and transfer coordination.
- Long cohesive Python state machines remain intact until a separately tested
  responsibility is identified; line count alone is not an implementation task.
