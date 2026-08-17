# OpenProgram Ink TUI workspace

This workspace contains the React/Ink terminal client. It connects to the
single OpenProgram worker over WebSocket; it does not implement a second
backend or own product state.

## Source and build output

- `src/` contains the TypeScript and TSX source.
- `src/index.tsx` is the executable entry point.
- `dist/index.js` is the generated Node.js bundle and is not edited by hand.
- `openprogram/cli_ink.py` resolves and launches the bundle from a source
  checkout. If the Ink runtime is unavailable in an immutable release,
  `openprogram` uses the built-in Rich terminal interface.

## Verification

```bash
npm install
npm run typecheck
npm test
npm run build
```

The TUI expects the canonical worker, normally on port `18100`. Local visual
acceptance uses the default OpenProgram profile rather than starting another
worker instance.
