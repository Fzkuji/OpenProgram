# Web UI

Web UI surfaces — the surface system, indicator dots, attachment handling, chat-turn visuals, and GUI-agent context flow.

- [`invariants.md`](invariants.md) — cross-module UI invariants (walk the list before touching related modules)
- [`chat-turn-visual-spec.html`](chat-turn-visual-spec.html) — chat-turn visual spec (execution timeline + manual function runs + message minimap; interactive demo, single source of truth)
- [`interaction-feedback.md`](interaction-feedback.md) — the 0ms interaction-feedback rule (optimistic state first, data backfills)
- [`state-layer.md`](state-layer.md) — web state layer: one store instance per session, with genuinely shared data global
- [`built-in-browser.html`](built-in-browser.html) — built-in browser home, browser-profile import, compact History, and the four-entry new-pane launcher
- [`composer-interaction-modes.md`](composer-interaction-modes.md) — composer interaction modes
- [`attachment-handling.md`](attachment-handling.md) — attachment handling: how an attached file reaches the model ([rendered](attachment-handling.html))
- [`chat-attachments.html`](chat-attachments.html) — chat attachments both ways: what the transcript shows, how the agent hands a file back, how a readable file opens
- [`gui-agent-context.md`](gui-agent-context.md) — GUI agent context flow
- [`indicator-dots.md`](indicator-dots.md) — indicator dots
- [`surface-system.md`](surface-system.md) — surface system
- [`web-styles.md`](web-styles.md) — web style organization (one component, one file; directories mirror the component tree)
