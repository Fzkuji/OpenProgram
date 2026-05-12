"""Interactive CLI chat entry point + deep_work runner."""
from __future__ import annotations


def _cmd_cli_chat(oneshot: str | None = None,
                  resume: str | None = None,
                  tui: bool = True) -> None:
    """Terminal chat entry point — delegates to openprogram.cli_chat.run_cli_chat."""
    from openprogram.cli_chat import run_cli_chat
    run_cli_chat(oneshot=oneshot, resume=resume, tui=tui)


def _cmd_deep_work(task, level, provider, model,
                   max_steps, max_revisions, interactive):
    """Run a deep_work session and stream the per-phase callback to stdout."""
    from openprogram.programs.functions.buildin.deep_work import deep_work
    from openprogram._cli_cmds.programs import _get_runtime

    runtime = _get_runtime(provider, model)

    print(f"Deep work session")
    print(f"  Task: {task}")
    print(f"  Level: {level}")
    print(f"  Runtime: {runtime.__class__.__name__}")
    print()

    def on_update(result):
        rtype = result.get("type", "?")
        if rtype == "clarify":
            plan = result.get("plan_summary", "")
            if plan:
                print(f"  Plan: {plan[:200]}")
        elif rtype == "step":
            action = result.get("action", "?")
            print(f"  [step] {action}")
            if result.get("ready_for_review"):
                print(f"  → Submitting for evaluation...")
        elif rtype == "evaluation":
            score = result.get("score", "?")
            verdict = result.get("verdict", "?")
            passed = result.get("passed", False)
            icon = "PASS" if passed else "FAIL"
            print(f"  [eval] [{icon}] Score: {score}/10 — {verdict}")
            if not passed:
                feedback = result.get("feedback", "")
                if feedback:
                    print(f"  Feedback: {feedback[:200]}")
                print(f"  → Revising...")

    result = deep_work(
        task=task,
        level=level,
        runtime=runtime,
        max_steps=max_steps,
        max_revisions=max_revisions,
        callback=on_update,
        interactive=interactive,
    )

    print()
    if result.get("done"):
        evals = result.get("evaluations", [])
        final_score = evals[-1].get("score", "?") if evals else "?"
        print(f"Completed in {result['steps']} steps, {result.get('revisions', 0)} revision(s).")
        if evals:
            print(f"Final score: {final_score}/10")
    else:
        print(f"Stopped after {result['steps']} steps.")
        if result.get("error"):
            print(f"Reason: {result['error']}")
