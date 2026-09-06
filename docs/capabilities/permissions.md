# Control tool permissions

Use the permission menu in Web or the installed App, or `/permissions` in the terminal. `Shift+Tab` cycles the terminal's ordinary modes. Bypass requires its existing explicit enable action.

| Mode | What proceeds automatically |
| --- | --- |
| Ask permissions | Safe read tools; other operations ask for approval. |
| Accept edits | Safe reads and supported edits inside working directories. Commands still ask. |
| Plan mode | Operations allowed by the read-only plan tool policy. |
| Auto mode | Safe tools and operations accepted by the risk classifier. |
| Bypass permissions | Ordinary operations without approval or risk classification. |

Bypass does not override explicit deny or ask rules, mandatory plan-exit or self-update approval, plugin restrictions, identity capabilities, or Sandbox.

Approval choices appear at the bottom left of the request. Select Allow once,
Always allow or Deny, then use Send on the right to submit. Chat about this sits
next to Send and keeps the existing decline-and-return-to-chat behavior.
On narrow windows, these controls wrap within the same bottom action area.

## Change permissions during a task

An existing session's selection is sent to the server and confirmed before the interface displays it as effective. The interface sends the change immediately when it already knows the confirmed session version. Once the server confirms Bypass, subsequent ordinary tool calls do not ask for approval, even if the model is still reasoning, streaming text or generating tool arguments. This also applies to later tools in the same response. You do not need to stop generation or send another message. The new mode is included in subsequent model requests; text already being generated is not rewritten. A tool already authorized for execution keeps that authorization. Switching into Plan mode prevents pending write calls from being authorized.

An ordinary approval that is no longer required is automatically resolved through the execution's durable wait and the task continues. Explicit ask rules, mandatory approvals, user questions, forms, and Sandbox escalation are not answered by changing the mode. Repeated answers, cancellation, timeout, and recovery cannot execute an already completed call again.

Changes are session-specific. Other windows receive the confirmed mode; stale updates are rejected rather than overwriting a newer choice. A failed or disconnected update remains unconfirmed. Reconnect and review the current mode before retrying. An unsent draft stores its mode locally and sends it with the first message.

The effective default is the session override, then the project default, then Ask permissions. A sub-agent created by an authenticated owner Agent inherits the parent’s effective permission mode and explicit rules at creation. For example, a parent using Bypass can create a sub-agent that runs ordinary commands without approval. The sub-agent keeps its own identity and non-interactive restrictions; explicit ask rules and mandatory approvals still prevent operations that require an approver. Later mode changes do not rewrite already admitted sub-agents. Independent scheduled tasks and external channels do not acquire owner permissions or Bypass from a local session.

## Understand a refusal

For Windows path rules, forward slashes avoid escaping ambiguity: for example,
`read(C:/Users/me/project/**)`. Ordinary backslashes in drive paths are preserved;
the rule syntax still uses `\\`, `\(`, and `\)` to escape its own special characters.
JSON configuration additionally requires JSON's normal backslash escaping.

A tool result identifies whether the refusal came from authority, a permission rule, Plan mode, Auto classification, or Sandbox. `AUTHORITY_TIER_MISSING` means the execution request lacks an authenticated authority tier. It is not resolved by Bypass. New authenticated chat submissions carry that identity; old admitted executions missing identity must be resubmitted through an authenticated interface.

Sandbox is configured separately in the composer Plus menu. Changing Sandbox affects subsequent turns. Bypass keeps the current sandbox restrictions, and an ordinary tool approval never authorizes a sandbox escalation. When a sandbox denial cannot open a separate escalation wait, change the relevant settings explicitly and submit a new call.

See [tools](tools.md), [Web](../interfaces/web.md), [terminal](../interfaces/tui.md), and the [engineering contract](../reference/design/runtime/permission-model.md).
