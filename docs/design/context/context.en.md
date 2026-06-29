# Context — The Context Layer

**Assembles conversation history + current input into what gets fed to the LLM on each call.** Upstream of
[`../providers/`](../providers/): context produces a `Context` (system /
messages / tools), and providers translate it into each vendor's wire request.

Contract = `Context` (system / messages / tools, where content blocks may carry `cache_control`).
This layer decides **what to feed and how to layer it**; providers decide **how to send it to a given vendor**. The two layers are decoupled.

> For the target state (layering each call by stability + letting the model know its situation), see
> [`context-composition.md`](context-composition.md); this document covers the current mechanism.

---

## 1. Current Pipeline

The main chat path has only one true context-assembly engine:

```
dispatcher.process_user_turn
  → engine.prepare()            ← core: 6 steps producing TurnPrep
      1. reference scan (references)
      2. select history messages ── commit-chain (default) or dag (flag), pick one
      3. assemble system prompt (identity + workspace files + skills + memory)
      4. compute token budget (four-way split: system / history / tools / output reserve)
      5. usage reconciliation (provider-measured + local estimate)
      6. return TurnPrep
  → should_auto_compact? → compact() (optional, inline LLM-summarized)
  → agent_loop → Context(system, messages, tools) → provider
```

Thresholds: 70% warning / 80% auto-compaction / 95% emergency (`engine.py`).

**Main path vs. auxiliary paths**: outside the main path, 8–10 features each make their own single LLM call (summarization /
memory sleep / branch summary / mixture-of-agents / agentic runtime). They bypass
engine.prepare and hand-roll a minimal `Context` (essentially just system + one user message, no tools).
This is intentional layering — these calls don't need the full chat context.

---

## 2. Storage Model: DAG

Conversation memory splits into two layers:

- **DAG**: the true conversation history. Append-only, never rewritten. Stores user / assistant / tool /
  runtime nodes, plus the topology of branch / retry / attach / merge.
- **ContextCommit**: an immutable snapshot of the "LLM input context" under a given head (the result after compaction, aging,
  summary, and attach expansion). See §3.

### Nodes

```python
@dataclass
class Call:
    id: str
    seq: int                      # monotonically increasing within a session; the sole ordering key (≠ wall-clock)
    created_at: float
    role: Literal["user", "assistant", "tool", "system"]
    name: str                     # tool name / model name / ""
    input: Any                    # tool args / system text
    output: Any                   # tool result / assistant content / user content (permanent original text)
    predecessor: Optional[str]    # conversation edge (user/assistant chain)        ┐ mutually exclusive
    caller: Optional[str]         # call edge (assistant → tool → sub-llm)           ┘
    reads: list[str]              # declares which nodes were read
    metadata: dict
```

Constraints: `predecessor`/`caller` are mutually exclusive (guaranteed on the write side); `output` is permanent original text, and no
aging/compact ever touches it.

### The git metaphor: retry / edit / fork are the same operation

Each turn is a "commit", and `predecessor` is the object it responds to. retry / edit / fork
are essentially identical — hanging a divergent sibling under some node's parent, differing only in how they're triggered:

| Operation | Meaning |
|---|---|
| Send a new message | append a node under the current HEAD, HEAD advances |
| retry | a same-content sibling (assistant re-sampling / function re-run) |
| edit | a different-content sibling (forks under the same parent) |
| switch version `< N/M >` | checkout to a sibling, **display-only, no re-run** |
| branch into a new session | new Session, HEAD points at some node, diverges going forward |

Key points: **checkout never triggers a re-run** (switching HEAD only re-renders); **edit/retry are forbidden while an agent is running**
(to avoid hanging the active execution tree on a node that's about to "go stale"); **workdir does not roll back with checkout**
(side effects belong to the user, just as `git checkout` won't re-run your tests).

---

## 3. ContextCommit Immutable Snapshot

An immutable snapshot of the "context fed to the LLM" under a given head. Current implementation: git-backed JSON
(`<session_repo>/context/commits/<id>.json`), with code in `openprogram/context/commit/`.

### Data Structures

```python
@dataclass
class ContextCommit:
    id: str
    session_id: str
    parent_ids: list[str]   # single parent for a normal turn; multiple parents for a merge turn
    created_at: float
    head_node_id: str
    rules_version: str
    total_tokens: int
    items: list[ContextItem]
    summary: str = ""

@dataclass
class ContextItem:
    source_node_id: str          # corresponding DAG node (summaries use a virtual sm_<hex>)
    role: str
    state: Literal["full", "aged", "cleared", "summarized", "summary"]
    locked: bool                 # True = rules no longer touch it
    rendered: str
    tokens: int
    reason: str                  # "new" / "tail_window" / "idle_60min" / "attached_from:X"
    merged_into: Optional[str]
    is_anchor: bool              # high-value original text retained during summarization
    attached_from: Optional[str] # source commit this came from via an attach expansion
```

state: `full` (original content) / `aged` (tool result replaced with short text) / `cleared` (old tool result replaced with a
fixed placeholder, cache-friendly) / `summarized` (folded into some summary, not rendered) / `summary`
(the synthesized summary item).

### Generation

`ensure_latest_commit()`: finds the nearest commit on the head's ancestor chain; if it already corresponds to the current head, returns directly;
if there are new nodes, runs `generate_commit()` (copy parent items → convert new nodes to `full` items → run
`RULE_PIPELINE` → compute tokens, save JSON). Rules only modify unlocked items, never write back to the DAG, and summary items
exist only in the commit (using `sm_<hex>`).

### Compaction: Three Rule Classes

| Rule | Trigger | Effect |
|---|---|---|
| **tool aging** | tool result exceeds the tail window (default `tail_turns=3`) | full → aged, replaced with `[tool <name>] output: <head>… <tail>` |
| **idle clearing** | aged and untouched for 60 min | aged → cleared fixed placeholder, more stable cache prefix |
| **summarize** | `total_tokens > threshold` (~70–80% budget) | the oldest run of consecutive items is folded into one `summary`; folded items are marked `summarized`; high-value ones (cited/pinned/anchor) stay full |

### Rendering

`render_commit(commit)` → provider messages: `summarized` is skipped; `summary` is rendered as
assistant text prefixed with `[Summary]`; tool items are downgraded to user text (to avoid protocol
errors when pairing information is missing). **A pure function, same input same output** — easy to test, debug, and predict cache hits.

---

## 4. Attach / Merge (Cross-Branch References and Aggregation)

attach and merge reuse the same mechanism — expanding another branch's ContextCommit into a
set of items within the current commit. The difference: **attach is passive** (stages, waits for the next LLM run), **merge is active** (immediately triggers an
LLM turn and writes a multi-parent commit).

- **Attach**: the attach pointer is a DAG node with `function="attach"` (its metadata contains the
  source session/head/commit). The generator reads `source_commit_id`, expands its items, wraps them with
  open/close markers, and tags each with `attached_from`; deduplication is by `attached_from` (**expanded only once across turns**);
  if it can't be loaded, it falls back to a single user item. Expanded items default to `full/unlocked`,
  passing through the rules on equal footing with native turns (**they can be compacted**); summarize respects attach boundaries.

- **Merge**: `process_merge_turn()` writes N temporary attach pointers after the target head
  (each pointing at a peer's terminal commit) + one merge instruction → runs `process_user_turn()`
  → saves a **multi-parent** commit (`parent_ids=[target_prev, peer_1, …]`) → marks the peer heads
  merged. The `base_peer`'s attach block is marked `locked=True` (guaranteeing the merge agent sees the original text), while the other
  peers can be summarized. Multi-parent commits come only from merge.

---

## 5. Cross-Turn Tool Context

**Problem**: if `role=tool` rows in history are dropped, the model can't see across turns which tools it called,
with what arguments, and what they returned — leading to repeated calls and hallucinating files it "already checked". This is distinct from summarizing the whole history (per-item
tool aging vs. whole-segment semantic compaction).

**Strategy** (tool aging, i.e. the first rule in §3, refined):

- **tail-window full text**: the tool_use / tool_result of the most recent `TAIL_TURNS=3` assistant turns is
  retained in full.
- **old-turn aging**: for earlier turns, the tool_use head is kept (args truncated to `MAX_TOOL_ARGS_CHARS=200`),
  and the tool_result is swapped for a one-line semantic stub.
- **per-item cap**: any tool_result over `MAX_TOOL_RESULT_CHARS=4000` is truncated at head and tail (truncated even within the tail,
  to prevent a single turn from blowing up).
- **critical-tool protection**: `PRUNE_PROTECTED_TOOLS={todo_read, todo_write, web_search}`
  do not participate in aging.

Data flow: `get_branch` → hang caller-children tool rows back onto the assistant → tool aging (full in tail
/ stub for old ones) → per-item truncation → (when over threshold) whole-segment summarize → render into ToolUse +
ToolResult content blocks. Code: `openprogram/context/tool_aging/`.

Thresholds are taken from reference frameworks: tail-window (OpenCode), per-item stub (Hermes), per-item truncation + critical-tool
protection (OpenCode).

---

## 6. Invariants

1. The DAG is append-only; rules never change node content.
2. A ContextCommit is not written back once saved; new rules only affect subsequent commits.
3. A ContextItem's compaction state tightens monotonically, never reverting to a more complete state.
4. `locked=True` is not modified by rules; `state=full` is the default.
5. summary items are not written to the DAG.
6. attach expansion is deduplicated by `attached_from`, expanded only once.
7. Multi-parent commits come only from merge.
8. `render_commit` is a pure function.
9. checkout is display-only, does not re-run, does not roll back the workdir.

---

## 7. UI

- **History view**: the full DAG, with nodes = circle/triangle/square, showing all tool calls /
  retry branches / merge confluences (drawing forks from `parent_ids`).
- **ContextCommit Timeline** (right column): `web/components/right-sidebar/context-commit-timeline/`
  + `ws_actions/context_commits.py`. Lists the current commit's items with state badges + token counts
  (full/aged/cleared/summary, with their `attached_from` source).

---

## 8. Target State and Gaps

[`context-composition.md`](context-composition.md) is this layer's target: layer each LLM call into three tiers by
"how often it changes", serving the cache (stable content up front) while also letting the model know its situation.

| Tier | Content | Change frequency | Current status |
|---|---|---|---|
| **L0 constant** | identity / global instructions / tool list | unchanged for the whole session | ✅ engine already assembles it (not layered for caching) |
| **L1 situation** | which function I'm a part of / who called me / which step of the program / where the output goes | changes on each new frame | ❌ **entirely missing** |
| **L2 task** | progress within the frame / inherited upstream results / current input / output format | changes each step | ✅ engine already selects history |

Gaps (by value):

1. **The L1 situation layer is entirely missing** — the model doesn't know which @agentic_function it's a part of, who called it, or
   how its output will be used. Highest value (core to the paper's LLM-as-Code thesis), with zero implementation today.
2. **Two coexisting history-rendering paths risk parity drift** — commit-chain and dag each have their own traversal logic, and changing one
   easily forgets the other (there was a real bug where ThinkingContent was handled only on the dag path and missed on the commit path). This should be
   converged into one.
3. **Auxiliary paths' system prompt doesn't follow the agent** — summarization/branch-summary use a hard-coded
   `SUMMARIZATION_SYSTEM_PROMPT` that doesn't track the user's AGENTS.md / identity / skills.

---

## 9. Early Designs (Deprecated, Kept for Traceability)

We once designed a **SQLite `node_annotations` derived model**: DAG truth + one recomputable
Annotation per node, updated by an annotator pipeline each turn, then rendered by a `build_view` pure function. It and
ContextCommit are two ways of landing the same idea: annotation is "derived, discardable-and-recomputable + SQL table", ContextCommit is
"immutable snapshot + git JSON". **It has been superseded by ContextCommit**; its ideas that still hold (compaction tightens monotonically,
the view is a pure function, summaries aren't written to the DAG) have been folded into the text above. pinning / dedup / user manual pin-unpin are not yet
fully landed under ContextCommit, and remain capabilities to be filled in.
