#!/usr/bin/env python3
"""Runner for the DAG-paper E2/E3 ablations.

One trial =
    copy tasks/<name>/setup/ -> a fresh temp workdir
    run tasks/<name>/setup/_make_fixture.py if present (big log fixtures)
    build the variant's env overrides
    invoke `openprogram --print "<task.md>"` in that workdir, wall-timed
    run tasks/<name>/grade.py in the workdir -> score in [0,1]
    attribute usage.db rows to the trial by time window (+ profile dir)
    append one JSON line to results/<stamp>.jsonl

Nothing here writes to the product tree and nothing restarts a service.
Each trial gets its own --profile (state dir), so sessions, history and
usage rows are isolated per trial.

    python run.py --task all --variant full --model anthropic:claude-... --trials 3
    python run.py --task prime_utils --variant no-aging --dry-run
    python run.py --task all --reference          # score the reference solutions

Cost guard: a real (non --dry-run, non --reference) run bills the model
API. It refuses to start unless --i-know-this-costs-money is passed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(HERE, "tasks")
DEFAULT_OUT = os.path.join(HERE, "results")

# ---------------------------------------------------------------- variants
#
# Each variant is a dict of env vars layered onto the trial's environment.
# NOTE (§8 input): the switches marked TODO below DO NOT EXIST in the
# product yet — `openprogram/context/tool_aging/policy.py` holds bare
# module constants and `render.py` derives the spill dir from the session
# path with no off switch. Until they land, the no-aging / no-spill /
# no-both variants are NOT faithful; see MISSING_SWITCHES and the
# `unsupported` flag stamped into every result row.

MISSING_SWITCHES = {
    "OPENPROGRAM_TOOL_AGING": (
        "on|off master switch for cross-turn tool aging. Read in "
        "openprogram/context/tool_aging/policy.py and honoured by BOTH "
        "consumers: tool_aging.prepare_history (engine.py:259) and the DAG "
        "pre-pass render._aged_code_ids (render.py:35). Off => no turn is "
        "ever collapsed to a [aged] stub."
    ),
    "OPENPROGRAM_TOOL_AGING_TAIL_TURNS": (
        "int override for policy.TAIL_TURNS (default 3). Lets us sweep the "
        "aging aggressiveness instead of only on/off."
    ),
    "OPENPROGRAM_TOOL_AGING_MAX_RESULT_CHARS": (
        "int override for policy.MAX_TOOL_RESULT_CHARS (default 4000) — the "
        "within-turn hard cap on a single tool_result."
    ),
    "OPENPROGRAM_NODE_SPILL": (
        "on|off for the over-cap node spill-to-.txt path in "
        "render._cap_node_text. Off => fall through to the plain "
        "char-truncation branch with no readable artifact, which is the "
        "E2 'no recoverable overflow' condition. Today the branch is only "
        "skipped when large_dir is None, and large_dir is derived from the "
        "session history dir (render._large_dir), so there is no way to "
        "disable it from outside."
    ),
    "OPENPROGRAM_EXPOSE_DEFAULT": (
        "io|llm|full|hidden — default `expose` tier for functions that do "
        "not set one explicitly. agentic_function.__init__ currently hard "
        "defaults to \"io\" (function.py:589) with no external override, so "
        "the E3 sweep cannot be driven without either this env var or "
        "editing every decorator."
    ),
}

VARIANTS: dict[str, dict[str, str]] = {
    # E2 — context economization
    "full":     {},                                             # everything on
    "no-aging": {"OPENPROGRAM_TOOL_AGING": "off"},              # TODO: unsupported
    "no-spill": {"OPENPROGRAM_NODE_SPILL": "off"},              # TODO: unsupported
    "no-both":  {"OPENPROGRAM_TOOL_AGING": "off",
                 "OPENPROGRAM_NODE_SPILL": "off"},              # TODO: unsupported
    # E3 — expose tier sweep
    "expose-io":     {"OPENPROGRAM_EXPOSE_DEFAULT": "io"},      # TODO: unsupported
    "expose-llm":    {"OPENPROGRAM_EXPOSE_DEFAULT": "llm"},     # TODO: unsupported
    "expose-full":   {"OPENPROGRAM_EXPOSE_DEFAULT": "full"},    # TODO: unsupported
    "expose-hidden": {"OPENPROGRAM_EXPOSE_DEFAULT": "hidden"},  # TODO: unsupported
}

# A variant is faithful only if every env var it sets already exists.
SUPPORTED_ENV: set[str] = {"OPENPROGRAM_NODE_RENDER_CAP"}


def unsupported_vars(variant: str) -> list[str]:
    return sorted(k for k in VARIANTS[variant] if k not in SUPPORTED_ENV)


# ------------------------------------------------------------- no-script mode
#
# The four ``log_*`` tasks ask questions ABOUT a large log fixture ("which
# endpoint is slowest", "dedupe these errors"). An agent has two honest ways
# to answer: read the log through the harness (every line lands in context —
# what the DAG machinery is actually being measured on), or write a throwaway
# Python/awk script and read only its printed answer (context stays flat, the
# harness is bypassed).
#
# Both are legitimate agent behaviour, so we measure BOTH rather than picking
# one: the default variant leaves the choice to the model, and --no-script
# forbids the scripting escape hatch so the in-context path is exercised.
# Comparing the two is the point — see README "Two readings of the log tasks".

NO_SCRIPT_TASKS = frozenset({
    "log_dedupe_report", "log_grep_refactor", "log_mine_errors", "log_slowest",
})

# Appended verbatim to the task instruction. Bans computing the answer
# out-of-context without banning the tools outright (the agent still needs
# bash/read to LOOK at the log — it just cannot make a program do the
# aggregation for it).
NO_SCRIPT_SUFFIX = """

## Additional constraint for this run

Do not write, generate, or execute any script, program, or one-liner (Python,
shell, awk, sed, jq, or otherwise) whose purpose is to compute, aggregate,
count, sort, or summarize the answer for you. Do not pipe log contents through
any command that reduces them (grep -c, sort, uniq, wc, head applied to get the
result, etc.).

You must read the relevant log content yourself and work out the answer by
reading it. Use plain file reads to see the data. Deliverable files that the
task asks you to write are of course still expected — the ban is on
outsourcing the ANALYSIS to a program, not on producing the requested output.
"""


NO_SCRIPT_SUFFIX_LABEL = "+no-script"


def apply_no_script(task: str, prompt: str, enabled: bool) -> str:
    """Append the no-script constraint for the log_* tasks when enabled."""
    if enabled and task in NO_SCRIPT_TASKS:
        return prompt.rstrip() + "\n" + NO_SCRIPT_SUFFIX
    return prompt


def variant_label(variant: str, constrained: bool) -> str:
    """Variant name as recorded in results — suffixed when the row actually
    carried the no-script constraint. Keeps the two readings of a log task
    from colliding under one key when results are grouped by variant."""
    return f"{variant}{NO_SCRIPT_SUFFIX_LABEL}" if constrained else variant


# ---------------------------------------------------------------- tasks

def list_tasks() -> list[str]:
    return sorted(
        d for d in os.listdir(TASKS_DIR)
        if os.path.isdir(os.path.join(TASKS_DIR, d))
        and os.path.exists(os.path.join(TASKS_DIR, d, "task.md"))
    )


def prepare_workdir(task: str, dest: str) -> None:
    """Copy setup/ into dest and materialize any generated fixture."""
    src = os.path.join(TASKS_DIR, task, "setup")
    shutil.copytree(src, dest, dirs_exist_ok=True)
    gen = os.path.join(dest, "_make_fixture.py")
    if os.path.exists(gen):
        subprocess.run([sys.executable, "_make_fixture.py"], cwd=dest, check=True,
                       capture_output=True, timeout=120)


def apply_reference(task: str, workdir: str) -> None:
    """Overlay the reference solution, for --reference self-check.

    Conventions inside reference/:
      __DELETE__   newline-separated relative paths to remove
      solve.py     executed in the workdir after the overlay (artifact tasks)
    """
    ref = os.path.join(TASKS_DIR, task, "reference")
    if not os.path.isdir(ref):
        return
    for root, _dirs, files in os.walk(ref):
        for fn in files:
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, ref)
            if rel == "__DELETE__":
                for victim in open(src).read().split():
                    p = os.path.join(workdir, victim)
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                    elif os.path.exists(p):
                        os.remove(p)
                continue
            dst = os.path.join(workdir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    solve = os.path.join(workdir, "solve.py")
    if os.path.exists(solve):
        r = subprocess.run([sys.executable, "solve.py"], cwd=workdir,
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"reference solve.py failed: {r.stderr[-2000:]}")
        os.remove(solve)


def grade(task: str, workdir: str) -> float:
    """Run the task's grader against workdir. Score is the last stdout float."""
    grader = os.path.join(TASKS_DIR, task, "grade.py")
    dst = os.path.join(workdir, "_grade.py")
    shutil.copy2(grader, dst)
    try:
        r = subprocess.run([sys.executable, "_grade.py"], cwd=workdir,
                           capture_output=True, text=True, timeout=600)
        for line in reversed(r.stdout.strip().splitlines()):
            try:
                return max(0.0, min(1.0, float(line.strip())))
            except ValueError:
                continue
        return 0.0
    except subprocess.TimeoutExpired:
        return 0.0
    finally:
        os.path.exists(dst) and os.remove(dst)


# ---------------------------------------------------------------- usage.db

def profile_home(profile: str) -> str:
    """State dir a `--profile NAME` run uses (openprogram.paths.get_state_dir)."""
    return os.path.expanduser(f"~/.openprogram-{profile}")


def seed_profile(profile: str, model: str | None) -> None:
    """Create the trial's state dir with API keys carried over and the
    requested model pinned.

    A fresh profile starts with no config at all, so it would have no
    credentials and no default model. We copy ONLY `api_keys` and
    `providers` from the user's real config and set default_provider /
    default_model from --model (`provider:model`).
    """
    home = profile_home(profile)
    os.makedirs(home, exist_ok=True)
    real = os.path.expanduser("~/.openprogram/config.json")
    cfg: dict = {}
    if os.path.exists(real):
        try:
            src = json.load(open(real))
            cfg = {k: src[k] for k in ("api_keys", "providers") if k in src}
        except (OSError, ValueError):
            cfg = {}
    if model:
        provider, _, mid = model.partition(":")
        if mid:
            cfg["default_provider"] = provider
            cfg["default_model"] = mid
        else:
            cfg["default_model"] = provider
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump(cfg, f, indent=2)


def usage_db_path(profile: str) -> str:
    p = os.path.join(profile_home(profile), "usage.db")
    return p if os.path.exists(p) else os.path.expanduser("~/.openprogram/usage.db")


def collect_usage(db_path: str, t0: float, t1: float) -> dict:
    """Sum usage_events in the [t0, t1] wall-clock window.

    Time-window attribution because `--print` does not report its session
    id on stdout. Each trial runs under its own --profile, so as long as
    the profile's own usage.db exists the window only ever contains this
    trial's rows; the shared-db fallback is best-effort.
    """
    empty = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "total_tokens": 0, "cost_total": 0.0,
             "n_events": 0, "models": [], "session_ids": []}
    if not os.path.exists(db_path):
        return empty
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, total_tokens, cost_total, model_id, session_id "
            "FROM usage_events WHERE ts >= ? AND ts <= ?", (t0, t1)).fetchall()
        con.close()
    except sqlite3.Error:
        return empty
    out = dict(empty)
    models, sessions = set(), set()
    for i, o, cr, cw, tot, cost, model, sess in rows:
        out["input_tokens"] += i or 0
        out["output_tokens"] += o or 0
        out["cache_read_tokens"] += cr or 0
        out["cache_write_tokens"] += cw or 0
        out["total_tokens"] += tot or 0
        out["cost_total"] += cost or 0.0
        models.add(model)
        sessions.add(sess)
    out["n_events"] = len(rows)
    out["models"] = sorted(m for m in models if m)
    out["session_ids"] = sorted(s for s in sessions if s)
    return out


# ---------------------------------------------------------------- one trial

def run_trial(task: str, variant: str, model: str | None, trial: int,
              *, dry_run: bool, reference: bool, timeout: int,
              keep: bool, no_script: bool = False) -> dict:
    prompt = open(os.path.join(TASKS_DIR, task, "task.md")).read()
    prompt = apply_no_script(task, prompt, no_script)
    workdir = tempfile.mkdtemp(prefix=f"dagabl-{task}-")
    profile = f"dagabl{uuid.uuid4().hex[:10]}"
    # The suffix only lands on the log_* tasks, so a --no-script sweep over
    # "all" produces rows where some tasks ran unmodified. Record what THIS
    # row actually got, not what the flag asked for, so the analysis never
    # has to re-derive it.
    constrained = no_script and task in NO_SCRIPT_TASKS
    row: dict = {
        "task": task,
        "variant": variant_label(variant, constrained),
        "base_variant": variant,
        "no_script": constrained,
        "model": model, "trial": trial,
        "mode": "reference" if reference else ("dry-run" if dry_run else "live"),
        "unsupported_env": unsupported_vars(variant),
        "profile": profile, "started_at": time.time(),
    }
    try:
        prepare_workdir(task, workdir)

        if reference:
            apply_reference(task, workdir)
            row.update(wall_s=0.0, returncode=0, usage=collect_usage("", 0, 0),
                       stdout_tail="")
        else:
            env = dict(os.environ)
            env.update(VARIANTS[variant])
            env["OPENPROGRAM_NO_AUTO_UPDATE"] = "1"
            env["OPENPROGRAM_NO_WEB"] = "1"
            if dry_run:
                # Stand-in for the real agent: no LLM call, no cost. Proves
                # the copy -> invoke -> grade -> usage -> record chain.
                cmd = ["echo", f"[dry-run] would run openprogram --print in "
                                f"{workdir} ({len(prompt)} chars of prompt)"]
            else:
                # Model is config-driven, not env-driven: seed the throwaway
                # profile's config.json with credentials + the target model.
                seed_profile(profile, model)
                cmd = ["openprogram", "--profile", profile, "--print", prompt]
            t0 = time.time()
            try:
                r = subprocess.run(cmd, cwd=workdir, env=env, timeout=timeout,
                                   capture_output=True, text=True)
                rc, out, err = r.returncode, r.stdout, r.stderr
            except subprocess.TimeoutExpired:
                rc, out, err = -9, "", "TIMEOUT"
            t1 = time.time()
            row.update(
                wall_s=round(t1 - t0, 3), returncode=rc,
                usage=collect_usage(usage_db_path(profile), t0, t1 + 5),
                stdout_tail=out[-2000:], stderr_tail=err[-2000:],
            )

        row["score"] = grade(task, workdir)
    except Exception as e:                      # a broken trial is a data point
        row["score"] = 0.0
        row["error"] = f"{type(e).__name__}: {e}"
    finally:
        row["workdir"] = workdir if keep else None
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
            # Throwaway state dir; only ever ~/.openprogram-dagabl<hex>.
            if profile.startswith("dagabl"):
                shutil.rmtree(profile_home(profile), ignore_errors=True)
    return row


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", default="all",
                    help="task name, comma list, or 'all' (default: all)")
    ap.add_argument("--variant", default="full", choices=sorted(VARIANTS),
                    help="ablation condition (default: full)")
    ap.add_argument("--model", default=None,
                    help="provider:model, e.g. anthropic:claude-sonnet-4-5")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--timeout", type=int, default=1800,
                    help="per-trial seconds (default 1800)")
    ap.add_argument("--dry-run", action="store_true",
                    help="replace the agent with `echo`; no LLM, no cost")
    ap.add_argument("--reference", action="store_true",
                    help="overlay the reference solution and grade it; "
                         "validates the graders, no LLM")
    ap.add_argument("--keep", action="store_true", help="keep temp workdirs")
    ap.add_argument("--no-script", action="store_true",
                    help="forbid computing log_* answers with a script, so the "
                         "agent must read the log in context. Only affects the "
                         "four log_* tasks; their rows get the "
                         f"'{NO_SCRIPT_SUFFIX_LABEL}' variant suffix.")
    ap.add_argument("--i-know-this-costs-money", action="store_true",
                    dest="paid", help="required for a real billed run")
    args = ap.parse_args(argv)

    tasks = list_tasks() if args.task == "all" else [
        t.strip() for t in args.task.split(",") if t.strip()]
    unknown = [t for t in tasks if t not in list_tasks()]
    if unknown:
        ap.error(f"unknown task(s): {unknown}. Known: {list_tasks()}")

    live = not (args.dry_run or args.reference)
    if live and not args.paid:
        ap.error("a live run calls a billed model API. Re-run with "
                 "--i-know-this-costs-money, or use --dry-run / --reference.")
    if live and not args.model:
        ap.error("--model is required for a live run")

    missing = unsupported_vars(args.variant)
    if missing and live:
        print(f"[warn] variant {args.variant!r} needs env switches the product "
              f"does not implement yet: {missing}. The run will proceed but "
              f"the condition is NOT faithful — see MISSING_SWITCHES in run.py.",
              file=sys.stderr)

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    variant_tag = args.variant + ("_no-script" if args.no_script else "")
    path = os.path.join(args.out,
                        f"{stamp}_{variant_tag}_{args.task.replace(',', '+')}.jsonl")

    n_ok = 0
    with open(path, "w") as f:
        for task in tasks:
            for trial in range(args.trials):
                row = run_trial(task, args.variant, args.model, trial,
                                dry_run=args.dry_run, reference=args.reference,
                                timeout=args.timeout, keep=args.keep,
                                no_script=args.no_script)
                f.write(json.dumps(row) + "\n")
                f.flush()
                n_ok += row["score"] >= 1.0
                print(f"{task:22} {row['variant']:14} trial={trial} "
                      f"score={row['score']:.2f} "
                      f"wall={row.get('wall_s', 0):.1f}s "
                      f"in={row.get('usage', {}).get('input_tokens', 0)} "
                      f"out={row.get('usage', {}).get('output_tokens', 0)}"
                      + (f"  ERR {row['error']}" if row.get("error") else ""))

    total = len(tasks) * args.trials
    print(f"\n{n_ok}/{total} scored 1.0 -> {path}")
    return 0 if n_ok == total or live else (0 if n_ok == total else 1)


if __name__ == "__main__":
    raise SystemExit(main())
