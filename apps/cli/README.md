# OpenProgram CLI applications

This workspace contains both CLI application layers. `src/` is the React/Ink
terminal client. `python/openprogram_cli/` owns the Python command parser,
dispatch, Rich fallback and setup flows. Both use the Agent Core and the single
OpenProgram worker; neither owns a second backend or product-state store.

## Source and build output

- `src/` contains the TypeScript and TSX source.
- `src/index.tsx` is the executable entry point.
- `dist/index.js` is the generated Node.js bundle and is not edited by hand.
- `python/openprogram_cli/_impl/` contains the Python application
  implementation. `openprogram/cli/` is the bounded compatibility import.
- `python/openprogram_cli/_impl/ink.py` resolves and launches the bundle from
  a source checkout. If Ink is unavailable in an immutable release,
  `openprogram` uses the Rich terminal interface.

## Verification

```bash
npm install
npm run typecheck
npm test
npm run build
uv run pytest -q tests/unit/config/test_cli_parser_structure.py
```

The TUI expects the canonical worker, normally on port `18100`. Local visual
acceptance uses the default OpenProgram profile rather than starting another
worker instance.
