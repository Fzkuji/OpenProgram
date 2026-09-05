# Execution authority, permission, and sandbox

The sandbox is specified together with its upstream authority and permission contracts in the canonical [Execution Authority and Sandbox Design](sandbox-architecture.html).

The canonical document covers:

- the fixed `owner`, `paired`, and `mcp_browser` authority tiers;
- permission modes, project rules, interactive approval, and non-interactive denial;
- macOS Seatbelt, Linux bubblewrap, and Windows WSL2-to-bubblewrap enforcement;
- `auto` capability detection, including native unsandboxed fallback when the
  optional Windows WSL2 backend is unavailable and fail-closed behavior when
  `workspace-write` is selected explicitly;
- bypass and sandbox-escalation semantics;
- request sources, framework comparisons, failure behavior, implementation evidence, and known gaps.

This page is retained as a stable product-documentation link target. It is not a second sandbox specification.
