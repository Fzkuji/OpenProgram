# OpenProgram on Harbor

`agent.py` runs OpenProgram as a [Harbor](https://docs.harborframework.com/agents/custom-agents.md)
installed agent, so any Harbor dataset can score it. `frontier_eval` runs it
against a written target beside a pinned control harness (below); the raw
`harbor` commands further down use FrontierHarness Eval
(`frontier-harness-eval/frontier-harness-eval@v1.1.0`, 62 tasks). Design notes:
[`docs/reference/design/testing/harbor-benchmark-adapter.html`](../../../docs/reference/design/testing/harbor-benchmark-adapter.html).

Per trial, in the task container:

1. **Install** (as root): pinned uv puts Python 3.12 and the `openprogram` CLI
   under `/installed-agent/openprogram`. The task image needs no Python.
   Every dependency is held to the commit's `uv.lock` by a constraints file,
   so every trial runs the same code; the versions installed are in
   `agent/openprogram-packages.txt` of the trial.
2. **Run** (as the task's agent user, in its working directory): the
   instruction on stdin to
   `openprogram --print=- --standalone --no-project-binding --output-format stream-json --model <provider/id> [--effort L] [--no-web] --usage-file /logs/agent/openprogram-usage.json --transcript-file /logs/agent/openprogram-transcript.jsonl`.
   The key Harbor resolved for `-m` is the provider's standard variable
   (`OPENAI_API_KEY` and so on) of that one process: nothing is stored and
   no key or instruction text is on a command line. Nothing is written
   into the graded folder. Stdout (the events) is in `agent/openprogram.txt`
   of the trial, stderr in `agent/openprogram.stderr.txt`. Core files are
   off, so a process that aborts leaves nothing in the graded directory.
   When Harbor's agent timeout ends the run, OpenProgram gets SIGTERM: it
   cancels the turn and still writes the usage summary and the transcript.
3. **Finish**, however the run ended (success, failure, Harbor's
   timeout): OpenProgram's state in the agent's home is copied to
   `agent/openprogram-state/` of the trial, without `auth/` and `cache/`,
   and then `~/.openprogram/auth` is deleted. A named profile's state
   (`~/.openprogram-<profile>`, under `OPENPROGRAM_PROFILE`) is handled the
   same way into `agent/openprogram-state-<profile>/`; the step fails when
   any credential is left. The copy holds the session
   log with the turn's journal and the usage ledger, so a run killed
   outright, which writes neither the summary nor the transcript, still
   leaves its evidence; the key stays neither in the logs nor in the
   container.
4. **Rebuild**: a run killed before it wrote its transcript gets one from
   that state with `openprogram transcript`, the same code
   `--transcript-file` runs.
5. **Report**: tokens, cost and per-model usage go into the trial's
   `agent_result`; rounds, tool calls and how the run ended go into its
   `metadata.openprogram`. The transcript (every model request with its
   tokens and time, the reasoning and text, each tool call with its full
   arguments and each result's full output, in order) is in
   `agent/openprogram-transcript.jsonl`, and as Harbor's ATIF trajectory in
   `agent/trajectory.json`, which `harbor view` shows. A result whose
   `complete` is false is the store's 4,000-character preview (a result
   the turn's journal in the session log does not hold). The output is the
   result as the model read it: one block of at most 32,000 characters, the
   whole text kept in a file the model can page through with `read`; the
   shell tool keeps the last part of a long output and says how many bytes
   it dropped.

A non-zero exit, a missing usage summary, or a summary that does not say
`completed` with at least one model request fails the trial as
`NonZeroAgentExitCodeError`. The run's output is never read for the
error's type: it holds the model's text and its tools' output, which can
say "rate limit" or "invalid api key" about anything (Harbor's own Codex
and Claude Code adapters likewise hand its matcher only the harness's
error events). The type comes from OpenProgram's usage summary alone. A
run that failed before its turn started (`not_started`) is reported by its
cause: `AgentAuthenticationError`, `ApiRateLimitError`,
`ApiInternalServerError`, `NetworkConnectionError` or `ModelNotFoundError`.
A turn that ended on a stream idle bound, after OpenProgram sent the request
again up to 5 times, is `ApiResponseStalledError`, whatever words the
provider's reader uses (`no openai stream event for 300s`, `no deepseek
chunk event for 300s`, `no Codex WebSocket bytes for 600.0s`).

With `--ak bundle=<tar.gz>` step 1 unpacks a prebuilt install instead
(about 9 seconds instead of 8 to 16 minutes under emulation, no network).
It is used only on an `x86_64` glibc image with `tar` and `gzip`; anywhere
else the trial falls back to the wheel install, so `wheel` and
`constraints` stay required.

## One command: `frontier_eval`

`scripts/benchmarks/frontier_eval.py` runs a **target**: a file in
`scripts/benchmarks/targets/` that is the whole method of one evaluation,
with where each part was published.

| Target | Tasks | Attempts | Retry | Model, effort | Control |
| --- | --- | --- | --- | --- | --- |
| `fh-v1-tb21` (default) | FrontierHarness v1's 21 Terminal-Bench tasks, `terminal-bench@2.0` | 3 | 2 | gpt-5.6-terra, medium | Codex 0.148.0 |
| `fh-v1.1` | FrontierHarness v1.1's 62 tasks, `frontier-harness-eval@v1.1.0` | 4 | 2 | gpt-5.6-sol, medium | Codex 0.148.0 |
| `tb2` | Terminal-Bench 2.0, all 89 tasks, no timeout overrides | 5 | 0 | gpt-5.6-terra, medium | Codex 0.148.0 |

Each also names which rate its published figure is (`selection`),
FrontierHarness's wall-clock limit where it has one, Harbor 0.25.0, the
environments it counts as comparable (Runta) and the prices tokens are
counted at. FrontierHarness's runner wraps one task's whole `harbor run
-r 2` (setup, every attempt Harbor retries, the verifier) in
`timeout 5400`; Harbor itself keeps each task's own agent timeout, 600 to
1,800 seconds for these 21 tasks. The job does the same, so the limit is
not a Harbor setting: the summary counts the kept trials that ran longer
than it, which leaves out the time of attempts Harbor deleted to retry.

```sh
P=".venv/bin/python -m scripts.benchmarks.frontier_eval"
$P check --target fh-v1-tb21 --key-profile dev   # Docker, Harbor, key, disk
$P run --target fh-v1-tb21 --key-profile dev --env runta --budget-usd 300
$P resume --job op-fh-v1-tb21 --rerun-infra      # after a stop or a crash
$P summary --job op-fh-v1-tb21
```

- **Control**: OpenProgram and the target's control harness (Harbor's own
  Codex adapter at the pinned version, given the target's reasoning
  effort) run in one job on the same tasks, model and network, so the
  comparison does not lean on published numbers taken under other
  conditions. `--no-control` leaves it out.
- **Departures**: `--model`, `--attempts`, `--tasks`, `--exclude-tasks`,
  `--n-tasks`, `--agent-timeout-cap-min`, `--agent-env`, `--no-control`,
  an environment the target does not count as comparable, or another
  Harbor version are allowed (a target with `overrides_allowed: false`
  refuses a timeout cap) and each is written into `jobs/<job>.run.json`
  with `methodology_comparable: false`. Both agents get the target's
  reasoning effort, OpenProgram through `openprogram --print --effort`;
  `--effort` sets another for both and is a departure. A build whose
  `--print` takes no `--effort` runs at its Agent's default (`run` says
  so), which is a departure too. Effort `none` sends `effort: "none"` on
  both sides. `--pilot` runs the target's eight pilot tasks (three tiers
  by FrontierHarness v1 pass share) and is recorded as a departure. A
  target's `disable_web_search: true` reaches both agents (`--no-web` for
  OpenProgram) and is no departure.
- **Build**: `--ref` (default `stable`) is built into a wheel with its
  `uv.lock` as constraints, from `git archive`, so an unpublished commit
  works and uncommitted files never enter. For local Docker it also packs
  the install once per commit as `openprogram-bundle-x86_64.tar.gz`
  (about 17 minutes emulated, 209 MB; `--no-bundle` skips it). Everything
  lands in `~/.openprogram-eval/builds/<commit>/`, with sha256s in
  `build.json`.
- **Key**: `--key-profile dev` reads the active API key the profile stores
  for the model's provider and passes it in Harbor's environment only;
  otherwise the shell's variable (`OPENAI_API_KEY` and so on) is used. The
  control reads the same variable.
- **Retries**: the launcher's, not Harbor's. The job runs with
  `max_retries: 0`; once Harbor exits, up to the target's `max_retries`
  rounds (2) move every trial Harbor's default `RetryConfig` would retry
  (an exception it does not exclude, and a verifier whose own setup
  failed) to `jobs/<job>.retried/<trial>/<n>/`, write its ledger line and
  run `harbor job resume`. Every attempt of both agents stays on disk;
  `run.json` counts the rounds in `retry_rounds`.
- **Budget**: `--budget-usd` sends Harbor SIGINT once every attempt so
  far has cost that much, and no retry round starts after that; trials in
  flight finish within five minutes, so leave headroom. Each OpenProgram
  attempt appends its tokens, cost and the kind of failure to
  `jobs/<job>.attempts.jsonl`, and the budget reads that. An attempt set
  aside is written there too, with the reason, under the trial's name and
  start time, so a control trial rerun under the same name keeps both
  costs.
- **Resume**: refuses a wheel, bundle or target that changed since the run
  began, and goes on with the retry rounds left; `--rerun-infra` sets
  aside the trials the summary counts as infrastructure or cancelled (a
  failed verifier setup included) and runs them again.
- **Summary**: `jobs/<job>/openprogram-summary.md` and `.json`. Per agent:
  pass rate over passed plus agent failures; first-attempt pass rate
  (a trial does not count as passed when an attempt before it ended for
  the agent's own reason; a retry for the provider, the network or the
  container does not count against it; both agents in a job the launcher
  retried, OpenProgram only in an older one, where Harbor deleted the
  control's retried attempts); FrontierHarness's first-valid-attempt
  rate per task, and which rate the target publishes; retries and their
  kinds; cost of every attempt as reported, with what the retried ones
  cost; cost at the target's prices; cost per pass, the trials Harbor
  kept at the target's prices over their passes, the same attempts for
  both agents; container hours; trials over the wall-clock limit. Paired
  with the control: the n-th attempt of
  each on a task form a pair, McNemar's exact test on the pairs where only
  one passed, and the smallest gap that many pairs can show (21 tasks x 3
  attempts with 30% of pairs disagreeing: about 19 points).
- **Infrastructure** needs evidence: an infrastructure error type
  (whatever the reward), a provider or network cause in the usage summary,
  a verifier whose own setup failed before any test ran (its `test.sh`
  could not download uv), or a finished turn whose verifier never ran. A
  verifier timeout or missing reward after the tests ran counts against
  the agent: a solution that hangs under the tests ends the same way.

## Setup

```sh
uv tool install "harbor[runta]==0.25.0"     # Harbor and the Runta environment
cd /path/to/OpenProgram                     # every harbor command below runs here
export PYTHONPATH="$PWD"                    # so Harbor can import the adapter
```

Build a wheel of the commit under test, and export its locked dependency
versions as constraints. Uncommitted files are not in either.

```sh
REF=$(git rev-parse dev)
git worktree add --detach /tmp/op-eval-src "$REF"
uv build --wheel /tmp/op-eval-src -o /tmp/op-eval-dist
(cd /tmp/op-eval-src && uv export --frozen --no-hashes --no-emit-project \
  -o /tmp/op-eval-dist/constraints.txt)
git worktree remove /tmp/op-eval-src
WHEEL=$(ls /tmp/op-eval-dist/openprogram-*.whl)
CONSTRAINTS=/tmp/op-eval-dist/constraints.txt
```

`--ak git_ref=<tag or commit>` installs from the public repository instead;
use it only for a commit published on GitHub `main`, with constraints
exported from that commit. Harbor reads `--ak` values as JSON, so quote a
commit id that could read as a number: `--ak 'git_ref="1234e56"'`.

Export the provider key in the shell that runs Harbor. Do not pass it with
`--ae`: that also puts it into the environment of every command the agent runs.

```sh
export DEEPSEEK_API_KEY=...        # name follows the provider in -m
```

| `-m` | Key variable | Extra flags |
| --- | --- | --- |
| `deepseek/<id>` | `DEEPSEEK_API_KEY` | |
| `anthropic/<id>` | `ANTHROPIC_API_KEY` | |
| `openrouter/<vendor>/<id>` | `OPENROUTER_API_KEY` | |
| `<harbor provider>/<id>` where OpenProgram's provider id differs | any | `--ak provider=<openprogram id> --api-key-name <VAR>` |

The model id must be one OpenProgram lists for that provider (its catalog is
`openprogram/models/providers/adapters/<provider>/provider.json`); otherwise
the run fails at once, naming the provider and model.

## Commands

The flags shared by every agent run:

```sh
AGENT=(-d frontier-harness-eval/frontier-harness-eval@v1.1.0
       -a scripts.benchmarks.harbor_agent.agent:OpenProgramAgent
       -m deepseek/deepseek-chat --ak wheel="$WHEEL" --ak constraints="$CONSTRAINTS")
```

| Step | Local Docker | Runta |
| --- | --- | --- |
| Oracle smoke (no model) | `harbor run -d frontier-harness-eval/frontier-harness-eval@v1.1.0 -a oracle -e docker -l 1` | `runta login; export RUNTA_CONFIG=~/.config/runta/config.toml` then the same with `-e runta` |
| Install check (no model) | `harbor run "${AGENT[@]}" -i terminal-bench/schemelike-metacircular-eval -e docker --install-only` | same with `-e runta` |
| One task | `harbor run "${AGENT[@]}" -i terminal-bench/schemelike-metacircular-eval -e docker` | same with `-e runta` |
| Full run, 62 x 4 | `harbor run "${AGENT[@]}" -k 4 -n 4 -e docker --job-name op-fh-v1.1` | `harbor run "${AGENT[@]}" -k 4 -n 8 -e runta --job-name op-fh-v1.1` |

- Task names carry the `terminal-bench/` prefix; `-l 1` takes the first task.
- Results: `jobs/<job>/result.json` (rewards, tokens, cost) and
  `jobs/<job>/<trial>/agent/` (OpenProgram output, usage, transcript and
  trajectory). Browse with
  `harbor view jobs`.
- Add `-r 2 --retry-include ApiRateLimitError --retry-include NetworkConnectionError
  --retry-include ApiInternalServerError` to retry trials stopped by the
  provider or the network.
- On Apple silicon the task images are `x86_64` and run emulated: the wheel
  install alone takes 3 to 16 minutes (pass `--ak bundle=...` or use
  `frontier_eval`). Raise `--agent-setup-timeout-multiplier` if setup times
  out. Local Docker results are for checking the pipeline; only Runta
  matches the published runs.

## What one task looked like

Trials of `terminal-bench/schemelike-metacircular-eval` on local Docker
(Apple silicon, emulated `x86_64`; the task allows 40 minutes, 2 GB and one
CPU). Trial 1 ran a wheel of dev `3219d0632`; trials 2 and 3 added an early
fix for shell commands without `ps`, trial 3 also an early run note; trial 4
ran a wheel of dev `a0875b030`; trials 5 to 7 a wheel of dev `664ab6e5a`
with `--transcript-file`. Trial 5 also recorded every provider request
(a temporary debugging option, since removed).

| Trial | Model | Install | Agent run | Requests | Tool calls | Input (cached) | Output | Cost | Reward |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | gpt-5-mini | 4 m 42 s | 4 m 35 s | 11 | 10 | 222,728 (191,232, 86%) | 23,464 | $0.060 | 0 |
| 2 | gpt-5-mini | 4 m 33 s | 13 m 53 s | 63 | 62 | 2,662,204 (2,551,296, 96%) | 28,930 | $0.149 | 0 |
| 3 | gpt-5-mini | 4 m 35 s | 11 m 56 s | 55 | 54 | 2,453,365 (448,000, 18%) | 33,477 | $0.579 | 0 |
| 4 | gpt-5-mini | 4 m 53 s | 9 m 50 s | 43 | 42 | 1,987,326 (1,869,824, 94%) | 32,473 | $0.141 | 0 |
| 5 | gpt-5-mini | 4 m 31 s | 12 m 21 s, killed | 47 | 47 | 1,865,458 (1,809,408, 97%) | 30,254 | about $0.12 | 0 (0 of 63) |
| 6 | gpt-5.6-sol | about 5 m | 9 m 45 s, stopped | 6 done | 17 | 111,093 (82,298, 74%) | 5,119 | $0.250 | none |
| 7 | gpt-5.6-sol | 6 m 11 s | 5 m 49 s | 15 | 23 | 397,195 (346,410, 87%) | 8,424 | $0.510 | 0 (61 of 63) |

- **The tools did what they were asked.** Trials 5 to 7 were checked call by
  call: replaying every `write` and `edit` gives exactly the text each later
  `read` of the file returned, each `edit` matched its old text once, and
  every shell result fits its command. No tool truncated or corrupted a file.
  The transcripts of trials 6 and 7 hold only the first 4,000 characters of
  a longer tool result, the preview the conversation store keeps (request
  12 of trial 7, for one, is cut at 4,017 characters); a transcript now
  takes each result's full output from the run itself.
- **Trials 1 to 4: the same stop, read from the verifier only.** Every one
  of the 63 programs ran directly but stopped inside `interp.py`'s reader
  when run through `eval.scm`: `Unexpected closing parenthesis` in trial 1,
  `Missing closing parenthesis` in trials 2 to 4. In trial 3 `interp.py`
  also printed `[DEBUG]` lines on every run. These trials kept no copy of
  `eval.scm` and no transcript, so their parentheses were not counted.
- **Trial 5: the model's code never parsed.** gpt-5-mini wrote `eval.scm`
  four times from scratch; each version it sent already had 2 to 4 more
  opening than closing parentheses, so `interp.py` reported
  `Missing closing parenthesis` every time, and the verifier failed all 63
  programs. The run note reached each of the 47 requests exactly once. The
  model did not stop: the process was killed (exit 137) in the middle of a
  shell call, most likely out of memory from the request recording, whose
  file had reached 378 MB (the recorder reads the whole file again before
  each request: measured, its peak memory per request is twice the file's
  size); such a kill writes neither the usage summary nor the transcript,
  and the trial's state copy now keeps its journal.
- **Trial 6: the harness can solve the task.** gpt-5.6-sol wrote one
  balanced `eval.scm` in its fifth request, and the calculator ran directly,
  through it and through it twice. The trial then ended without a reward:
  the Harbor process was stopped from outside, and OpenProgram, on SIGTERM,
  cancelled the turn and wrote both files. The task's own verifier, run
  offline on that `eval.scm`, passes all 63 programs. After writing its
  files the process aborted at shutdown (a thread still starting a model
  request) and left a 637 MB `core` file in the working directory.
- **Trial 7: correct but too slow, and an early stop.** gpt-5.6-sol wrote one
  `eval.scm` that every program runs through correctly, checked all of
  `test/` one level deep, interrupted its own two-level check after about
  90 seconds, and ended after 6 of the 40 minutes saying everything was
  verified. The verifier (`tests/test_outputs.py`) runs `closures.scm`
  two levels deep with `timeout=60` and prints a `takes` line only when a
  run finishes; for both copies of `closures.scm` that second line is
  missing, so the two-level run timed out. Offline in the task image with
  one CPU, the same command takes 65.8 seconds with this evaluator and
  14.3 with trial 6's, both with output matching the direct run. The 25,416 uncached input
  tokens of request 13 are the traceback the interrupted check printed.
- **Images without `ps`.** The task image is Debian slim with Python and no
  procps. Shell commands now read `/proc` on Linux; before that every
  `bash` call failed with `shell launch failed` and trial 1 wrote `eval.scm`
  without running it.
- **The run note and the cache.** Trial 3's early version of the note was
  written into the stored message and so repeated once more every round,
  which dropped the cached share of input from 96% to 18%. The note now
  goes into each request once, with the same bytes every round.
- **Early stops.** gpt-5-mini in trial 4 ended after 10 of 40 minutes asking
  whether to go on; gpt-5.6-sol in trial 7 ended after 6, sure it was done.
  Both rewards of 0 are the model's: nothing in the run failed.

## Five tasks with the launcher

One attempt each of five tasks on local Docker (Apple silicon, emulated
`x86_64`, 4 CPUs, 7.7 GB), gpt-5.6-sol, stable `d0fa55dbc`, concurrency 2,
agent time capped at 12 minutes. The images were already pulled.
`batched-eval-parity` ran a second time with the idle bound raised to 1800 s.

| Trial | Install | Agent run | Requests | Input (cached) | Output | Cost | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fix-git | 10 s | 2 m 27 s | 9 | 113,877 (86%) | 3,100 | $0.164 | reward 1 |
| bun-sourcemap-leak | 8 s | 6 m 34 s | 22 | 352,984 (94%) | 9,403 | $0.407 | reward 0: model |
| sqlite-with-gcov | 9 s | 5 m 47 s | 18 | 308,259 (91%) | 4,820 | $0.321 | reward 0: verifier download failed |
| batched-eval-parity | 9 s | 5 m 37 s | 0 | 0 | 0 | not recorded | stream stalled 300 s |
| freecad-impeller | 8 s | 7 m 56 s | 5 | 47,413 (74%) | 4,968 | $0.163 | stream stalled 300 s |
| batched-eval-parity, bound 1800 s | 10 s | 9 m 16 s | 29 | 1,113,777 (95%) | 19,577 | $1.026 | verifier could not start |

- **Install**: with the old per-trial install the same five images took
  3 m 24 s to 15 m 57 s each (8 to 16 minutes for four of them); the
  bundle takes 8 to 10 seconds.
- **The tools did what they were asked** in every trial read. bun: the
  model passed 25 of 36 tests and ended sure it was done; its policy check
  was stricter than the task's variants. sqlite: the verifier's `test.sh`
  failed to download uv from GitHub (`curl: (92) HTTP/2 PROTOCOL_ERROR`)
  before any test ran, so its 0 says nothing about the agent; the summary
  counts it as infrastructure, and `--rerun-infra` runs it again (`harbor
  job regrade` supports only tasks with a separate verifier). The sample's
  summary: 1 passed, 1 agent failure, 3 infrastructure, pass rate 50%.
- **Stalls (2 of 6 trials)**: a request (the first in one, the sixth in
  the other) sent no stream event for 300 s; OpenProgram ended the turn at its idle bound, without
  sending the request again, and the failed request's tokens and cost are
  missing from the summary. Rerun with the bound at 1800 s, no request
  took longer than 39 s between results, so the bound is right and the
  stream stalled. The adapter now reports this as `ApiResponseStalledError`,
  which the job retries, a passing trial included, so a retry cannot keep
  only the stalls that failed; the retried attempt's cost stays in the
  ledger. OpenProgram now sends a stalled request again itself (up to 5
  attempts, each counted), so a trial fails on a stall only once those are
  spent.
- **Verifier without network**: `batched-eval-parity`'s verifier runs
  with `allow_internet=false`. Local Docker refuses `no-network`, and its
  egress-control sidecar needs `docker buildx` (`unknown flag: --file`
  without it), so this task cannot be scored here.
- **Spend and time**: $2.08 recorded, plus the two stalled requests. The
  five-task job took 55 minutes, 16 of them pulling freecad's verifier
  image.

## A full run

- **Comparable**: only Runta (native `x86_64`, the tasks' CPU and memory,
  the published network policy). Here every image runs emulated, Docker has
  4 CPUs and 7.7 GB while 17 tb4 tasks ask for 2 to 4 CPUs and 4 to 8 GB,
  and one verifier cannot run. Local Docker checks the pipeline only.
- **Cost (inferred)**: the five finished gpt-5.6-sol trials here and trial 7
  above averaged $0.49 and 6 agent minutes, every one ending well inside
  its time. 248 trials at that rate is about $120; the tb4 tasks allow 8
  hours each, so plan $150 to $300 and set `--budget-usd`. Stalls (2 of
  6 here) are retried up to twice and each retried attempt is paid again:
  at that rate add up to a third.
- **Time (inferred)**: about 10 minutes of wall time per trial here
  (setup, agent, verifier); 248 trials at concurrency 4 is 10 hours or more
  locally, longer for the tb4 tasks that need the whole machine. On Runta
  at concurrency 8 to 16, a few hours.
- **To decide or provide**: a Runta account (`runta login`, `--env
  runta`); the budget and the model; an API key with that much headroom;
  whether the maintainers will accept an unranked run with
  `methodology_comparable: false`; for local scoring of
  `batched-eval-parity`, `docker buildx`.

## Not known yet

- **Score on native `x86_64`**: trial 7's evaluator missed the verifier's
  60-second limit only under emulation; whether it passes on Runta or another
  `x86_64` host has not been run.
- **Stalled retries live**: `ApiResponseStalledError` and its retry are
  tested on recorded output; no live trial has stalled since.
- **Bundle on Runta**: the bundle is checked on local Docker only.
- **Abort after a stop**: after a SIGTERM, a turn's thread can still start a
  model request while the process exits, which aborts it (trial 6 left a
  637 MB core file in the working directory; core files are now off). The
  usage summary and the transcript are written before that.
- **Leaderboard submission**: no public process. New runs are recorded with
  `methodology_comparable: false` and are unranked until a control harness
  runs under the same network policy. Ask the maintainers
  (https://github.com/frontier-harness-eval/eval/issues) before a full run.
- **Cost of a timed-out trial**: on Harbor's agent timeout OpenProgram is
  stopped with SIGTERM and writes its summary; no trial has reached the
  timeout yet, so whether every environment (Runta in particular) still runs
  that stop command after it has not been checked.
- **DeepSeek**: no trial with `-m deepseek/<id>` has run yet.
- **Runta egress**: whether each provider's API host is on the Runta allowlist
  the published runs used.
