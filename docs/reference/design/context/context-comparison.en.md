# Context Composition Comparison — Reference Projects vs. Us (organized by the three layers)

Status: **gap-finding record** · Created: 2026-06-24

> Compare the context components that reference projects feed to the LLM against our L0/L1/L2 design, to find out **what we are missing**.
> This document is comparison only; it does not change the design.
>
> **Corroboration**: Hermes itself is also **three-layered, partitioned by stability** — the same line of thinking as our L0/L1/L2.
> It calls them `stable / context / volatile`:
> - `stable`  = identity + tool guidance + skills + model/platform/environment hints  → our **L0**
> - `context` = caller system_message + context files (AGENTS.md, etc.) → our **L1 project layer**
> - `volatile`= memory snapshot + USER.md + external memory → our **L1 project memory / L2**
>
> So three layers is the right organizing scheme. Below we compare per L0/L1/L2, dropping each missing component directly into the layer where it belongs.
> The other three projects (opencode / claude-code / openclaw / pi-mono) all have fewer context components than Hermes, on par with us or fewer; almost everything we are missing comes from Hermes.

✓=present, -=absent, △=present but scattered, not assigned to a layer.

---

> **Within a layer, also order by stability**: more stable goes first, more frequently changing goes last (cache-prefix matching means intra-layer order matters too). The `#` column in each table below is **the wire order from front to back within that layer**. Per-turn-appended things like history go at the end of their layer. The ordering follows Hermes's `stable_parts` append order + our caching principle.

## L0 System level (configured once, never changes)

Intra-layer order: identity (most stable) → guidance blocks → tools/skills → environment info (relatively more variable, goes later).

| # | Component | hermes | claude-code | others | us | missing? |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | overall identity | ✓ | ✓ | pi ✓ | ✅ L0 implemented | — (identity) |
| 2 | inline agent prompt | ✓ | ✓ | — | ✅ L0 implemented | — (inline_prompt) |
| 3 | **tool enforcement (act-don't-ask)** | ✓ | - | - | ✅ L0 implemented | — (tool_enforcement, constant) |
| 4 | **model-specific operating guidance** | ✓ | - | - | ✅ L0 implemented | — (model_guidance, per provider) |
| 5 | **platform rendering format (multi-channel)** | ✓ | - | - | ✅ L0 implemented | — (platform_format, per channel parameter) |
| 6 | computer-use guidance | ✓ | - | - | ✗ | missing · low (only when that tool is enabled) |
| 7 | skills index | ✓ | - | pi ✓ | ✅ L0 implemented | — (skills_index) |
| 8 | tools + MCP schema | ✓ | ✓ | oc/oclaw ✓ | ✓ L0 | — |
| 9 | global/user-level memory | ✓ | - | - | ✅ L0 implemented | — (memory_global) |
| 10 | environment info (OS / shell / remote backend) | ✓ | - | - | ✅ L0 implemented | — (environment: OS/shell; cwd handled separately by tool-runtime) |
| 11 | current date (day granularity, cache-friendly) | ✓ | - | pi ✓ | ✅ L0 implemented | — (current_date, day granularity) |

> Ordering note: identity/guidance/tools are configured once and never touched, so they go first; environment info (OS/backend/date), although also stable across a whole session, is "closer to changing" than identity (it changes when you switch machines / the next day), so it goes at the end of L0.
> L0 is fully implemented except computer-use guidance (low priority).

---

## L1 Session/project level (follows the project/session, variable)

Intra-layer order: fixed project info (changes only when you switch projects, most stable) → session bindings → security detection → **history (appended per turn, last)**.

| # | Component | hermes | claude-code | others | us | missing? |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | project identity (AGENTS.md / .cursorrules) | ✓ | ✓ | oclaw ✓ | ✓ L1 | — |
| 2 | **prompt-injection detection** (scan before 1 is loaded into the prompt) | ✓ | - | - | ✅ L1 implemented | — (pi_shield + detect_injection_patterns) |
| 3 | context-file truncation policy (bounds the size of 1) | ✓ | - | - | ✅ L1 implemented | — (workspace_files truncation, MAX_WORKSPACE_CHARS=8000) |
| 4 | project-level memory | ✓ | - | - | ✓ L1 | — |
| 5 | **user profile USER.md** | ✓ | - | - | ✅ L1 implemented | — (user_profile, loaded by workspace_files via read_user_md) |
| 6 | working directory cwd | ✓ | - | pi ✓ | ✓ L1 | — |
| 7 | whether inside a git repo | ✓ | - | - | ✅ L1 implemented | — (git_repo_flag) |
| 8 | session_id / model / thinking / tier | ✓ | - | - | ✓ L1 | — |
| 9 | deferred tools catalog | - | - | - | ✓ L1 | — |
| 10 | **history messages (results) + tool-call records** | ✓ | - | - | ✓ L1 | — (appended per turn, ordered last) |

> Ordering note: fixed project info (AGENTS.md / project memory / USER.md / cwd / bindings) changes only when you switch projects, so it goes first; **history is appended every turn, is the least stable, and goes at the end of L1** — exactly the "keep the constantly-changing history at the back" you mentioned.
> Injection detection / truncation policy sit right next to the project files they guard (2 and 3 follow 1).
> L1 is fully implemented.

---

## L2 Task level (used once then discarded, this turn)

Intra-layer order: this turn's situation/environment (relatively stable) → this turn's input → this turn's output spec → timestamp (very last).

| # | Component | hermes | claude-code | others | us | missing? |
|:--:|---|:--:|:--:|:--:|---|---|
| 1 | this turn's situation (which function / call stack / which step) | ✓(_situational) | - | - | ✅ L2 implemented | — (situation + call_path, step 6a/6b) |
| 2 | **git branch / status** (this turn's environment snapshot) | △(git root) | - | - | ✅ L2 implemented | — (git_status, L2 order=20) |
| 3 | **todo list / task plan / progress** | - | ✓(todo tool) | - | ✅ L2 implemented | — (todo_progress, reads _TODOS) |
| 4 | token budget hint | - | - | - | ✗ | missing · low |
| 5 | per-turn memory prefetch (material retrieved for this turn) | ✓ | - | - | ✓ L2 (currently wrongly stuffed into system) | — |
| 6 | this turn's user input + attachments | ✓ | ✓ | ✓ | ✓ L2 | — |
| 7 | output format / schema | - | ✓ | - | ✓ L2 | — |
| 8 | output contract output_contract | - | - | - | ✅ L2 implemented | — (inside _situational_prefix) |
| 9 | timestamp | ✓ | - | pi ✓ | ✓ L2 | — (changes every time, very last) |
| — | Kanban multi-agent coordination | ✓ | - | - | ✗ | missing · low (specific to Hermes multi-agent) |

> Ordering note: situation/environment/todo are "this turn but relatively settled", so they go first; user input + output spec are in the middle; timestamp changes every time, so it goes very last.
> Remaining unimplemented in L2: token budget hint (low).

---

## Missing-items summary (by priority, to decide what to add)

**✅ Implemented**
- L0 overall identity (identity)
- L0 inline agent prompt (inline_prompt)
- L0 tool enforcement act-don't-ask (tool_enforcement)
- L0 model-specific operating guidance (model_guidance)
- L0 platform rendering format (platform_format, per channel parameter)
- L0 skills index (skills_index)
- L0 global/user-level memory (memory_global)
- L0 environment info (environment: OS/shell)
- L0 current date (current_date, day granularity)
- L1 prompt-injection detection (pi_shield + detect_injection_patterns)
- L1 USER.md user profile (user_profile, loaded by workspace_files via read_user_md)
- L1 git-repo flag (git_repo_flag)
- L2 this turn's situation + call_path (_situational_prefix + _compute_call_path)
- L2 todo list/progress (todo_progress, reads _TODOS)
- L2 output_contract (rendered as the `Your output:` line inside _situational_prefix)
- L1 context-file truncation policy (workspace_files truncation, MAX_WORKSPACE_CHARS=8000)
- L2 git branch/status (git_status, L2 order=20)

**Low (vendor-specific / specialized, mostly not adding)**
- computer-use guidance / Nous subscription / Kanban multi-agent / Hermes profile / external memory provider

---

## Next steps
Once we decide what to add, write them into the corresponding layer in `context-composition.md` + the cross-reference table in §"three''". After this gap-finding is complete, this document can be deleted or kept for traceability.
