# Calculator

A keyboard-first scientific calculator. It is a UI-only application: there is
no backend and no Agent operation, and the only capability is `storage.app`,
which keeps the history.

## Features

- Type an expression and see the result update as you type; Enter (or `=`)
  commits it to the history.
- Operators `+ − × ÷`, powers `^` and `²`, roots `√` / `sqrt()`, factorial `!`,
  percent, parentheses (unclosed ones are shown as ghost text and closed for
  you), implicit multiplication (`2(3)`, `2π`), scientific notation (`1E3`).
- Functions `sin cos tan asin acos atan ln log sqrt abs exp`, constants `π`, `e`
  and `Ans` (the previous result). Degrees or radians.
- History grouped by day; click an entry to reuse it. Copy the result with the
  button or ⌘C.
- Follows the host's light or dark theme.

| Key | Action |
| --- | --- |
| Enter / `=` | Calculate and add to history |
| Esc | Clear |
| ↑ / ↓ | Recall earlier expressions |
| ⌘C (nothing selected) | Copy the result |

`*`, `/` and `-` are shown as `×`, `÷` and `−`.

## Files

- `application.json`: the manifest.
- `ui/index.html`, `ui/app.js`, `ui/app.css`, `ui/theme.css`: the page.
- `ui/calc.js`: the evaluator, a small recursive-descent parser (no `eval`).
- `tests/test_calculator.py`: runs `calc.js` under Node; skipped without Node.

## Install

```bash
openprogram apps install examples/applications/calculator
```
