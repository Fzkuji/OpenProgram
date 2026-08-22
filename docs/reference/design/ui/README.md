# Web UI

Web UI surfaces — the surface system, indicator dots, attachment handling, chat-turn visuals, and GUI-agent context flow.

- [`invariants.md`](invariants.md) — cross-module UI invariants (walk the list before touching related modules)
- [`chat-turn-visual-spec.html`](chat-turn-visual-spec.html) — chat-turn visual spec (execution timeline, file-change summary surface, manual function runs, and message minimap); file-history semantics live in the [canonical runtime design](../runtime/operations/file-management.html)
- [`interaction-feedback.md`](interaction-feedback.md) — the 0ms interaction-feedback rule (optimistic state first, data backfills)
- [`turn-occupancy.md`](turn-occupancy.md) — stop, send-queue, and session-slot occupancy (released on cancel intent)
- [`state-layer.md`](state-layer.md) — web state layer: one store instance per session, with genuinely shared data global
- [`center-tabs-and-split-layout.html`](center-tabs-and-split-layout.html) — authoritative single-tab and composite split-tab lifecycle, rendering, persistence, and transfer design
- [`built-in-browser.html`](built-in-browser.html) — built-in browser home, browser-profile import, compact History, and the four-entry new-pane launcher
- [`browser-extensions.html`](browser-extensions.html) — product decision not to support Chrome/Edge extension installation or management, retained browser capabilities, and the inert legacy-data boundary
- [`integrated-terminal.html`](integrated-terminal.html) — real PTY terminal and direct local Claude Code launcher
- [`composer-local-attachment-paths.html`](composer-local-attachment-paths.html) — local attachment path preservation from the composer to model context
- [`composer-responsive-controls.html`](composer-responsive-controls.html) — responsive composer controls and compact-state interaction contract
- [`composer-tool-profile-menu.html`](composer-tool-profile-menu.html) — Tools action and profile submenu behavior
- [`programs-source-categories.html`](programs-source-categories.html) — Programs grouping and source-category behavior
- [`composer-interaction-modes.md`](composer-interaction-modes.md) — composer interaction modes
- [`attachment-handling.md`](attachment-handling.md) — attachment handling: how an attached file reaches the model ([rendered](attachment-handling.html))
- [`chat-attachments.html`](chat-attachments.html) — chat attachments both ways: what the transcript shows, how the agent hands a file back, how a readable file opens
- [`gui-agent-context.md`](gui-agent-context.md) — GUI agent context flow
- [`indicator-dots.md`](indicator-dots.md) — indicator dots
- [`surface-system.md`](surface-system.md) — surface system
- [`theme-system.html`](theme-system.html) — authoritative theme entry, complete token contract, component consumption, and desktop-overlay propagation
- [`settings-collapsible-columns.html`](settings-collapsible-columns.html) — independent 49px collapse for the app and Settings nav; Providers list stays expanded with a full-width search field
- [`avatar-randomization.html`](avatar-randomization.html) — shared Agent/User avatar picker: type first, then same-style variants or letter fields
- [`web-styles.md`](web-styles.md) — web style organization (one component, one file; directories mirror the component tree)
- [`window-state.md`](window-state.md) — desktop window bounds, maximize/fullscreen, and titlebar resize hit-testing
- [`window-lifecycle.md`](window-lifecycle.md) — one main window: launch, Dock activate, and second-instance share a single create
