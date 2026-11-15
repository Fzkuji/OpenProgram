# OpenProgram terminal client

The React/Ink terminal client. The Python CLI application that parses
commands, dispatches them and launches this client is the package
`openprogram.interfaces.cli` in
[`openprogram/interfaces/cli/`](../../openprogram/interfaces/cli/). Both use the
Agent Core and the single OpenProgram worker; neither owns a second backend or
product-state store.

## Source and build output

- `src/` contains the TypeScript and TSX source.
- `src/index.tsx` is the executable entry point.
- `dist/index.js` is the generated Node.js bundle and is not edited by hand.
- `dist/index-standalone.cjs` is the self-contained release bundle; product
  runtimes pair it with their platform Node.js executable.
- `npm run build:release` writes the self-contained bundle
  `openprogram/interfaces/cli/dist/index.mjs`, which ships in wheels as package
  data of `openprogram.interfaces.cli`.
- `openprogram/interfaces/cli/ink.py` resolves the source bundle or the
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
