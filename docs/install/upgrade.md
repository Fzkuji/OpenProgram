# Upgrading

Upgrade behavior depends on the installation type. A stable installation always moves between published versions; it never follows `origin/main`.

Version 0.7.0 is the one-time transition from v0.6.6 to the updater-enabled release line: Desktop users install the v0.7.0 DMG manually; CLI/server users rerun the complete release installer once:

```bash
curl -fsSL https://openprogram.io/install | sh
```

After installing v0.7.0, the Desktop settings and `openprogram upgrade` commands below handle later stable releases.

## Desktop release

The Desktop checks the latest stable GitHub Release automatically. You can also use **Settings → General → Application → Check now**.

- macOS: when a release is available, choose **Download and open DMG**. OpenProgram selects the architecture-matched complete `unsigned` DMG, downloads it to the location you choose, verifies its byte count and SHA-256, and opens it. Quit OpenProgram and replace `OpenProgram.app`; macOS may require **Privacy & Security → Open Anyway** again.
- Linux: rerun the release installer from the target immutable tag. Linux currently has no published desktop package.

The application shell and complete product runtime are replaced together. State under `~/.openprogram` remains unchanged.

## CLI and server release

Check or upgrade to the latest stable release:

```bash
openprogram upgrade --check
openprogram upgrade
```

To select a specific immutable release instead:

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=X.Y.Z sh
```

The command downloads the versioned installer from the immutable release tag. The installer downloads the platform runtime archive used by Desktop, verifies its checksum and complete capability manifest in a new version directory, cold-starts the worker, then changes the `current` symlink. A failure before the change leaves the previous version selected. A running worker is not restarted automatically.

Restart a login service after upgrading:

```bash
openprogram worker restart
```

## Recovering a conversational self-update

The source-checkout `self_update_prepare` chat tool accepts an optional
`verification_plan` argument. The plan is included in the mandatory owner
approval and stored with the immutable request. For example:

```json
{"schema":1,"checks":[{"id":"diagnostics","assertion_id":"acceptance-1","entry":"/api/diagnostics","timeout_seconds":10,"max_output_bytes":65536}]}
```

Provide exactly one check for each assertion (`acceptance-1`, `acceptance-2`,
and so on), from 1 to 32 checks. All fields shown are required; `schema` must
be the integer `1`. Check IDs must be unique alphanumeric/underscore/hyphen
identifiers starting with an alphanumeric character, at most 64 characters.
Each check requires an integer timeout of 1–60 seconds and an integer output limit of
1–262144 bytes. Supported entries are `/api/commands`, `/api/diagnostics`,
`/api/doctor`, `/healthz` and `/chat`; arbitrary URLs, query strings and extra
fields are rejected before creating the update.

The restarted verifier receives the same plan and calls
`self_update_observe(check_id="diagnostics")`, without supplying `entry` or
execution arguments. The execution layer applies the approved limits and the
original overall deadline. Signed evidence must match that check and assertion;
reusing evidence for another assertion does not pass. Omitting the plan preserves
the previous HTTP-only verifier behavior and grants no new permissions.
UI, CLI and candidate-test check kinds are not accepted by this plan yet. HTTP
responses, including `/chat` HTML, do not prove rendered App behavior; complete
UI/CLI/test verification and installed-App acceptance remain pending.

This source-checkout capability is separate from stable-release upgrades. On macOS,
if a conversational self-update leaves the default App in maintenance, inspect it
from your local terminal without starting an Agent:

```bash
openprogram self-update status --json
openprogram self-update status UPDATE_ID --json
openprogram self-update repair UPDATE_ID
```

Replace `UPDATE_ID` with the ID reported by `status`. Repair requires an interactive
terminal and the exact confirmation displayed with the action, revision and plan
digest. There is no `--yes` or force-clear option. An existing approved attempt can
resume only within its original ten-minute window; a failed or expired attempt
requires fresh confirmation.

Repair uses the controller saved before the update. It restores the previous App
when rollback remains possible, or completes an already-started irreversible
commit only with the original accepted verification evidence. An aborted,
unactivated transaction retains the old App. Missing or changed evidence leaves
maintenance enabled. Repair restarts the default worker, checks the App identity
and live service, then clears maintenance. It creates no new verifier Job and does
not change the original update verdict. The separate repair result records which
version was recovered; service recovery is not proof that a failed feature meets
its original goal.

New conversational update requests also freeze a read-only diagnostic stage. After
a verified rollback, the restored worker creates one **Post-rollback diagnosis**
Job in the original session. It uses the approved model and profile, reads failure
evidence, and reports a cause and proposed corrections. It cannot edit source,
run shell commands, install software, or authorize another update. The original
verification and rollback verdicts are retained.

The Job has at most five minutes after rollback, shortened by any earlier approved
iteration deadline. Restart does not reset that limit or repeat a terminal Job.
Use the ordinary Job cancel action to stop diagnosis; `self_update_cancel` still
only cancels a pre-activation update. A new update supersedes pending diagnosis.
Unavailable models or invalid evidence stop diagnosis without restricting the
restored service. `self-update status --json` includes `diagnosis_result` when one
exists. Older requests without frozen diagnostic configuration keep their prior
behavior. A diagnostic report does not itself modify or install code.

New requests separately freeze a **Post-rollback source repair** stage. Initial
approval includes isolated repair and the listed `required_tests`. Default mode
requires a separate approval before another installation; explicitly approved
`bounded_auto` also permits subsequent installations within its original limits.
An implementation/test diagnosis can trigger one read-only model
Job that proposes text edits. The controller validates each edit, creates a new
linked worktree and commit, and runs the frozen tests in a native sandbox without
network access or App write permission. Your original worktree is unchanged.
Default scope is the original changed files; `bounded_auto` uses its approved
path patterns. Protected runtime, approval, installer, dependency and Git files
are not automatically modified. Invalid paths or non-unique old text stop repair.

Repair and tests share at most ten minutes after rollback, shortened by an
earlier approved deadline. A new update, cancellation or expiry stops this stage
and its test processes. Use the ordinary Job cancel action while the model runs;
after the model finishes, call `self_update_repair_cancel(update_id)` in the
original conversation to cancel tests. Restart does not replay partial edits,
commits or tests. Failed worktrees and evidence remain available for inspection.
Old requests without frozen repair configuration do not gain this capability.

`self_update_status` and `self-update status --json` expose `source_repair_result`.
The chat tool uses the same read-only projection as the owner-authenticated Web
history (`GET /api/self-updates?session_id=…`) and detail
(`GET /api/self-updates/{update_id}?session_id=…`) endpoints. History includes
completed attempts and supports bounded `limit` and `cursor` pagination.
`candidate_revision` and `target_app` describe the requested installation;
`last_verified_runtime` contains the last matching verification's SHA, PID,
timestamp and source, or is null when unknown. It does not prove the worker is
still online. `state_revision` is a state counter, not a Git revision.
Verifier evidence can be read with
`GET /api/self-updates/{update_id}/evidence?session_id=…&evidence_id=…`, using
the verifier's `evidence_id` or an assertion's `evidence_refs` value from that
projection. Owner authentication and the original session are required. The
response contains only observations cited by the validated signed result, not
arbitrary files, credentials or configuration. Stored HTTP/HTML response text is
not proof of a rendered App window. Changed or invalid evidence returns an error.

Queries do not initialize or repair update state. Invalid state returns an
error rather than an empty history. Running reports `self_update_error` if its
update snapshot is unavailable. The projection excludes credentials, raw logs
and configuration; repair summaries include status and the new candidate SHA
when present. It does not replace the separate CLI recovery inspection output.

The conversation shows persisted self-update history grouped by update sequence
and attempt; **Load older updates** reads another page. Running uses the same
status card. Target revision and last verified runtime are separate: an unknown
runtime stays **Unknown**, and a prior verification is not a live connection check.
Expand **Update details and evidence** to inspect assertions and load authenticated
evidence as plain text. When status cannot be read, the view retains its last
snapshot with an explicit stale warning and last-sync time, then retries.

Under **Request an update action**, cancellation, stopping iteration and retry
buttons only append a request to the original conversation's unsent draft; they
preserve existing draft text. Retry requires a complete new candidate commit SHA.
Send the draft in that conversation to request the tool operation. These buttons
do not install, cancel or approve anything themselves, and normal tool authority
and mandatory approvals still apply. The card changes only after the controller
reports the new state.

`candidate_ready` means the new commit and all configured tests passed validation;
`awaiting_tests` means no required tests were configured. Missing/failed tests or
source drift produce `failed`; cancellation and expiry produce `cancelled` and
`expired`. None means installed.

For a tested repaired candidate, call `self_update_retry(update_id, candidate_sha)`
in the original owner conversation. It always asks for one-shot approval, even
in bypass mode. The approval displays the exact SHA, changed paths, tests and
remaining budget. Git contents and test logs are checked again after approval;
changing either invalidates submission. An `awaiting_tests` candidate cannot be
approved through this entry: start a new owner request with explicit tests.

New `bounded_auto` requests must include a future total `deadline`, allowed path
patterns and non-empty `required_tests`. Only requests with the separately frozen
iteration authorization can automatically submit another candidate. An old
request's mode field alone never grants that permission. Each child preserves
the original goal, assertions, source baseline, model/profile and policy. The
first update counts as attempt 1; the maximum of three includes it. Reserving a
child consumes one attempt, including failed submissions. Restart reuses the
same child and deadline rather than resetting the budget.

`self_update_iteration_cancel(update_id)` stops the whole sequence, including
pending approval, diagnosis, source repair and candidate tests. A child already
being activated or verified must finish its transaction or safe rollback; no
subsequent attempt is allowed. This does not cancel unrelated jobs.

The chat `self_update_status` result includes an `iteration` section with root,
parent, attempt limits, deadline and submission status. `submitted` means the
request was handed to the external supervisor, not that installation succeeded.
The child still goes through packaging, system checks, a new verification Job
and commit or rollback. Full installed-App acceptance remains a separate release
requirement; fixture tests do not establish that the current App was updated.

The worker also persists finite update results in the original session, even if
the window is closed or the update stops before a verification Job is created.
Repeated worker reconciliation reuses the same update, attempt and result-type
identity; it does not start another model turn or move the conversation's HEAD.
An interrupted write is retried. If the original session or initiating assistant
node no longer exists, delivery remains pending without recreating the session
or sending the result elsewhere. Persisting a result does not reopen the App or
prove that installation succeeded; the update phase and verified runtime evidence
remain the source of that conclusion.

Desktop now has a receiver for a trusted, update-specific conversation recovery
request. It resolves only the original session through owner authentication and
acknowledges it after the transcript loads in the main window. An expired request,
a deleted session or an authentication failure leaves normal startup available
and shows a recovery reason. Changing pages stops automatic relocation; the
dismissible notice retains an original-session link when that identity is valid.
Neither recovery nor its loading confirmation starts another verification Job or
proves the update succeeded. The controller now persists the recovery intent before
activation and binds the opaque update ID to the installer transaction. If the App
was open, both activation and rollback reopen it with that ID; an originally closed
App stays closed. Ordinary App launches do not consume these recovery requests.

Both the candidate and installed App must contain matching packaged recovery
protocol declarations. Missing, incompatible or changed declarations, a mismatched
transaction ID, or invalid frozen owner configuration stop activation before the
old App is replaced. The controller rechecks these inputs after waiting and after
resuming a prepared update. Packaging and the local refresh script generate the
declaration from the actual Desktop archive, installer, runtime manifest, backend
and compiled Web files. An older App without it needs an explicit complete update
before conversational recovery is available. The source integration is covered by
fixture tests; real installed-App restart and session/tab restoration acceptance
remain pending, so this is not yet a release-ready end-to-end feature.

If the App or normal CLI cannot start, use the entry saved for that update:

```bash
"$HOME/.openprogram/self-updates/UPDATE_ID/recover.sh" status
"$HOME/.openprogram/self-updates/UPDATE_ID/recover.sh" repair
```

The script uses the original saved runtime outside the App. `status` is also the
default when no argument is given. `repair` still requires interactive owner
confirmation; it does not bypass failed evidence or expired authorization.
`recover.sh resume` invokes the original supervisor within its existing authority
and deadlines, without approving a new update or recreating a verifier Job.

Before activation, OpenProgram also publishes the update's user-owned
`ai.openprogram.self-update.recovery.UPDATE_ID.plist` under `~/Library/LaunchAgents/`.
It runs once per subsequent user login, independently of the App. There is no
resident process or periodic retry, and writing the file does not start another
controller immediately. Recovery does not run before login or disk unlock. If
both the App and controller stop in the current login session, use the saved script
explicitly. Completed updates remove only their unchanged login file; the saved
runtime, script and evidence remain. Missing or damaged trusted recovery files
require manual intervention rather than reconstruction from an unverified App.

## Development checkout

In a source checkout, the same command uses the development pipeline instead of the release installer. It validates a Git target, updates dependencies and built assets when their source files changed, probes the new checkout, and restarts the worker only after the probe succeeds:

```bash
openprogram upgrade --check
openprogram upgrade --dry-run
openprogram upgrade
```

The historical `openprogram update` command is a compatibility alias for `openprogram upgrade`.

See [Server upgrading](../server/upgrading.md) for source-checkout recovery details.
The maintained architecture, trust boundaries, UI states, and implementation
evidence are in [Automatic updates](../reference/design/distribution/automatic-updates.html).
