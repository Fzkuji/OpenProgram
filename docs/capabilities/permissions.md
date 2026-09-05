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

## Change permissions during a task

An existing session's selection is sent to the server and confirmed before the interface displays it as effective. The change applies at the next tool approval check and is included in subsequent model requests. A tool already authorized for execution keeps that authorization. Switching into Plan mode prevents pending write calls from being authorized.

An ordinary approval that is no longer required is automatically resolved through the execution's durable wait and the task continues. Explicit ask rules, mandatory approvals, user questions, forms, and Sandbox escalation are not answered by changing the mode. Repeated answers, cancellation, timeout, and recovery cannot execute an already completed call again.

Changes are session-specific. Other windows receive the confirmed mode; stale updates are rejected rather than overwriting a newer choice. A failed or disconnected update remains unconfirmed. Reconnect and review the current mode before retrying. An unsent draft stores its mode locally and sends it with the first message.

The effective default is the session override, then the project default, then Ask permissions. Background tasks and external channels retain their own identity and non-interactive restrictions; changing a local session to Bypass does not grant them owner permissions.

## Understand a refusal

For Windows path rules, forward slashes avoid escaping ambiguity: for example,
`read(C:/Users/me/project/**)`. Ordinary backslashes in drive paths are preserved;
the rule syntax still uses `\\`, `\(`, and `\)` to escape its own special characters.
JSON configuration additionally requires JSON's normal backslash escaping.

A tool result identifies whether the refusal came from authority, a permission rule, Plan mode, Auto classification, or Sandbox. `AUTHORITY_TIER_MISSING` means the execution request lacks an authenticated authority tier. It is not resolved by Bypass. New authenticated chat submissions carry that identity; old admitted executions missing identity must be resubmitted through an authenticated interface.

Sandbox is configured separately in the composer Plus menu. Changing Sandbox affects subsequent turns. Bypass keeps the current sandbox restrictions, and an ordinary tool approval never authorizes a sandbox escalation. When a sandbox denial cannot open a separate escalation wait, change the relevant settings explicitly and submit a new call.

See [tools](tools.md), [Web](../interfaces/web.md), [terminal](../interfaces/tui.md), and the [engineering contract](../reference/design/runtime/permission-model.md).
