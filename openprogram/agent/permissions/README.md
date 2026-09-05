# Agent permissions

This package owns permission decisions and their execution lifecycle. Import
session-setting and live-update operations from `openprogram.agent.permissions`.
Tool adapters import `approval.wrap_with_approval`; interfaces do not implement
permission policy independently.

| Module | Responsibility |
| --- | --- |
| `state.py` | Persisted modes, authenticated owner validation and atomic version checks |
| `policy.py` | Shared decisions, explicit rules, Plan restrictions and hard constraints |
| `classifier.py` | Auto-mode risk classification; only JSON boolean `true` grants approval |
| `approval.py` | Approval manifests, exact wait consumption and authorized tool execution |
| `lifecycle.py` | Per-call snapshots, confirmed live changes and durable wait reconciliation |

Identity capabilities remain in `agent/authority.py`. Process isolation remains
in `sandbox/`. `SessionStore` owns settings and the execution wait store owns
approval outcomes. Permission updates do not mutate admission identity or reuse
one operation's approval for another operation.
