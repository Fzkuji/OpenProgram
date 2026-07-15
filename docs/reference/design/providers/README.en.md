# Providers

Design docs for the LLM provider layer. Providers translate the framework's internal unified context (`Context`: system / messages / tools) into each vendor's API request, handling authentication, caching, errors, and the model catalog.

The docs are organized into four groups by responsibility (three existing subdirectories plus one core group still to be filled in):

## Translation + Caching (core, partially to be filled in)

How the provider-agnostic unified format is translated into each vendor's wire format, and how prompt caching is implemented per provider — the core mechanism of the providers layer.

- [`request-build`](request-build.md) — **Overall design**: the unified-format Context, per-provider translation, the three caching modes, current state, and three gaps.
- [`cache-control-passthrough`](../plans/cache-control-passthrough.md) (in `docs/plans/`) — landed: per-block passthrough of Anthropic `cache_control`.
- For upstream (how content is layered and assembled, L0/L1/L2) see [`context/context-composition.md`](../context/context-composition.md).

## [auth/](auth/) — Credentials · Authentication · Accounts

Resolution, validation, and storage of API keys and subscription OAuth, plus the multi-account pool and rotation.

- [`credential-validation-unification`](auth/credential-validation-unification.md) — Unified credential-validation entry point
- [`credential-status-redesign`](auth/credential-status-redesign.md) — Credential status ("available / disabled", dropping COOLING)
- [`api-key-resolution-unification`](auth/api-key-resolution-unification.md) — Unified API key resolution chain
- [`unified-auth-storage`](auth/unified-auth-storage.md) — Self-contained auth storage
- [`unified-account-management`](auth/unified-account-management.md) — Multi-account management + pool rotation/fallback
- [`claude-code-direct-oauth`](auth/claude-code-direct-oauth.md) — claude-code subscription OAuth direct connection (dropping Meridian)

## [reliability/](reliability/) — Fault tolerance · Errors · Retries · Timeouts

Classification, retries, timeouts, and upward propagation of errors when a model call fails.

- [`llm-fault-tolerance`](reliability/llm-fault-tolerance.md) — Overall design for fault tolerance and timeouts
- [`error-retry`](reliability/error-retry.md) — Error handling and retry decisions
- [`error-taxonomy-propagation`](reliability/error-taxonomy-propagation.md) — Structured errors propagated all the way to the UI
- [`error-and-timeout-mechanism.html`](reliability/error-and-timeout-mechanism.html) — Visualization of the error/timeout mechanism

## [models/](models/) — Model catalog · Capabilities

The data layout and configuration structure of the model list, plus the declarative mapping of capabilities like thinking/effort. Every model is bound to the provider it belongs to, so it lives under providers.

- [`models`](models/models.md) — Model catalog and provider configuration (data layout, fetch, merge)
- [`thinking-effort`](models/thinking-effort.md) — The thinking/effort subsystem (declarative per-provider mapping)
- [`fast-tier`](models/fast-tier.en.md) — the Fast tier: two-tier detection (hand-written subscription entries / models.dev auto), storage, wires
