# TUI Transcript Rendering and Interaction

> This document is the design of the Ink TUI's transcript display and
> interaction: how tool calls render, how output folds and expands, how
> commands and keybindings are declared, and how mid-run prompts are
> presented. Companion:
> [user-input-requests.md](../runtime/operations/user-input-requests.md)
> (mid-run questions; their TUI surface is specified here).

## 1. Goal

The transcript should show tool calls that collapse without losing
information, offer a ctrl+o expanded transcript view, render diffs
structurally, queue messages while the agent is busy, and expose keybindings
that a user can discover and override.

## 2. Starting point

The TUI base provides a vendored hermes-ink cell-grid renderer with mouse
tracking and ScrollBox, four themes with live preview, a BottomBar showing
tokens / context% / cache / permission mode, a command palette (ctrl+k),
fish-style autosuggest, `@file` completion, per-session drafts, and the
account and channel flows. The gaps this design closes are concentrated in
the transcript itself:

- Tool output folds hard at 6 lines (`Turn.tsx` MAX_LINES) with no way to
  expand it — no keybind, no verbose mode.
- Tool args are one truncated line; there is no per-tool rendering, so every
  tool looks the same.
- There is no diff rendering; `/diff` prints raw `git diff` text.
- Streaming text shows raw markdown source and jumps visually when the final
  render lands (`Turn.tsx:123`). Committed text is memoized per mounted text
  segment and terminal width under the full-redraw renderer.
- `follow_up_question` and `approval_request` envelopes are typed in
  `ws/client.ts` but dropped without notice, so agent questions time out and
  the `ask` permission mode is unreachable (shift+tab only cycles
  bypass↔auto).
- `/resume` rebuilds the transcript from role and content only, losing tool
  history (`useWsEvents.ts:326-336`).
- Tool results are matched to calls by tool name, because server stream
  events carry no call id, so concurrent same-name calls are mis-attributed.
- Busy means input is locked (`submitText` returns early); there is no
  message queueing.
- The `ui/` kit (ModalProvider / Confirm / Form / MultiSelect / Toast) is
  built but used only by the `--demo` screen; the REPL runs a 24-state
  `pickerKind` enum.

## 3. Transcript rendering

Information density follows Claude Code's approach (file pointers into
`references/claude-code-leaked/src` are in the references document).

1. **Tool renderer interface.** Each tool gets render hooks (use-line,
   progress, result, error) sharing one shell: a status dot (`⏺` dim when
   queued, blinking while running, green on completion, red on error), the
   bold tool name, a parenthesized arg summary, and the result indented
   under a `⎿` gutter. Two glyphs carry all tool state; there are no boxes.
2. **Truncation at 3 lines** with `… +N lines (ctrl+o to expand)`, plus two
   refinements: if only one line is hidden, show it instead; pre-truncate
   huge outputs by character count and estimate the remaining line count.
3. **Quantified one-line summaries** per tool rather than ellipsis cuts:
   `Read 52 lines`, `Added 5 lines, removed 2 lines` followed by a diff,
   `Found 8 files`, and `Done (12 calls · 48k tokens · 2m 10s)` for sub-runs.
4. **ctrl+o opens a frozen-snapshot transcript screen** — a separate Screen
   state rather than in-place expansion. It freezes the message list,
   re-renders everything expanded, and shows exit hints in the footer;
   ctrl+e inside it shows everything with no truncation at all.
5. **A composite spinner line**: `✻ verb… (esc to interrupt · 42s · ↓ 3.2k
   tokens)`, with progressive width gating. The spinner and the token stats
   already exist separately; this merges them.
6. **Queued messages while busy**: typing during a run queues the message
   (dim, above the input), ↑ recalls it for editing, and the queue flushes
   between turns.

Diff rendering is written in-house: line numbers, add/remove coloring, three
context lines, used by edit-style tool results and by `/diff`. Markdown
rendering goes through marked-terminal and is memoized per turn, which
matters under a renderer that redraws every frame.

The renderer itself is not replaced. The vendored hermes-ink already has
mouse tracking and ScrollBox, so switching to OpenTUI buys nothing here.

## 4. Command and interaction architecture

Interaction structure follows opencode (pointers into
`references/opencode/packages/opencode/src` are in the references document).

7. **One command registry as the single source of truth.** Each command is
   declared once (name, title, category, keybind, slash-name, enabled), and
   that declaration drives keybindings, the ctrl+k palette, slash commands,
   and the live key hints in footers. A single table is what keeps registry
   and handlers from drifting apart — `/branch` implemented but unlisted,
   `/memory` listed but stubbed.
8. **A keybind definitions table with user overrides.** Defaults and
   descriptions are declared once and generate the config schema, which fits
   the existing schema-driven settings design. Unknown keys are an error;
   `"none"` disables a binding.
9. **Question and permission prompts replace the input box** through a
   three-way slot (Prompt | QuestionPrompt | ApprovalPrompt) instead of a
   modal. The transcript stays visible and scrollable and esc keeps a single
   clear meaning. This is the TUI surface of user-input-requests.md, handling
   `follow_up_question` and `approval_request` and making the `ask`
   permission mode reachable.
10. **In-row danger confirmation** for destructive picker actions: pressing
    the key again confirms and the row turns red, instead of a nested
    confirm layer.

REPL pickers move from the `pickerKind` enum onto the ModalProvider/Form kit;
the change is mechanical and can be done one picker at a time.

## 5. Server support

Two pieces of the design need data the server does not currently send:

- Tool stream events carry a `call_id`, so the TUI matches results by id
  rather than by name and concurrent same-name calls are attributed
  correctly.
- `conversation_loaded` carries tool blocks, so `/resume` restores tool
  history.

Both touch `_event_parsing.py` and dispatcher event emission, which is shared
with the event-bus work.

## 6. Constraints

- The cell-grid renderer redraws everything per frame, so the heavier
  per-turn rendering above depends on memoizing markdown and diff output.
- ctrl+o is safe as a global key: terminal flow control uses ctrl+s and
  ctrl+q.

## Appendix: Implementation Status

Research is complete; implementation is in progress. Markdown output is
memoized by streaming state, text, and terminal width for each mounted text
segment, so updates to a separate streaming turn do not parse unchanged
committed text again while terminal resizes still re-render width-sensitive
Markdown. The remaining work follows this order:

- **P0 — transcript density** (pure TUI, no server changes): tool render
  shell, 3-line truncation, the ctrl+o transcript screen, and the diff
  component remain. Accepted when a run with mixed tools
  reads as two-line entries, ctrl+o shows everything, an edit shows a
  colored diff, and long bash output folds with an accurate +N count.
- **P1 — interaction**: queued messages with ↑ editing, the composite
  spinner/status line, command registry unification, the keybind definitions
  table with `~/.openprogram` overrides, and `?` shortcut help generated
  from the same table.
- **P2 — server-dependent items**: `call_id` in tool stream events, tool
  blocks in `conversation_loaded`, question/approval prompts in the input
  slot, and the REPL picker migration.
