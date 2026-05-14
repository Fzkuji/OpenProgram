# Legacy → React Migration Plan

Goal: delete every file in `web/public/js/` and `web/public/html/`, remove the legacy bridge in `app-shell.tsx` (`SHARED_JS`, sidebar/right-sidebar HTML fetch) and `page-shell.tsx` entirely. End state: AppShell mounts pure React; no `innerHTML` from `/html/*`, no `<script src="/js/*">` injection.

Note: a second WS layer already exists (`lib/ws.tsx`) but is unused — `app-shell.tsx` still routes everything through `chat-ws.js`. That parallel implementation is the seed for the unified provider in Batch 1.

## Inventory

| Path (under web/public/) | LOC | Migration target / status |
|---|---|---|
| html/index.html | 114 | Becomes the React chat view (`<ChatPage />`); composer + welcome already React-portalled in |
| html/_sidebar.html | 47 | New `<LeftSidebar />` (replaces `app-sidebar.tsx` skeleton + legacy HTML) |
| html/_right-sidebar.html | 28 | New `<RightSidebar />` housing `<HistoryGraph />` + `<DetailPanel />` |
| js/shared/state.js | 48 | Delete — every var has a Zustand slot (`useSessionStore` / new `useWS`) |
| js/shared/helpers.js | 282 | Split: `lib/format.ts` (escHtml/escAttr/truncate/fmtTokens/formatUsage*), `lib/markdown.tsx` (renderMd + KaTeX), `lib/scroll.ts` (stickToBottom hook) |
| js/shared/sidebar.js | 72 | Inlined into `<LeftSidebar />` |
| js/shared/conversations.js | 1534 | Decomposed into: `<ConversationList />`, `<ChannelDropdown />`, `<BranchDropdown />`, `<BranchesPanel />`, `useChannelHealth()`, `useBranches()`, plus folding `loadSessionData / renderSessionMessages / handleAttemptSwitched` into the WS provider's `session_loaded` / `attempt_switched` reducers |
| js/shared/programs-panel.js | 115 | `<FavoritePrograms />` + `useFunctions()` hook; `clickFunction` / `clickFnExample` / `setInput` collapse into session-store actions (most already exist) |
| js/shared/providers.js | 618 | `<ModelBadge />` (exists), `<AgentSelector />`, `<TokenBadge />` (exists as ContextBadge), `<ProviderPills />`; cache-TTL logic into `useTokenBadge()` |
| js/shared/ui.js | 676 | Split per concern: `useRunningState` (running/paused/send button), `<StatusBadge />`, `<PlusMenu />` + `<ThinkingMenu />` (already inside Composer), `<DetailPanel />`, `<CodeModal />`, popover coordinator hook |
| js/shared/scrollbar.js | 166 | Either keep as a tiny vendored hook (`useOverlayScrollbar`) attached to scroll containers, or drop entirely in favour of native styled scrollbars |
| js/shared/right-dock.js | 98 | `useRightDock()` store slice (open + view + localStorage) + `<RightSidebar />` reads it |
| js/shared/history-graph.js | 803 | `<HistoryGraph />` — pure SVG render from `session.graph + head_id` in store; biggest single component, but self-contained |
| js/chat/chat.js | 366 | Composer already owns send/retry/follow-up. Migrate `addRuntimeBlockPending`, `addUserMessage`, `_injectPauseRetryButtons`, `stopAndRetry`, `retryCurrentBlock` into the message reducer (server-driven render, no DOM mutation) |
| js/chat/chat-ws.js | 947 | The huge `_handleStreamEvent` / `_handleRuntimeResult` / `_handleChatResult` / `_handleContextStats` / `_handleTreeUpdate` / `_handleFollowUpQuestion` / `_handleRetryResult` reducers move into the unified `WSProvider` and feed a normalized message tree in the store. `MessageBubble` renders thinking/tools/text scaffold declaratively |
| js/chat/init.js | 652 | Connection/reconnect/keepalive go to `WSProvider`. `handleMessage` switch turns into typed reducers. Column resize → `useColResize` hook. `__triggerPendingRunFunction` (programs→chat hand-off) becomes `useEffect` on `searchParams` in `<ChatView />` |
| js/chat/tree.js + tree-render.js + tree-retry.js + tree-log.js | 145 + 243 + 137 + 176 | Folded into a single `<InlineTree />` + `<RetryPanel />` + `useTreeMerge()` reducer; `ContextTreePanel` already partially does this |
| js/chat/workdir.js | 83 | Already replaced by `fn-form.tsx`; just delete + remove from `JS_FILES_BY_PAGE.chat` |
| js/chat/message-actions.js | 353 | `<MessageActions />` already shipped (`MessageBubble` has copy/retry/branch). Final migration: delete the file once nothing else dispatches via `data-action` |
| js/chat/message-actions-edit.js | 131 | Becomes an `<InlineEdit />` mode on `<MessageBubble user />` driven by store flag `editingMsgId` |
| js/chat/message-actions-nav.js | 123 | `<SiblingNav />` reading `messagesById[id].sibling_index/sibling_total` already in store-friendly shape |

## Cross-cutting concerns to fix first

These globals are written by legacy, read by React (or vice versa). Each must collapse to one owner before the corresponding files can be deleted.

- `window.ws` — single open socket today. Replace with `WSProvider` in `lib/ws.tsx` becoming the only opener; `wsSend` helper in `composer.tsx` switches to `useWS().send`. AppShell stops loading any of `init.js / chat-ws.js`.
- `window.currentSessionId` — already mirrored both ways by `app-shell.tsx` and `init.js`. Single source: `useSessionStore.currentSessionId`, derived from `pathname` in `AppShell`.
- `window.availableFunctions` / `window.programsMeta` — move to `useProgramsStore` (file already exists in `lib/programs-store.ts`); hydrate on `functions_list` event in WSProvider.
- `window._toolsEnabled` / `window._webSearchEnabled` / `window._thinkingEffort` / `window._execThinkingEffort` — already in Composer state; legacy reads of these will disappear with `chat.js` / `ui.js` deletion.
- `window._pendingChannelChoice` / `window._pendingRunFunction` — store slots `pendingChannelChoice`, `pendingRunFunction`; the `/programs → /chat` hand-off goes through `searchParams.get("run")` instead of a global.
- `window.conversations` / `window.trees` / `window._allMessages` / `window.expandedNodes` / `window._nodeCache` / `pendingResponses` — already half-mirrored in `messagesById / messageOrder / trees`; finish migration during Batch 3 (chat-ws reducer rewrite).
- `window.rightDock`, `window.renderHistoryGraph`, `window.renderBranchesPanel`, `window._closeAllPopovers`, `window.refreshTokenBadge`, `window.refreshStatusSource`, `window.refreshChannelBadge`, `window.refreshBranchBadge`, `window.setStatusDotHealth`, `window.ensureMessageActions`, `window.makeMessageEditButton`, `window.ensureSiblingNav`, `window.__sessionStore`, `window.__navigate`, `window.__triggerPendingRunFunction`, `window.__pendingRunFunction`, `window._pendingUserBubble`, `window._postCheckoutScrollTo`, `window._branchLaneColorMap`, `window._branchesNextScrollToHead`, `window._branchesPanelCollapsed`, `window._webSearchProviderLabel`, `window._webSearchProviderTier`, `window._sessionStore` — all delete when their owners migrate.

## Batches (parallelizable within each, sequential between)

### Batch 1 — Unified WSProvider + store consolidation (foundation)

Single sequential block: every later batch consumes the new socket. Two agents max.

- Agent A: rewrite `lib/ws.tsx` so its `WSProvider` is the only socket opener. Move every reducer from `chat-ws.js` / `init.js` (`handleMessage` switch on `full_tree / event / functions_list / history_list / chat_ack / chat_response / session_loaded / session_reload / attempt_switched / sessions_list / channel_accounts / branches_list / branch_checked_out / branch_renamed / branch_name_deleted / branch_deleted / session_channel_updated / status / running_task / provider_info / provider_changed / agent_settings_changed / chat_session_update`) into typed handlers writing to `useSessionStore`. Mount `<WSProvider>` inside `app-shell.tsx`. Add `useWS()` send helper exposing `send / ack / chat / runFunction`.
- Agent B: extend `lib/session-store.ts` with: `functions`, `programsMeta`, `branchesByConv`, `branchTokensByConv`, `channelAccounts`, `pendingChannelChoice`, `pendingRunFunction`, `agentSettings`, `thinkingEffort/execThinkingEffort`, `rightDock {open, view}`, `popoverOwner` (mutual-exclusion), `welcomeVisible` (already there), `editingMsgId`. Reducers for `setRunActive`, `setRunningTask`, `setPaused`, `recordCacheWrite`. Subscribe Composer + ChatView via `useWS()` and store hooks (delete `wsSend` shim).

At end of Batch 1, `app-shell.tsx` keeps fetching `_sidebar.html` + `_right-sidebar.html` but `SHARED_JS` shrinks to zero — the WS layer is React-owned.

### Batch 2 — Pure-display surfaces (parallel)

- Agent C: replace `_sidebar.html` + `sidebar.js` + `programs-panel.js` + `conversations.js::renderSessions/switchSession/deleteSession/clearAllSessions/newSession` with a self-rendering `<LeftSidebar />` (uses existing `app-sidebar.tsx` as skeleton, mounts inside AppShell's flex row). Includes `<FavoritePrograms />`, `<ConversationList />`, `<UserMenuFooter />` (already React). Remove the `sidebarRef.innerHTML = ...` block from AppShell.
- Agent D: replace `_right-sidebar.html` + `right-dock.js` with `<RightSidebar />` (icon rail + content host). Children: `<HistoryGraphPanel />` placeholder (Batch 4 fills it), `<DetailPanel />` placeholder. Wire `useRightDock` slice. Remove the `rightSidebarRef.innerHTML = ...` block from AppShell.
- Agent E: split `helpers.js` into `lib/format.ts` (esc, truncate, fmtTokens, formatUsage*, parseRunCommandForDisplay), `lib/markdown.tsx` (`<Markdown />` component wrapping `marked` + KaTeX auto-render), `lib/scroll.ts` (`useStickToBottom`). All later batches import from these.
- Agent F: turn `providers.js` token-badge logic into `<TokenBadge />` (extends existing `context-badge.tsx`) + `useTokenBadge()` hook including 5-min cache TTL timer.

### Batch 3 — Chat surface (parallel after Batch 2)

- Agent G: rewrite `<ChatView />` so it renders the legacy `index.html` body in React: topbar, `#chatMessages` host, `<Composer />`, `<WelcomeScreen />`. Topbar pieces: `<StatusBadge />` (replaces conversations.js channel dropdown + ui.js status badge), `<BranchBadge />` (opens `<BranchDropdown />`), `<ChatAgentBadge />` + `<ExecAgentBadge />` (open `<AgentSelector />`). All read from store; no DOM IDs needed.
- Agent H: `<MessageList />` renders `messageOrder[sessionId].map(id => <MessageBubble msgId={id} />)`. `<MessageBubble />` already exists but needs the streaming-scaffold (thinking / tool_use / tool_result fold-outs), runtime-block variant, error variant with retry, follow-up-question variant, restored-runtime merging (`_buildRestoredRuntimeBlock`). Adopts `<InlineTree />` and `<SiblingNav />`.
- Agent I: `<InlineTree />` (from tree-render.js) — pure renderer over `trees[sessionId]` in store. Retry panel becomes `<RetryPanel />` opened on a node id. Tree merge logic from tree.js becomes `mergeTreeNode` reducer in store. Delete `tree-log.js` (legacy live exec log) — `<MessageBubble streamingScaffold />` covers it.
- Agent J: code modal + `viewSource` flow → `<SourceModal />` (already exists at `programs/source-modal.tsx`; just route the chat-side `viewSource(name)` calls through it via a store action).

At end of Batch 3, the chat page is React-only. PageShell can drop the chat-specific JS list and the `extractMainArea` HTML strip — but PageShell still exists for the unused `settings/programs/chats` keys (already empty).

### Batch 4 — Right rail and remaining shared (parallel)

- Agent K: `<HistoryGraph />` from history-graph.js. SVG-only, pure render of `conversations[id].graph + head_id`. Tooltip + click-to-checkout dispatch `checkout_branch` via `useWS().send`. Stash lane-color map in the rightDock slice instead of `window._branchLaneColorMap`.
- Agent L: `<DetailPanel />` (showDetail/closeDetail from ui.js) and `<BranchesPanel />` (renderBranchesPanel from conversations.js) — both read store, no DOM IDs.
- Agent M: `<MessageActions />` finalization — the legacy delegated handler in `message-actions.js / message-actions-edit.js / message-actions-nav.js` becomes per-bubble buttons (MessageBubble already wires copy/retry/branch). Add `<InlineEdit />` mode for user bubbles.
- Agent N: delete `scrollbar.js`. If overlay scrollbars are wanted, rewrite as `lib/use-overlay-scrollbar.ts` hook applied to `<ScrollArea />`. Otherwise just style native scrollbars in CSS.

### Batch 5 — Demolition

Sequential cleanup once Batches 1–4 land.

- Agent O: delete `web/public/js/` and `web/public/html/` entirely.
- Agent O cont.: from `app-shell.tsx` remove `SHARED_JS`, `EXTERNAL_LIBS` (move KaTeX/marked imports into `lib/markdown.tsx`), `loadExternalScript`, `fetchInlineScript`, `injectInlineScript`, `loadStylesheet`, sidebar/right-sidebar fetch effects, `userMenuFooterMount`/`composer-mount`/`welcome-mount` portal scaffolding, `__sessionStore` / `__navigate` globals. Replace with pure JSX children.
- Agent O cont.: delete `page-shell.tsx` entirely; its routes (`/chat`, `/s/[sessionId]`, `/programs`, `/chats`, `/settings/*`) render their own React subtrees directly under `app/(shell)/layout.tsx`.
- Agent O cont.: drop `<link rel="stylesheet" href="/css/style.css">` from anywhere referencing it and ensure module CSS covers what remains.

## Migration difficulty summary

Trivial deletes (no rewrite, just unwire): workdir.js, state.js, tree-log.js, scrollbar.js.

Medium (1:1 React component, existing skeleton): _sidebar.html, _right-sidebar.html, sidebar.js, programs-panel.js, right-dock.js, helpers.js split, message-actions* trio, providers.js (token-badge half), tree-retry.js.

Large (significant logic, multiple consumers): chat-ws.js (947 LoC reducer), conversations.js (1534 LoC; channel + branches + session-render are three sub-domains), history-graph.js (803 LoC pure render but lots of layout math), init.js (WS lifecycle + column resize + run-handoff), ui.js (popover coordinator + detail panel + plus/thinking menus + code modal).

Total: ~7900 lines of legacy collapse into roughly 25 new React components + 8 store slices + 6 hooks. Five batches; Batches 2–4 each parallelize to 3–4 agents.
