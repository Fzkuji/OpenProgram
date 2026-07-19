# Split Task 4 Report

Status: complete

## RED

- `cd web && npm run check:web-split` failed on the missing `visibleWebTab()` split-target selector.
- `python -m pytest tests/unit/test_webtab_control.py -q` failed on the missing renderer `openWebTabInSplit` control path (`10 passed, 1 failed`).
- A follow-up route-preservation check failed before the split branch was excluded from `showCenterSurface()`.

## GREEN

- `cd web && npm run check`: passed all center-tabs, bookmarks, web-split, multi-draft, provisional-send, and chat-ui checks.
- `node desktop/scripts/check-webtab-navigation.js`: `webtab navigation checks passed`.
- `python -m pytest tests/unit/test_webtab_control.py -q`: `11 passed` with 5 pre-existing SWIG deprecation warnings.
- `cd web && npx tsc --noEmit`: passed.
- `cd web && npm run build`: passed; Next.js emitted two pre-existing ambiguous Tailwind class warnings.

## Commit

- `feat(browser): control the visible split web tab` (this task commit).

## Concerns

- The renderer command contract is checked at source level; this task does not add a live Electron/CDP interaction test.
- The production build still reports ambiguous `duration-[220ms]` and `ease-[cubic-bezier(...)]` Tailwind classes outside this task's files.
