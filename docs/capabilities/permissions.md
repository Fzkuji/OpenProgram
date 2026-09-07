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

Tool approvals show the current operation and two actions: Allow once and Deny.
Clicking either submits that decision directly. The request closes only after
server confirmation; an unconfirmed answer can retry the same decision.
Persistent rules remain available in History project settings.

Human tool approvals have no default time limit. Waiting saves execution progress
and releases the active execution attempt; reopening the App restores the pending
request. Resuming uses the context saved for that turn, so later memory or date updates do not invalidate the approval. Tool, permission and working-directory checks still apply. If execution cannot resume, the conversation reports that the pending operation did not run. Cancelling the task withdraws its approval. Expired older requests are not
reactivated and cannot authorize a new operation.

For `edit`, `write`, and `apply_patch`, approval records the target files' state.
Before executing and immediately before writing, the tools check that state.
A deleted target is not recreated with the old approval. Changed files require
reading the current contents before proposing another operation. The UI shows
only the current proposal, without a before/after history. Arbitrary shell
commands do not declare all their file targets, so this file-specific check does
not cover arbitrary command side effects.

Ordinary questions retain their answer controls and Chat about this discussion
entry. Tool approval does not include a discussion editor.

## Change permissions during a task

An existing session's selection is sent to the server and confirmed before the interface displays it as effective. The interface sends the change immediately when it already knows the confirmed session version. Once the server confirms Bypass, subsequent ordinary tool calls do not ask for approval, even if the model is still reasoning, streaming text or generating tool arguments. This also applies to later tools in the same response. You do not need to stop generation or send another message. The new mode is included in subsequent model requests; text already being generated is not rewritten. A tool already authorized for execution keeps that authorization. Switching into Plan mode prevents pending write calls from being authorized.

An ordinary approval that is no longer required is automatically resolved through the execution's durable wait and the task continues. Explicit ask rules, mandatory approvals, user questions, forms, and Sandbox escalation are not answered by changing the mode. Repeated answers, cancellation, timeout, and recovery cannot execute an already completed call again.

Changes are session-specific. Other windows receive the confirmed mode; stale updates are rejected rather than overwriting a newer choice. A failed or disconnected update remains unconfirmed. Reconnect and review the current mode before retrying. An unsent draft stores its mode locally and sends it with the first message.

The effective default is the session override, then the project default, then Ask permissions. A sub-agent created by an authenticated owner Agent inherits the parent’s effective permission mode and explicit rules at creation. For example, a parent using Bypass can create a sub-agent that runs ordinary commands without approval. The sub-agent keeps its own identity and non-interactive restrictions; explicit ask rules and mandatory approvals still prevent operations that require an approver. Later mode changes do not rewrite already admitted sub-agents. Independent scheduled tasks and external channels do not acquire owner permissions or Bypass from a local session.

## Manage project rules in History

Open History → Projects, select the project, and open Settings → Permission Rules. The three lists contain that project's Deny, Ask and Allow rules. Add a rule with `ToolName` or `ToolName(pattern)` syntax; remove an existing rule to revoke it. To change a rule, remove the old entry and add its replacement. Deny takes precedence over Ask, which takes precedence over Allow. Session and global rules can still affect the result even when the project list is empty.

The interface waits for a project-specific server confirmation before clearing a submitted rule. Failed or unconfirmed saves preserve the input and show a status message. Successful changes update other open views of that project. The next operation in an authenticated interactive owner session reads the current rules, including changes made during a running turn. Already authorized operations retain their authorization; existing approval waits still require their own answer. Already admitted sub-agents retain their captured policy.

Allow once and Deny answer only the current request; they do not create project rules. Removing a saved project rule affects future authorization, not the historical result of an operation that already ran.

## Understand a refusal

For Windows path rules, forward slashes avoid escaping ambiguity: for example,
`read(C:/Users/me/project/**)`. Ordinary backslashes in drive paths are preserved;
the rule syntax still uses `\\`, `\(`, and `\)` to escape its own special characters.
JSON configuration additionally requires JSON's normal backslash escaping.

A tool result identifies whether the refusal came from authority, a permission rule, Plan mode, Auto classification, or Sandbox. `AUTHORITY_TIER_MISSING` means the execution request lacks an authenticated authority tier. It is not resolved by Bypass. New authenticated chat submissions carry that identity; old admitted executions missing identity must be resubmitted through an authenticated interface.

Sandbox is configured separately in the composer Plus menu. Changing Sandbox affects subsequent turns. Bypass keeps the current sandbox restrictions, and an ordinary tool approval never authorizes a sandbox escalation. When a sandbox denial cannot open a separate escalation wait, change the relevant settings explicitly and submit a new call.

See [tools](tools.md), [Web](../interfaces/web.md), [terminal](../interfaces/tui.md), and the [engineering contract](../reference/design/runtime/permission-model.md).
