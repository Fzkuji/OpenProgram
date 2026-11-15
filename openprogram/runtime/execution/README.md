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
The process-local driver registry stores only exact attempt/generation bindings;
it is not a lifecycle authority and cannot replace durable execution state.

This package imports no engine. The persisted schemas it decodes live here:
the Agent checkpoint payload (`agent_checkpoint.py`), a Job's admission input
(`job_input.py`). So does execution binding and cancellation inside
one process (`run_control/`). Where the control plane must start or ask an
engine, it calls a hook from `hooks.py` that the engine registers when its
package is imported: `openprogram.runtime.conversation` registers
`agent_activator`, `recovery_turn_starter`, `permission_wait_reconciler`,
`live_owner_signals`, `terminal_settler` and `session_goals`;
`openprogram.runtime.jobs` registers `job_records`, `job_runner` and
`turn_request_fields`. A missing required hook raises `EngineNotRegistered`;
`job_runner`, `live_owner_signals` and `terminal_settler` are optional,
because without their package there is no runner, subprocess or chat turn to
reach.

The control plane does not import Programs either. A chat Goal is settled
after each terminal execution (`terminal_settler`), and restart reconciliation
takes the session's Goal owner lock and pauses an interrupted Workflow Goal
(`session_goals`). The Goal tool loads on first use, which can be after startup
recovery, so the conversation engine registers these hooks and imports
`openprogram.programs.tools.planning.goal` when one is first called.

Admission stores an immutable input snapshot with the queued execution. Run
state is folded from each session's log into tables in the Worker's memory, and
every committed change is appended to that log.

`RuntimeControlService` commits pause and cancel intent before notifying a live
driver. Missing or failing local delivery leaves the durable command applying
for recovery. A pause becomes applied only after the current attempt publishes
a declared safe-point checkpoint and relinquishes ownership; cancel atomically
supersedes lower-priority applying commands.

Attempt finalization checks the effect ledger. A dispatched or uncertain effect
moves the execution to `reconciliation_required` instead of claiming a terminal
outcome. Once every effect has a terminal receipt, an applying cancel resumes
its canonical finalization; otherwise the execution becomes paused for an
explicit continuation decision.

The normative design is
[`docs/reference/design/runtime/execution/control.html`](../../docs/reference/design/runtime/execution/control.html).

`restart.py` records bounded worker restart intents in canonical events and
continues eligible checkpoints through the existing control/resource commands.
