# DAG ablation bed (E2 / E3)

Local experiment bed for the DAG paper's two ablation studies. Nothing here
touches the product tree; every trial runs in a temp workdir under a throwaway
`--profile` state dir.

- **E2 — context economization**: tool aging (turn folding) and over-cap node
  spill-to-file, on/off.
- **E3 — expose tiers**: `io` / `llm` / `full` / `hidden`.

## Layout

```
tasks/<name>/task.md     instructions handed to the agent verbatim
tasks/<name>/setup/      initial files, copied into each trial workdir
tasks/<name>/grade.py    run in the workdir, prints a float 0..1
tasks/<name>/reference/  a correct solution, for validating the grader
tasks/_build_tasks.py    regenerates every task dir (hand-written log
                         references under reference/ are preserved)
run.py                   one trial = copy -> agent -> grade -> usage -> JSONL
summarize.py             JSONL -> markdown tables
results/                 output (gitignored)
```

## Usage

```bash
# validate every grader against its reference solution — no LLM, no cost
python run.py --task all --reference

# end-to-end plumbing check with `echo` standing in for the agent
python run.py --task all --variant no-aging --dry-run

# a real, billed run (guarded)
python run.py --task all --variant full --model openrouter:anthropic/claude-opus-4.7 \
              --trials 3 --i-know-this-costs-money

python summarize.py --by variant
python summarize.py --by variant,task
```

`--model` is `provider:model`. It is applied by seeding the trial profile's
`config.json` with `default_provider` / `default_model` plus the `api_keys` and
`providers` blocks copied from `~/.openprogram/config.json` — OpenProgram has no
model env var.

Token attribution is by wall-clock window over the trial profile's own
`usage.db`, because `--print` does not report its session id on stdout. Each
trial has a private profile, so the window contains only that trial's rows.

## Tasks

| task | one-liner | graded by |
|---|---|---|
| `prime_utils` | implement `is_prime` / `primes_up_to` / `prime_factors` from scratch | pytest |
| `csv_stats_bug` | fix three bugs in a CSV summariser (int-vs-float, empty mean, even median) | pytest |
| `split_module` | split one module into a `shop/` package of four files, delete the original | pytest (layout + behaviour) |
| `json_config_merge` | recursive config merge with list-replacement and `None`-deletes | pytest |
| `retry_decorator` | `@retry` factory with exponential backoff, hook, selective exceptions | pytest |
| `sql_migration_bug` | fix upsert, missing commit and a LIMIT off-by-one in a SQLite store | pytest |
| `text_wrap_bug` | fix off-by-one and dropped-last-line in greedy word wrap | pytest |
| `event_bus` | pub/sub with error isolation, idempotent unsubscribe, `*` wildcard | pytest |
| `cli_argparse` | build a `tally` CLI with subcommands and exit codes | pytest (subprocess) |
| `graph_paths` | Kahn topo-sort, BFS shortest path, undirected components | pytest |
| `api_client_refactor` | inject a transport into a urllib-hardcoded client, add 5xx retry | pytest |
| `log_mine_errors` † | mine a 6000-line log for error counts and buried sentinels | assert `report.json` against a recomputed ground truth |
| `log_slowest` † | latency percentiles, slowest request, per-service outliers | assert `slow.json` |
| `log_grep_refactor` † | build a log parser library, then use it to produce a summary | 0.5 library API + 0.5 `summary.json` |
| `log_dedupe_report` † | cross-reference the log against a known-issues file | assert `triage.json` |

† The four log tasks generate a deterministic ~6000-line / ~500 KB
`server.log` at setup time and require repeated search over it. That is far
above `_NODE_RENDER_CAP` (32 000 chars), so they are the tasks designed to
trip tool aging and node spill.

## Two readings of the log tasks (`--no-script`)

The log tasks have two honest solutions, and which one the agent picks decides
whether the DAG machinery is exercised at all:

- **In-context** — read the log through the harness. Every line passes through
  the context window, so tool aging and node spill are on the critical path.
  This is the behaviour the ablation is meant to measure.
- **Scripted** — write a throwaway Python/awk one-liner and read back only its
  printed answer. Context stays flat, and the harness is bypassed. A capable
  model prefers this, which makes the ablation switches look inert.

Rather than declare one of them correct, the bed measures both:

```bash
# free choice — the model does whatever it would normally do
python run.py --task all --variant full --model <m> --i-know-this-costs-money

# scripting banned on the four log_* tasks — forces the in-context path
python run.py --task all --variant full --no-script --model <m> \
              --i-know-this-costs-money
```

`--no-script` appends a fixed instruction (`NO_SCRIPT_SUFFIX` in `run.py`)
banning any script, program, or pipeline that computes, aggregates or
summarizes the answer. Reading files to look at the data is still allowed, and
deliverable files the task asks for are still expected — only outsourcing the
*analysis* is forbidden.

It applies to the four `log_*` tasks only. Under `--task all` the other tasks
run unmodified in the same sweep, so the rows record what each trial actually
got rather than what the flag asked for:

| field | meaning |
|---|---|
| `variant` | `full+no-script` when the row carried the constraint, else `full` |
| `base_variant` | the `--variant` value, always unsuffixed |
| `no_script` | bool — whether this row got the suffix |

So `summarize.py --by variant` separates the two readings on its own, and
`--by base_variant` collapses them back together. Output files are also
tagged (`<stamp>_full_no-script_<tasks>.jsonl`), so the paired runs never
overwrite each other.

Report both. A gap between them is the interesting result: it quantifies how
much of the measured effect depends on the agent choosing to keep the data in
context.

## Missing product switches (input for §8)

`--variant` values other than `full` are **not faithful today**. The runner
sets the env vars below, stamps `unsupported_env` into every result row, and
`summarize.py` prints a caveat — but the product ignores them. Implementing
these is a prerequisite for E2/E3.

| env var | what it must control | where |
|---|---|---|
| `OPENPROGRAM_TOOL_AGING` | `on\|off` master switch for cross-turn tool aging. Must be honoured by **both** consumers: `tool_aging.prepare_history` (`context/engine.py:259`) and the DAG pre-pass `render._aged_code_ids` (`context/render.py:35`). Off ⇒ nothing ever collapses to an `[aged]` stub. | `context/tool_aging/policy.py` |
| `OPENPROGRAM_TOOL_AGING_TAIL_TURNS` | int override for `policy.TAIL_TURNS` (default 3), so aging aggressiveness can be swept rather than only toggled. | `context/tool_aging/policy.py` |
| `OPENPROGRAM_TOOL_AGING_MAX_RESULT_CHARS` | int override for `policy.MAX_TOOL_RESULT_CHARS` (default 4000), the within-turn cap on one `tool_result`. | `context/tool_aging/policy.py` |
| `OPENPROGRAM_NODE_SPILL` | `on\|off` for the spill-to-`.txt` branch in `render._cap_node_text`. Off ⇒ fall through to plain char-truncation with no readable artifact — the E2 "overflow is unrecoverable" condition. Today the branch is skipped only when `large_dir` is `None`, and `large_dir` is derived from the session history dir (`render._large_dir`), so it cannot be disabled externally. | `context/render.py:220-282` |
| `OPENPROGRAM_EXPOSE_DEFAULT` | `io\|llm\|full\|hidden` default `expose` tier for functions that do not set one. `agentic_function.__init__` hard-defaults to `"io"` (`agentic_programming/function.py:589`) with no override, so the whole E3 sweep is undrivable without it. | `agentic_programming/function.py` |

Already present and usable: `OPENPROGRAM_NODE_RENDER_CAP` (`render.py:213`)
controls the per-node char cap that triggers spill in the first place, so a
cap sweep is available today even though the on/off switch is not.
