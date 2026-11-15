# Agent permissions

This package owns permission decisions and their execution lifecycle. Import
session-setting and live-update operations from `openprogram.runtime.permissions`.
Tool adapters import `approval.wrap_with_approval`; interfaces do not implement
permission policy independently.

| Module | Responsibility |
| --- | --- |
| `rule.py` | Rule strings: parsing, printing, command matching and merged global/project/session rule sets |
| `state.py` | Persisted modes, authenticated owner validation and atomic version checks |
| `policy.py` | Shared decisions, explicit rules, Plan restrictions and hard constraints |
| `classifier.py` | Auto-mode risk classification; only JSON boolean `true` grants approval |
| `approval.py` | Approval manifests, exact wait consumption and authorized tool execution |
| `lifecycle.py` | Per-call snapshots, confirmed live changes and durable wait reconciliation |
| `modes.py` | Permission modes (`VALID_PERMISSION`, `DEFAULT_PERMISSION`) and `PermissionRules`; session settings re-export them |
| `session_facts.py` | The session and turn facts permission code reads, registered by the conversation package |

`authority.py` is the identity core: authority fields and constructors, the
fixed tier-capability table (`decide_capability`, `has_capability`) and the
model-visible speaker envelope. It names no tools. `tool_authority.py` maps a
tool call onto one capability (`capability_for_tool`) and decides it
(`decide_tool_authority`). `attended.py` withholds the question tool when
nobody is watching.

The package sits below the call core and the conversation package and
imports neither. A turn request is any object with `TurnRequest`'s fields;
tools are `AgentTool` from `openprogram.models.providers.types`. What only a
conversation knows (the bound session and execution, a running turn, plan
mode, session-layer rules, project defaults, surface grants, the Agent's
outbound hold, the session's chat model, an execution's turn request) is
read through `session_facts.facts()`, which
`openprogram.runtime.conversation.permission_registration` registers on
import. Process isolation remains
in `sandbox/`. `SessionStore` owns settings and the execution wait store owns
approval outcomes. Permission updates do not mutate admission identity or reuse
one operation's approval for another operation.
