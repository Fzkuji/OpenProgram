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

Conversational packaging is offline. Its dependency base must have exactly the
candidate's `uv.lock` and `scripts/release/product-runtime.json`; the controller
uses the saved runtime's pinned build tools and private copies of existing
npm/uv/Electron caches. It still builds the candidate's Web, docs, wheel and
Desktop archive. Missing caches or a different dependency base stop the update
before activation; they do not authorize downloads or reuse of mismatched
dependencies. This build path and real installed acceptance remain release gates.

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
1–262144 bytes (1–1572864 for `ui:main`). Supported entries are `/api/commands`, `/api/diagnostics`,
`/api/doctor`, `/healthz`, `/chat`, `cli:version`, `cli:help`, `test:python` and `ui:main`.
Arbitrary URLs, query strings and unsupported fields are rejected before creating
the update; only `test:python` additionally requires `argv` as described below.

The restarted verifier receives the same plan and calls
`self_update_observe(check_id="diagnostics")`, without supplying `entry` or
execution arguments. The execution layer applies the approved limits and the
original overall deadline. Signed evidence must match that check and assertion;
reusing evidence for another assertion does not pass. Omitting the plan preserves
the previous HTTP-only verifier behavior and grants no new permissions.

For a planned update, verification, post-rollback diagnosis and source-repair
Jobs receive the same approved plan and iteration policy, together with the
attempt's timeout. A repaired child candidate keeps the original goal, assertions,
check IDs, limits, model and authority; it cannot refresh the overall iteration
deadline. Diagnosis and source repair still have only their read tools: including
the verification plan in their prompts does not authorize them to execute checks.

The fixed CLI entries run the installed App's Python with `-I -B -m openprogram`
and respectively `--version` or `--help`, never a model-supplied command or PATH
lookup. Preparation rejects a CLI plan without the native sandbox or a compatible
installed runtime manifest and matching package/App build-revision markers.
Execution uses private temporary directories, blocks network access and App/source
writes, and denies owner-home reads outside the required runtime and scratch paths.
Native verification is single-process: the sandbox denies process creation,
including ordinary or detached subprocesses, `fork` and `posix_spawn`. A check
requiring a child process cannot pass through this adapter. This restriction
applies to these CLI checks and the candidate checks below, not the separate
source-repair required-test or build execution paths.
Evidence binds the runtime identity, exact invocation and exit status; nonzero exit,
timeout, cancellation, changed identity, excess output or failed cleanup cannot pass.
These entries check CLI startup/help only, not arbitrary feature behavior.

For a candidate source test, add a check such as:

```json
{"id":"source-test","assertion_id":"acceptance-1","entry":"test:python","argv":["tests/verify_feature.py","expected"],"timeout_seconds":30,"max_output_bytes":65536}
```

Here `argv` contains 1–32 strings of at most 4096 characters each, with no NUL.
The first item is a committed regular `.py` file relative to the candidate root,
not a symlink, absolute path, parent traversal or interpreter option. Script paths
start with an ASCII letter, digit or underscore and otherwise use letters, digits,
underscore, slash, dot or hyphen, with at most 511 characters. The remaining items
are literal script arguments, approved before the update; the verifier cannot change
them. The installed candidate Python runs `-I -B SCRIPT ARGS` from the registered
candidate worktree root. Isolated mode does not automatically add that root to
Python's import path; a test importing candidate code must do so explicitly.
The registered candidate path is an additional allowed read location, including
when it is under the owner's HOME; other owner-home data stays inaccessible.
The candidate remains read-only. Temporary test data belongs in the private `TMPDIR`
or `HOME`, and no dependency installation is performed. Native CLI prerequisites
and limits also apply. Missing/dirty/unregistered source or a changed script blocks
verification; evidence records the source revision, script digest and invocation.
A `candidate_test` result proves source-test execution, not installed-App behavior.

For a read-only capture of the original session's main App window, use:

```json
{"id":"main-capture","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576}
```

Preparation requires a compatible packaged UI-verification descriptor, runtime
identity and exactly one connected main Desktop window. The candidate and rollback
packages must both contain matching capture/backend/frontend capability bindings
before installation. An older package without this capability cannot use this plan.
The verifier receives a PNG image and accessibility tree, not merely a file path;
the approved output limit covers the entire capture JSON, including base64 image
data. PNGs must be non-interlaced 8-bit RGB/RGBA, with each dimension at most 16384
and at most 32 million pixels. The resolved verifier model must declare image
input support. Preparation rejects a text-only model; restart recovery checks
the capability again before creating the verifier Job. If image support is no
longer available, startup records an error instead of running a text-only verifier.
An already queued Job repeats this check at execution, before calling the model.

Capture is bound to the active verification Job, candidate revision, worker,
original session route and exact main-window connection. Expired, cancelled,
replayed or identity-mismatched requests cannot produce passing evidence.
User input, navigation, window changes, conflicting capture resources, excess
output or incomplete cleanup also prevent successful capture. No arbitrary URL,
JavaScript, target window, click, navigation or data mutation is authorized.
During capture, the current Desktop adapter rejects new page network requests,
native IPC operations and external-link navigation from that main window. The
restriction is released before evidence upload, including on failure, and does
not apply to other windows. It does not revoke already running requests. The
native adapter marks the main renderer's HTTP requests; the backend rejects
marked requests, including authentication bootstrap and stale markers. During
the check, that window's existing WebSocket accepts only observation replies,
cancellation of the exact verifier Job, and the exact approved test-object
operations described below, not ordinary application commands.

To capture the conversation after an approved scroll, add `interaction`:

```json
{"id":"scroll-history","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576,"interaction":{"kind":"scroll","delta_y":-400}}
```

`delta_y` is a nonzero integer from -1200 to 1200 CSS pixels; the target is always
the original main conversation. No selector or script is accepted. This requires
UI protocol 2 in both candidate and rollback packages, binding the actual native
scroll adapter, backend guards and compiled UI persistence guard. Protocol 1
packages remain capture-only and are rejected for scroll plans before installation.
The screenshot and accessibility tree show the after-scroll state; signed evidence
also records before, after and restored scroll metrics. The position is restored
before success without persisting the temporary position. Failed capture attempts
restore their own scroll while the original target and deadline remain valid.
User interruption cancels the check without restoring over the user's new position.
Target changes or failed restoration cannot pass. At a scroll boundary the position
may remain unchanged; this alone does not prove an assertion requiring movement.

For the original conversation's context graph, use a frozen perspective check:

```json
{"id":"context-view","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576,"interaction":{"kind":"view","target":"dag"}}
```

`target` is exactly `session` or `dag`, not a URL or another session. The check
starts with the original conversation visible and invokes its actual perspective
control. It captures the requested perspective and restores the conversation and
scroll position before success. An already selected DAG, a missing/replaced
control, user interruption or failed restoration cannot pass. A `session` target
does not change the perspective and cannot prove that a switch occurred. No
temporary perspective is persisted, including through background tab updates.
On user interruption the adapter does not force the original perspective or
scroll position over the user's selection. Failed restoration may leave the
requested perspective visible; the check reports failure, not successful cleanup.
Both candidate and rollback packages require UI protocol 3, which additionally
binds the compiled perspective support. Protocols 1 and 2 keep their previous
capture and scroll capabilities; they cannot accept perspective checks.

To verify the real rename dialog without changing any actual session title,
approve a temporary test object and both its change and cleanup:

```json
{"id":"rename-control","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576,"interaction":{"kind":"test_object","object_id":"rename-fixture","action":"rename","initial_title":"Before verification","title":"Approved rename","cleanup":"restore-and-remove"}}
```

`object_id` is a 1–64 character identifier using letters, digits, `_` and `-`,
starting with a letter or digit. It identifies only this check's isolated object,
not an existing session or path. The initial and new titles must be different,
nonempty, trimmed strings of at most 120 characters without control characters.
The object is scoped to the update, attempt, check and one-use operation nonce.
It exists only in the active backend check's memory; it is never saved as user data.

The adapter opens an instance of the actual rename dialog in the original main
conversation, enters exactly the approved text and presses Save. The backend
accepts only that exact object/value/operation from the bound socket and live Job.
After acknowledgement, the adapter captures the image and accessibility tree,
then presses Cancel to request the approved restoration. A successful result
requires backend state transitions from initial to renamed to restored, the
original title restored, and the owned dialog closed. The temporary object is
removed when the check ends, including cancellation, timeout or worker exit.
Interruption never permits a successful receipt; cleanup closes only this owned
test dialog and does not rename a real conversation or restore over user input.

Both packages require UI protocol 4, binding the native adapter, backend handler,
fixture bridge and actual compiled rename control. Older protocols gain no new
permissions. This check proves the bounded dialog operation against its temporary
backend object; it does not prove persistent renaming of a real session or authorize
other data changes. Missing objects, wrong input, unavailable controls, stale Jobs,
failed backend acknowledgement or incomplete cleanup are inconclusive.

The screenshot supports only assertions about the captured state; interactions
that were not observed remain inconclusive. HTTP responses, including `/chat`
HTML, do not prove rendered App behavior. Other side-effect operations are not
supported. Complete verification and actual installed-App acceptance remain pending.

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
