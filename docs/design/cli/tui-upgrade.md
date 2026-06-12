# TUI upgrade: transcript rendering & interaction

Status: proposed (2026-06) — research done, implementation not started.
Companion: [user-input-requests.md](../runtime/user-input-requests.md) (mid-run questions; its TUI surface lands here).
Research notes: [tui-upgrade-references.md](tui-upgrade-references.md) (full Claude Code / opencode study + current-state audit).

## Goal

Bring the Ink TUI's transcript display and interaction up to the level of
Claude Code and opencode: tool calls that collapse without losing
information, a ctrl+o expanded transcript view, structured diff rendering,
message queueing while busy, and a keybinding system with discoverability.

## Current state (audit summary)

The base is solid: vendored hermes-ink cell-grid renderer with mouse
tracking + ScrollBox, 4 themes with live preview, rich BottomBar
(tokens/context%/cache/permission mode), command palette (ctrl+k), fish
autosuggest, @file completion, per-session drafts, full account/channel
flows. What's missing is concentrated in the transcript itself:

- Tool output is hard-folded at 6 lines (`Turn.tsx` MAX_LINES) with **no
  expand affordance at all** — no keybind, no verbose mode.
- Tool args are one truncated line; no per-tool rendering (every tool looks
  the same).
- No diff rendering anywhere; `/diff` dumps raw `git diff` text.
- Streaming text shows raw markdown source, then visually jumps when the
  final render lands (`Turn.tsx:123`); `renderMarkdown` is un-memoized under
  a full-redraw renderer.
- `follow_up_question` / `approval_request` envelopes are typed in
  `ws/client.ts` but **silently dropped** — agent questions time out, the
  `ask` permission mode is unreachable (shift+tab only cycles bypass↔auto).
- `/resume` rebuilds the transcript from role+content only — tool history
  is lost (`useWsEvents.ts:326-336`).
- Tool results are matched to calls **by tool name** (server stream events
  carry no call id) — concurrent same-name calls mis-attribute.
- Busy = input locked (`submitText` returns); no message queueing.
- The `ui/` kit (ModalProvider/Confirm/Form/MultiSelect/Toast) is built but
  only used by the `--demo` screen; the REPL still runs a 24-state
  `pickerKind` enum.

## What we adopt, and from where

From **Claude Code** (information density — see references doc for file
pointers into `references/claude-code-leaked/src`):

1. **Tool renderer interface.** Each tool gets render hooks (use-line /
   progress / result / error), with one shared shell: status dot
   (`⏺` queued-dim / running-blink / done-green / error-red) + bold name +
   parenthesized arg summary, result indented under a `⎿` gutter. Two
   glyphs carry all tool state; no boxes.
2. **"3 lines + `… +N lines (ctrl+o to expand)`" truncation**, with their
   two refinements: if only 1 line is hidden, just show it; pre-truncate
   huge outputs by chars and estimate the remaining line count.
3. **Quantified one-line summaries** per tool instead of ellipsis cuts:
   `Read 52 lines`, `Added 5 lines, removed 2 lines` + diff, `Found 8
   files`, `Done (12 calls · 48k tokens · 2m 10s)` for sub-runs.
4. **ctrl+o = frozen-snapshot transcript screen** (a separate Screen state,
   not in-place expansion): freeze the message list, re-render everything
   expanded, footer with exit hints; ctrl+e inside = show-all (no
   truncation at all).
5. **Composite spinner line**: `✻ verb… (esc to interrupt · 42s · ↓ 3.2k
   tokens)` with progressive width gating — we already have the spinner
   and the token stats, this is a merge.
6. **Queued messages while busy**: typing during a run queues the message
   (dim, above the input), ↑ recalls it for editing, queue flushes between
   turns.

From **opencode** (interaction architecture — see references doc for
pointers into `references/opencode/packages/opencode/src`):

7. **Command registry as single source of truth**: one declaration per
   command (name/title/category/keybind/slash-name/enabled) drives
   keybindings, the ctrl+k palette, slash commands, and live key hints in
   footers. Fixes the existing registry/handler drift (`/branch` etc.
   implemented but unlisted; `/memory` etc. listed but stubbed).
8. **Keybind definitions table + user overrides**: defaults + descriptions
   declared once, generating the config schema (fits the existing
   schema-driven settings design); unknown keys error; `"none"` disables.
9. **Question/permission prompts replace the input box** (a three-way slot:
   Prompt | QuestionPrompt | ApprovalPrompt) instead of a modal — the
   transcript stays visible and scrollable, esc semantics stay clear.
   This is the landing site for user-input-requests.md's TUI surface.
10. **In-row danger confirmation** (press again to confirm, row turns red)
    instead of nested confirm layers, for destructive picker actions.

Explicitly **not** adopted: switching renderers (OpenTUI). Our vendored
hermes-ink already has mouse tracking and ScrollBox; markdown lands via
marked-terminal; diff we write ourselves. Renderer swap is out of scope.

## Phases

### P0 — transcript density (pure TUI, no server changes)

- Tool render shell: `⏺` status dot + name + arg summary, `⎿` result
  gutter; per-tool renderers for bash/read/write/edit/grep + generic
  fallback (`tool [k=v, …]`).
- 3-line truncation with `… +N lines (ctrl+o to expand)` + the 1-line and
  huge-output refinements.
- ctrl+o transcript screen (frozen snapshot, all expanded, q/esc exits,
  ctrl+e show-all; reuse TranscriptViewport scrolling).
- Diff component (line numbers, add/remove coloring, 3 context lines);
  used by edit-style tool results and `/diff`.
- Memoize `renderMarkdown` per turn (cheap, big win under full-redraw).

Acceptance: a run with mixed tools reads as two-line entries; ctrl+o shows
everything; an edit shows a colored diff; long bash output folds with an
accurate +N count.

### P1 — interaction

- Queued messages while busy + ↑ to edit queue.
- Composite spinner/status line (verb · esc hint · elapsed · ↓ tokens).
- Command registry unification (palette/slash/keys from one table) and the
  keybind definitions table with `~/.openprogram` overrides; `?` shortcut
  help generated from the same table.

### P2 — fixes & convergence (needs small server changes)

- Server: include a `call_id` in tool stream events; TUI matches results by
  id (fixes concurrent same-name mis-attribution).
- Server: `conversation_loaded` carries tool blocks; `/resume` restores
  tool history.
- Question/approval prompts in the input slot — implements the TUI side of
  user-input-requests.md (handles `follow_up_question` and
  `approval_request`, makes the `ask` permission mode reachable).
- Migrate REPL pickers from `pickerKind` enum to ModalProvider/Form kit
  (mechanical; do opportunistically per picker).

## Risks

- The cell-grid renderer redraws everything per frame; P0 adds heavier
  per-turn rendering — memoization (markdown, diff) is part of P0, not an
  afterthought.
- ctrl+o as a global key must not collide with terminal flow control
  (it doesn't; ctrl+s/ctrl+q do).
- P2's server changes touch `_event_parsing.py` / dispatcher event
  emission — coordinate with the in-flight event_bus work.
