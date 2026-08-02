# Context Composition Comparison — Reference Projects vs. Us (organized by the three layers)

> This document compares the context components that reference projects feed to the LLM against our L0/L1/L2 design, laid out layer by layer. It is a comparison; the design itself lives in [`composition.md`](composition.md).
>
> Hermes is also **three-layered, partitioned by stability** — the same organizing principle as our L0/L1/L2.
> It calls the layers `stable / context / volatile`:
> - `stable`  = identity + tool guidance + skills + model/platform/environment hints  → our **L0**
> - `context` = caller system_message + context files (AGENTS.md, etc.) → our **L1 project layer**
> - `volatile`= memory snapshot + USER.md + external memory → our **L1 project memory / L2**
>
> The other projects (opencode / claude-code / openclaw / pi-mono) each carry fewer context components than Hermes, on par with us or fewer.

Legend: ✓=present, -=absent, △=present but scattered, not assigned to a layer.

---

> **Within a layer, components are also ordered by stability**: more stable goes first, more frequently changing goes last, because cache-prefix matching makes intra-layer order matter too. The `#` column in each table below is **the wire order from front to back within that layer**. Per-turn-appended content such as history goes at the end of its layer. The ordering follows Hermes's `stable_parts` append order plus our caching principle.

## L0 System level (configured once, never changes)

Intra-layer order: identity (most stable) → guidance blocks → tools/skills → environment info (relatively more variable, goes later).

| # | Component | hermes | claude-code | others | us |
|:--:|---|:--:|:--:|:--:|---|
| 1 | overall identity | ✓ | ✓ | pi ✓ | ✓ L0 (identity) |
| 2 | inline agent prompt | ✓ | ✓ | — | ✓ L0 (inline_prompt) |
| 3 | **tool enforcement (act-don't-ask)** | ✓ | - | - | ✓ L0 (tool_enforcement, constant) |
| 4 | **model-specific operating guidance** | ✓ | - | - | ✓ L0 (model_guidance, per provider) |
| 5 | **platform rendering format (multi-channel)** | ✓ | - | - | ✓ L0 (platform_format, per channel parameter) |
| 6 | computer-use guidance | ✓ | - | - | - (applies only when that tool is enabled) |
| 7 | skills index | ✓ | - | pi ✓ | ✓ L0 (skills_index) |
| 8 | tools + MCP schema | ✓ | ✓ | oc/oclaw ✓ | ✓ L0 |
| 9 | global/user-level memory | ✓ | - | - | ✓ L0 (memory_global) |
| 10 | environment info (OS / shell / remote backend) | ✓ | - | - | ✓ L0 (environment: OS/shell; cwd handled separately by tool-runtime) |
| 11 | current date (day granularity, cache-friendly) | ✓ | - | pi ✓ | ✓ L0 (current_date, day granularity) |

> Ordering rationale: identity/guidance/tools are configured once and never touched, so they go first; environment info (OS/backend/date), although also stable across a whole session, is closer to changing than identity — it changes when you switch machines or when the day rolls over — so it goes at the end of L0.

---

## L1 Session/project level (follows the project/session, variable)

Intra-layer order: fixed project info (changes only when you switch projects, most stable) → session bindings → security detection → **history (appended per turn, last)**.

| # | Component | hermes | claude-code | others | us |
|:--:|---|:--:|:--:|:--:|---|
| 1 | project identity (AGENTS.md / .cursorrules) | ✓ | ✓ | oclaw ✓ | ✓ L1 |
| 2 | **prompt-injection detection** (scan before 1 is loaded into the prompt) | ✓ | - | - | ✓ L1 (pi_shield + detect_injection_patterns) |
| 3 | context-file truncation policy (bounds the size of 1) | ✓ | - | - | ✓ L1 (workspace_files truncation, MAX_WORKSPACE_CHARS=8000) |
| 4 | project-level memory | ✓ | - | - | ✓ L1 |
| 5 | **user profile USER.md** | ✓ | - | - | ✓ L1 (user_profile, loaded by workspace_files via read_user_md) |
| 6 | working directory cwd | ✓ | - | pi ✓ | ✓ L1 |
| 7 | whether inside a git repo | ✓ | - | - | ✓ L1 (git_repo_flag) |
| 8 | session_id / model / thinking / tier | ✓ | - | - | ✓ L1 |
| 9 | deferred tools catalog | - | - | - | ✓ L1 |
| 10 | **history messages (results) + tool-call records** | ✓ | - | - | ✓ L1 (appended per turn, ordered last) |

> Ordering rationale: fixed project info (AGENTS.md / project memory / USER.md / cwd / bindings) changes only when you switch projects, so it goes first; **history is appended every turn, is the least stable, and goes at the end of L1**. Injection detection and the truncation policy sit right next to the project files they guard, so 2 and 3 follow 1.

---

## L2 Task level (used once then discarded, this turn)

Intra-layer order: this turn's situation/environment (relatively stable) → this turn's input → this turn's output spec → timestamp (very last).

| # | Component | hermes | claude-code | others | us |
|:--:|---|:--:|:--:|:--:|---|
| 1 | this turn's situation (which function / call stack / which step) | ✓(_situational) | - | - | ✓ L2 (situation + call_path, step 6a/6b) |
| 2 | **git branch / status** (this turn's environment snapshot) | △(git root) | - | - | ✓ L2 (git_status, L2 order=20) |
| 3 | **todo list / task plan / progress** | - | ✓(todo tool) | - | ✓ L2 (todo_progress, reads _TODOS) |
| 4 | token budget hint | - | - | - | - |
| 5 | per-turn memory prefetch (material retrieved for this turn) | ✓ | - | - | ✓ L2 |
| 6 | this turn's user input + attachments | ✓ | ✓ | ✓ | ✓ L2 |
| 7 | output format / schema | - | ✓ | - | ✓ L2 |
| 8 | output contract output_contract | - | - | - | ✓ L2 (inside _situational_prefix) |
| 9 | timestamp | ✓ | - | pi ✓ | ✓ L2 (changes every time, very last) |
| — | Kanban multi-agent coordination | ✓ | - | - | - (specific to Hermes multi-agent) |

> Ordering rationale: situation/environment/todo are this-turn but relatively settled, so they go first; user input and output spec are in the middle; the timestamp changes every time, so it goes very last.

---

## Components we do not carry

These components exist in a reference project but have no counterpart here, because we have no corresponding feature: computer-use guidance, Nous subscription guidance, Kanban multi-agent coordination, the Hermes profile mechanism, and the external memory provider. The registration model in [`composition.md`](composition.md) leaves a slot for each — building the feature means registering one `ContextComponent`, with no framework change.

Also absent: the token budget hint, which no reference project carries either.
