# Execution control

This package owns OpenProgram's canonical execution-control data model and
durable store. It defines stable identities, lifecycle transitions, idempotent
control commands, and the append-only event history from which materialized
execution records can be rebuilt.

The same store owns immutable revisions, fenced physical attempts,
content-addressed checkpoint manifests, and the external-effect ledger. A live
attempt must hold the current unexpired lease before it can publish a
checkpoint or register and dispatch an effect. Unresolved dispatched effects
block checkpoint publication until reconciliation records a terminal receipt.

Execution engines do not belong here. Chat, Agent, Workflow, Job, and tool
drivers retain their own activation code and implement the driver contract
defined by the unified execution-control design. They report safe points and
outcomes to this control plane instead of writing lifecycle state directly.

The normative design is
[`docs/reference/design/runtime/execution/execution-control.html`](../../docs/reference/design/runtime/execution/execution-control.html).
