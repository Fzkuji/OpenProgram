# OpenProgram CLI applications

This workspace contains both CLI application layers. `src/` is the React/Ink
terminal client. `python/openprogram_cli/` owns the Python command parser,
dispatch, Rich fallback and setup flows. Both use the Agent Core and the single
OpenProgram worker; neither owns a second backend or product-state store.

## Source and build output

- `src/` contains the TypeScript and TSX source.
- `src/index.tsx` is the executable entry point.
- `dist/index.js` is the generated Node.js bundle and is not edited by hand.
- `dist/index-standalone.cjs` is the self-contained release bundle; product
  runtimes pair it with their platform Node.js executable.
- `python/openprogram_cli/_impl/` contains the Python application
  implementation. `openprogram/cli/` is the bounded compatibility import.
- `python/openprogram_cli/_impl/ink.py` resolves the source bundle or the
  immutable runtime's standalone bundle. If the active terminal cannot enter
  raw input mode, `openprogram` restores stdio and uses the Rich interface.

## Verification

```bash
npm install
npm run typecheck
npm test
npm run build
npm run build:standalone
uv run pytest -q tests/unit/config/test_cli_parser_structure.py
```

The TUI expects the canonical worker, normally on port `18100`. Local visual
acceptance uses the default OpenProgram profile rather than starting another
worker instance.
