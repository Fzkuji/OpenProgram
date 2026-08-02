# Self-Recursion Guard for Agentic Functions

> An agentic function guards against calling itself through situational guidance,
> with a recursion-depth ceiling as a backstop. This document maps to the real code
> line by line via file:line, so you can check along.
> Related code:
> - `openprogram/agentic_programming/function.py`
> - `openprogram/agentic_programming/runtime.py`
> - Tests: `tests/agentic_programming/test_self_recursion_guard.py` (8 cases)

---

## 1. The problem: why agentic functions self-recurse

An agentic function (e.g. `wiki_agent`) runs an inner agent loop in its body — it drives the inner LLM via `runtime.exec(content=[task])`.

Two triggers compound:

1. **The default toolset = full, which includes the function itself.** A bare `runtime.exec(content=...)` passes no `tools=` / `toolset=`, so `_call_via_providers` resolves it to `DEFAULT_TOOLSET = "full"`:
   - `openprogram/agentic_programming/runtime.py:1467` `DEFAULT_TOOLSET = "full"`
   - `runtime.py:1468-1483` the `raw_tools is None` branch → `_resolve_agent_tools(toolset="full", ...)`
   - and the `full` toolset lists all harness entry points themselves (`wiki_agent` / `research_agent` / `gui_agent` …, see `openprogram.functions.TOOLSETS["full"]`). So the inner model's tool list **contains the very function it is executing**.

2. **The model sees the docstring match the task and mistakenly thinks it should call it.** The model sees `wiki_agent`'s tool description ("Maintain a wiki vault — route to ingest…") match the current task exactly, decides it should route to `wiki_agent` → calls itself → enters another bare exec, sees itself again → infinite recursion.

A worked root-cause example of 7-level nesting is in `docs/reference/design/TODO-doc-code-gaps.md` §1; the session log's `context_tree` there shows the chain `4d76→0c07→0964→c6f9→f1c9→4379→8746→100c`.

---

## 2. Design philosophy: why "guidance" instead of "deny"

**The model understands its own situation and decides on its own not to call, rather than having the function hidden from its tool list.**

The alternative is a deny approach, where the wrapper pushes the function's own name into `_current_tool_policy["deny"]` so the inner model cannot see itself. Two things argue against it:

- The model never learns situational judgment. It does not know "I'm inside X"; it only sees that X is not in the tool list. In a context where deny does not apply, such as a cross-function cycle, it makes the same mistake.
- The framework decides for the model instead of giving the model enough information to decide correctly itself.

Guidance turns "don't call yourself" into situational information the model can act on: you are inside X, and calling X re-enters where you are. The model then decides on its own not to call. A **depth ceiling** independent of the model's judgment stays in place as a loss-limiting backstop.

---

## 3. How the three mechanisms work together

### (Primary) Situational prompt — prevents it from "happening"

`_situational_prefix(fn_name, fn_doc)` (`runtime.py:321-341`) generates an English situational prompt:

```
[Execution context] You are currently running INSIDE the agentic function `{fn_name}`.
The tool list may include `{fn_name}` itself — do NOT call it. Calling `{fn_name}`
re-enters where you are now and causes infinite recursion. Use lower-level tools
(search / read-write files / run code) to do the work directly.
```

When `fn_doc` is non-empty, the docstring is **demoted to the end** (`text += f"\n\nThis function's job: {fn_doc.strip()}"`, `runtime.py:339-340`) — the trigger (the docstring description) no longer outweighs the warning.

**Where it is injected: the text block at the start of the user turn, not into the system prefix.**

- DAG path: `runtime.py:578-587` builds `frame_prefix_blocks` (reading `name` + `metadata.doc` from the current frame node), then `runtime.py:597` `_build_pi_context(frame_prefix_blocks + (content or []))` — prepended before the current turn's `content`, as the leading block of the current turn's user message.
- standalone fallback path (no store): `runtime.py:1518-1532`, takes the deepest function name from `_recursion_depth` (`max(_depths, key=_depths.get)`, `runtime.py:1525`), calls `_situational_prefix(_cur_fn, "")` (no doc), and likewise prepends before `content` (`runtime.py:1532`).
- The system prefix is assembled separately (`runtime.py:1535-1539`: `self.system` + `_skills_block()`); **the situational prompt does not go into system**.

**Why put it in the user turn, not in system:** the whole project shares a **unified and constant** system prompt (identity + project memory + unified tool list + skills) to maximize KV cache hits (`dag/overview.md`) — change the prefix and a long context misses entirely afterward, blowing up cost. The situational prompt varies **per function, per call site** (each function name/docstring differs); putting it in system would break the constant prefix. Putting it at the start of the user turn lets the model see it without touching the system prefix.

### No self-deny — the tool list includes the function itself

The wrapper does not push the function's own name into `_current_tool_policy["deny"]`. The inner model's tool list **still shows the function itself**, which is what gives the situational prompt a subject; the prompt makes the model decide on its own not to call.

**The other uses of `_current_tool_policy` are unaffected**: `source` / `allow` / `toolset` / unattended deny. See `runtime.py:1451-1458` — `policy.get("deny")` is in use, merged with unattended's `denied_ask_tools` (`runtime.py:1457`); `source`/`allow`/`toolset` take effect at `runtime.py:1469`/`1479-1482`. The only thing absent is injecting the function's own name into deny.

### (Backstop) Depth ceiling — a loss-limiting safety net

- `_MAX_AGENTIC_RECURSION_DEPTH = 5` (`function.py:48`).
- `_recursion_depth` is a `ContextVar[Optional[dict]]` (`function.py:49-51`) holding the current nesting depth **per function name** `{name: depth}`.
- On entering the wrapper: take this function's name (`getattr(self, "tool_name", None) or fn.__name__`, sync `function.py:964`, async `function.py:852`), read the current depth, **raise `RecursionError` if over the limit**, otherwise +1 and write it back (saving the token):
  - sync: `function.py:964-976`
  - async: `function.py:852-864`
- Exact raise condition: `_cur_depth >= _MAX_AGENTIC_RECURSION_DEPTH` (i.e. raise when already at level 5 and about to enter level 6). Message: `f"agentic function {name} exceeded max nesting depth {5} — possible runaway recursion"` (sync `function.py:967-972`, async `function.py:855-860`).
- `finally` reset: `_recursion_depth.reset(token)` (sync `function.py:989`, async `function.py:877`) — reset on both return and exception.

**Normal calls never reach the ceiling**: the situational prompt stops it from "happening" first; the depth counter only fires after the model ignores the guidance and re-enters the same-named function 5 levels in a row.

**Roles of the three:** situational prompt = prevent occurrence (let the model decide not to call); no self-deny = the complement (the tool is visible, so the guidance has a subject); depth ceiling = loss-limiting safety net (don't burn infinite tokens when the model goes out of control).

---

## 4. Key code-location table

| Mechanism | Code | file:line |
|---|---|---|
| Depth-ceiling constant | `_MAX_AGENTIC_RECURSION_DEPTH = 5` | `function.py:48` |
| Depth-counter contextvar | `_recursion_depth` | `function.py:49-51` |
| sync wrapper: this function's name | `getattr(self,"tool_name",None) or fn.__name__` | `function.py:964` |
| sync wrapper: raise when over limit | `if _cur_depth >= MAX: raise RecursionError` | `function.py:967-972` |
| sync wrapper: +1 write-back | `_recursion_depth.set({**prev, name: cur+1})` | `function.py:973-976` |
| sync wrapper: finally reset | `_recursion_depth.reset(token)` | `function.py:989` |
| async wrapper: this function's name | same as above | `function.py:852` |
| async wrapper: raise when over limit | same as above | `function.py:855-860` |
| async wrapper: +1 write-back | same as above | `function.py:861-864` |
| async wrapper: finally reset | same as above | `function.py:877` |
| Situational-prompt text | `_situational_prefix(fn_name, fn_doc)` | `runtime.py:321-341` |
| Situational-prompt injection (DAG path) | `frame_prefix_blocks` → `_build_pi_context(prefix + content)` | `runtime.py:578-587`, `597` |
| Situational-prompt injection (standalone fallback) | take deepest name from `_recursion_depth` → prepend before content | `runtime.py:1518-1532` |
| System prefix assembled separately (no prompt) | `self.system` + `_skills_block()` | `runtime.py:1535-1539` |
| Other uses of `_current_tool_policy` | deny/source/allow/toolset resolution | `runtime.py:1451-1483` |

---

## 5. Behavioral contract (distilled from the tests)

From `tests/agentic_programming/test_self_recursion_guard.py`:

| # | Contract | Test |
|---|---|---|
| 1 | The situational prompt contains the function name, contains "do NOT call it", contains "recursion", and the docstring is demoted to the end (`recursion` appears before the docstring) | `test_situational_prefix_warns_against_self_call` |
| 2 | With an empty docstring, "This function's job" is not appended, and the prompt still contains the function name | `test_situational_prefix_handles_empty_doc` |
| 3 | The function's own name is **not** put into `_current_tool_policy["deny"]` | `test_self_name_NOT_denied_during_call` |
| 4 | During a normal one-level call, this function's name has depth = 1 (+1 on entry) | `test_depth_increments_during_call` |
| 5 | Mindless self-calling over the limit raises `RecursionError`, with the message containing the function name + the limit number; the number of times the function body is entered is exactly `_MAX_AGENTIC_RECURSION_DEPTH` (stops at the limit, doesn't go deeper) | `test_depth_backstop_raises_past_limit` |
| 6 | A→B with different names count independently: B's deep nesting doesn't count toward A's quota, and vice versa (per-name, no collateral damage) | `test_distinct_subcalls_not_collateral_damage` |
| 7 | After return, the depth resets back to its value before the call | `test_depth_restored_after_return` |
| 8 | After an exception is raised, the depth also resets | `test_depth_restored_after_exception` |

Supplement: the test uses a `_deny()` helper (`test:51-53`) that reads `_current_tool_policy.get(None).get("deny")`, and a `_depth(name)` helper (`test:55-56`) that reads `_recursion_depth.get(None).get(name, 0)` — when checking, you can use these two read patterns to confirm the count/deny shape.

---

## 6. Comparison with the deny approach

| Dimension | Deny: hide the tool | This design: situational guidance + depth ceiling |
|---|---|---|
| How | the wrapper pushes the function's own name into `_current_tool_policy["deny"]`, so the inner model can't see itself | the tool list includes the function itself; a situational prompt is injected at the start of the user turn so the model decides on its own not to call; over 5 levels raises `RecursionError` as a backstop |
| Model awareness | doesn't know "I'm inside X", just that X isn't in the list | explicitly knows the situation (you are inside X, calling X = recursion) |
| Effect on the system-prefix cache | deny is at the policy layer and doesn't touch system, but hiding decides for the model | the prompt goes in the user turn, not into system, so the prefix stays constant |
| Loss-limiting on runaway | relies on hiding to block indirectly, and is bottomless if hiding fails | an explicit 5-level depth ceiling as a hard stop |
| Strengths | direct, no model cooperation needed | the model learns situational judgment; deterministic backstop |
| Weaknesses | the model never learns situational judgment; runs wild once hiding doesn't take effect | pure guidance isn't fully reliable for weak models, hence the depth-ceiling backstop |

---

## 7. Known limitations

1. **Pure guidance isn't fully reliable for weak models / long contexts.** The situational prompt asks the model to judge on its own; a weak model, or a context long enough to dilute the prompt, may still call itself — hence the depth ceiling as a deterministic backstop.
2. **Cross-function cycles (alternating A→B→A) are not covered.** The depth ceiling counts **per same name** (`_recursion_depth[name]`) and blocks direct self-recursion (A→A→A…) only. In an alternating cycle like A→B→A→B, A's depth increments to a certain level and B's likewise, so neither name's ceiling fires. Whole-call-chain detection, counting it as a cycle if A appears anywhere on the call chain, is a possible enhancement.
3. **A deny approach would not cover cross-function cycles either.** Deny pushes the current function itself into deny; when A runs, deny holds A, but B can still be called, and A called inside B is not in B's deny either. Both approaches guard against direct self-recursion only, so cross-chain detection is an enhancement to either one.

---

## Related documents

- `docs/reference/design/runtime/dag/overview.md` — the unified-system-prefix constraint; this mechanism puts the situational prompt in the user turn to obey that constraint.
- `docs/reference/design/TODO-doc-code-gaps.md` §1 — the 7-level nesting root-cause example.
