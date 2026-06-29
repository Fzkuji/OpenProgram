# Three Template Rules

The previous docs covered the mechanism; this one gives three **real rules** and walks them end to end. They are both what the first version ships and a template for writing new rules later — just copy and adapt. Before reading this, finish `overview.md` and `execution-model.md`.

## Why start with only three

Not because we're short on time — it's **deliberate**. The classic way a proactive-interruption system dies isn't "too few features," it's "too many rules, too many false positives, and within three days users learn to ignore every prompt" (this is exactly how Clippy and UAC pop-ups died).

So this layer does not offer a "config file / DSL" that lets someone write a rule in ten minutes — rules must be Python classes and must pass code review. We first ship three to get the mechanism working and polish each one until it's genuinely useful, then add a fourth. The three each validate a different way of stepping in: one blocks the path, two observe (one of which also demonstrates "do your homework before speaking up").

| Rule | Type | One line |
|---|---|---|
| DangerousCommandGuard | Block the path | Catch a dangerous command before it runs and ask the user |
| TestGapWatcher | Observe (do homework first) | Core code changed without tests; verify in the background, then remind |
| UnvalidatedCompletionNudge | Observe (nudge the model first) | The model claims it's done but didn't verify; quietly let the model verify itself first |

---

## 1. DangerousCommandGuard (block the path)

**What it does**: when a tool is about to run, if it's a dangerous shell command, catch it and ask the user.

```python
class DangerousCommandGuard:
    on = {"tool.before"}
    lane = "gate"            # block the path: intercept before the command actually runs
    cooldown_s = 0

    def evaluate(self, event, state):
        if event.payload["tool"] != "bash":
            return None
        command = event.payload["command"]
        if is_dangerous_command(command):
            return Gate.ask(f"This command is risky: {command}\nConfirm execution?")
        return None
```

**Key: the judgment must see the arguments, not just match keywords.** Otherwise false positives pile up until the user reflexively clicks "confirm" in a split second, and the guardrail is useless (this is precisely how UAC pop-ups die). You have to distinguish:

| Dangerous | Not dangerous (don't false-positive) |
|---|---|
| `rm -rf /` `rm -rf ~/project` | `rm -rf /tmp/xxx`, `rm -rf node_modules` (everyday operations) |
| `git push --force origin main` (pushing a protected branch) | `git push --force` to your own feature branch (very common) |
| `kubectl delete namespace` (deleting an entire namespace) | `kubectl delete pod xxx` (everyday) |

So `is_dangerous_command()` must parse a path allowlist, check the branch name, and distinguish resource types — not something as crude as `"rm -rf" in command`.

**Be honest about how far it goes**: it guards against **slips and mistakes**, not against a malicious adversary. Clever bypasses (base64-encoding the command, writing it into a script and then running that, using another tool instead of bash) it can't catch any of these. This version positions it as a "guardrail against slips" and doesn't pretend to be a security boundary. Actually defending against an adversary requires a sandbox, which is a separate matter (covered in the archived threat-model).

**Relationship to the existing approval mechanism**: OpenProgram already has a tool-approval pop-up. Don't end up popping twice for the same command — let this rule's judgment attach as a "risk annotation" onto the existing approval flow, merged into a single confirmation.

---

## 2. TestGapWatcher (observe + do homework first)

**What it does**: when the user/model signals the round is wrapping up (about to commit, says it's done), if core code changed but tests didn't, give a reminder.

```python
class TestGapWatcher:
    on = {"model.response_completed"}    # check when the model finishes a round of talking
    lane = "observer"                    # observe: don't block the path
    cooldown_s = 1800                    # don't re-raise the same situation within half an hour

    def evaluate(self, event, state):
        # only check when "wrapping up", not on every file change
        if not is_wrapup_signal(event, state):
            return None
        if state.changed_core_code and not state.touched_tests:
            # don't remind directly — first dispatch a read-only background task to verify
            return Prepare(task="check whether this change really lacks tests and whether it's worth a reminder")
        return None
```

**Why "do homework first" (Prepare)**: reminding the moment you see "code changed without tests" produces many false positives — a comment edit, a rename, a config change, a dependency bump, a frontend change (frontend directories often have no testing culture at all) would all trigger it. So instead of speaking up directly, it first spins up a **read-only** background task to actually look at whether the change really lacks tests and whether the gap is worth raising. That task returns a judgment, and **only when it's confident enough does it Notify**; if it's not confident, it swallows the reminder and doesn't bother the user.

This is the rule most likely to turn into an annoying Clippy. Two constraints:
- **Narrow the trigger**: only check when "wrapping up" (about to commit, says it's done), not on every file change.
- **Make the cost visible**: background tasks cost money (they may call an LLM). Show a small bill in the UI — how many times this layer ran a background verification today, how much it spent, and how many times it ended up not reminding — so the user knows it's spending money and can turn it off.

**Give a one-click action in the reminder**: a Notify isn't just a sentence; include a "write the tests for me" button that, when clicked, spins up a task to draft the tests.

---

## 3. UnvalidatedCompletionNudge (observe + nudge the model first)

**What it does**: the model says "done," but this round never ran tests / never verified, which usually means it didn't actually verify.

```python
class UnvalidatedCompletionNudge:
    on = {"model.response_completed"}
    lane = "observer"
    cooldown_s = 900

    def evaluate(self, event, state):
        if event.payload["claimed_done"] and state.round_had_file_changes \
                and not state.round_had_verification:
            # default to not bothering the user — first quietly nudge the model to verify itself
            return Inject("You said it's done, but this round had no verification action. Verify first, then conclude.")
        return None
```

**Why default to Inject (nudge the model) instead of Notify (notify the user)**: this repo's conventions already require the model to verify its own work after a change. If the model follows the rules, this rule never fires; if the model cuts corners, **the cheaper fix is to quietly nudge the model** to go back and verify, rather than kicking the ball to the user to click confirm. Only when the model is nudged and still doesn't verify does it escalate to notifying the user. This also echoes the action table in the overview: Inject is "inject a sentence into the model without bothering the user."

**One detail of the judgment**: "this round had a verification action" must be counted fully — running tests counts, and using the browser (MCP) to actually open the page and check the result also counts (frontend changes are often verified this way). Missing browser verification would frequently misjudge frontend changes as "not verified."

---

## Look back at these three when writing new rules

| The rule you want to write | Which one to copy |
|---|---|
| Block before some action happens | DangerousCommandGuard (block the path, look at what's in front of you, be fast) |
| Remind once conditions accumulate, but worried about false positives | TestGapWatcher (observe, Prepare to do homework first, then Notify) |
| Want to correct the model's behavior without bothering the user | UnvalidatedCompletionNudge (observe, use Inject to nudge the model) |

Each one is: set `on`/`lane`/`cooldown_s` + write `evaluate`. Don't touch the framework core.
