# Claude Code Compaction

> Research document (not a design document). Reverse-engineered from source, documenting Claude Code's compaction mechanism.
>
> **Reliability note**: The description of the 5-tier cascade comes from third-party reverse engineering, not confirmed by Anthropic's official documentation.
> The official API docs (platform.claude.com) describe only a single compact mechanism (over token threshold → LLM summary);
> the internal Microcompact / Snip / Context Collapse layering is a Claude Code client implementation detail.
> Different reverse-engineering sources describe the execution order with slight variations. The version below follows Inside Claude Code (the most detailed analysis).
> Thresholds and parameters have been adjusted across versions; the numbers below may not be entirely accurate.
>
> Sources (ordered by reliability):
> - [Compaction - Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/compaction) (official, but describes only the API level)
> - [Context editing - Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing) (official)
> - [Inside Claude Code - Context Compaction](https://y-agent.github.io/inside-claude-code/04-context-compaction.html) (reverse engineering, most detailed)
> - [Dive into Claude Code](https://arxiv.org/html/2604.14228v1) (arXiv 2604.14228, VILA-Lab source reverse engineering)
> - [DeepWiki - Context Window & Compaction](https://deepwiki.com/anthropics/claude-code/3.3-context-window-and-compaction) (community wiki)
> - [Claude Code VS OpenCode §5.3](https://0xtresser.github.io/Claude-Code-VS-OpenCode/en/Chapter_05_Session_and_Context/5.3_Context_Compaction.html) (comparative analysis)

---

## 1. Overview

Claude Code has two kinds of compaction mechanisms:

**Routine operation** (done before every LLM call, independent of context length):
- Budget Reduction: truncate oversized individual tool outputs

**Cascade compaction** (escalates in the order Tier 1→2→3→4→5, cheapest first):
- Tier 1 Microcompact: clear old tool outputs
- Tier 2 Snip: delete the oldest few turns
- Tier 3 Context Collapse: segmented LLM summarization
- Tier 4 Auto-Compact: full LLM summarization
- Tier 5 Reactive: emergency compaction when the API returns 413

| | Name | What it does | Trigger | Calls LLM | Breaks cache | Information loss |
|---|---|---|---|---|---|---|
| Routine | Budget Reduction | Truncate oversized individual output | A single tool_result > **4000 chars** | No | No | Middle content lost |
| Tier 1 | Microcompact | Clear old tool outputs | Tool calls ≥ **50** (then every **25**) / idle **90 minutes** | No | cache-aware: no break | Old outputs lost |
| Tier 2 | Snip | Delete the oldest few turns | Still over budget after Tier 1 | No | Yes | Whole turns lost |
| Tier 3 | Context Collapse | Segmented summarization | Still over after Tier 2, ~**90%** usage | Yes | Yes | Detail lost, key points kept |
| Tier 4 | Auto-Compact | Full summarization | Still over after Tier 3 / user `/compact` | Yes | Yes | Heavy loss, only a digest kept |
| Tier 5 | Reactive | Emergency compaction | API returns **413** prompt_too_long | Yes | Yes | Same as Tier 4 |

**Cascade logic**: Before every LLM call, check starting from Tier 1. If the condition is met, execute it; after executing, recheck whether it is still over. If still over, advance to the next Tier. The earlier Tiers are cheaper (no LLM call, no cache break), so resolve it as early as possible.

> **Note**: The relationship between Tier 3 Context Collapse and Tier 4 Auto-Compact is described by reverse-engineering sources as
> "overlapping strategies," not necessarily a strict "fall back to 4 only when 3 is insufficient." Some configurations may
> skip Tier 3 and go straight to Tier 4. The `/compact` manual command directly triggers Tier 4, skipping Tiers 1-3.

---

## 2. Routine Operation: Budget Reduction

**Unrelated to the cascade**; done before every LLM call.

**What it does**: Check the size of each tool output, and truncate any that exceeds the threshold.

**Parameters**:
- Truncation threshold: **4000 chars** (about 1000 tokens)
- Truncation method: for output exceeding 4000 chars, keep only the first **2400 chars** (60%) + the last **1600 chars** (40%), discarding the middle
- Only processes tool outputs (tool_result), not user/assistant messages

**Example**:
```
Before truncation (50000 chars):
[tool_result] "src/a.py:12: TODO fix this\nsrc/b.py:34: ..."

After truncation (4000 chars):
[tool_result] "src/a.py:12: TODO fix this\n...[46000 chars removed]...\nsrc/z.py:99: TODO cleanup"
```

---

## 3. Cascade Compaction

Before every LLM call, after Budget Reduction, check and execute in Tier order.

### Tier 1: Microcompact

**What it does**: Clear old tool outputs to free up space.

**Trigger**:
- Cache-aware path: first triggers after **50 tool calls**, then again **every 25**
- Time-based path: **~90 minutes** since the last assistant message

**Only processes specific tools**: FileRead, Shell, Grep, Glob, WebSearch, WebFetch, FileEdit, FileWrite

**Two paths**:

| | Cache-aware (primary) | Time-based (fallback) |
|---|---|---|
| What it does | Has the server clear old tool_result via the Context Editing API | Client replaces old tool_result with a sentinel string |
| Breaks cache | No (server-side operation, client messages unchanged) | Yes (client modified the messages) |
| Trigger | After 50 tool calls, every 25 | Idle ~90 minutes |
| Recoverable | No | No |
| Dependency | Anthropic API's Context Editing (not supported by other providers) | No dependency |

**Compaction effect**: Each cleared tool output goes from its original size (a few hundred to a few thousand tokens) → 0 tokens (cache-aware) or ~20 tokens (time-based). Example: one Microcompact cleared 10 old tool results, each averaging 500 tokens → freed about 4800 tokens.

**Example**:
```
Before clearing:
[tool_result] "import os\nimport sys\n\nclass Config:\n    ..." (2000 tokens)

After clearing (cache-aware):
[tool_result] "" (server-cleared, 0 tokens, cache not broken)

After clearing (time-based):
[tool_result] "[content no longer available]" (~20 tokens)
```

**Cache-aware path details**: The sentinel string uses **byte-stable canonical form normalization** — repeated microcompact does not change already-cached content. Core principle: content already in the cache is not modified by the client (modifying it would break prefix matching). When old content needs clearing:
- Already in cache → cache-aware path, have the server clear it via the Context Editing API
- Not in cache → time-based path, client directly replaces it with a sentinel

#### Context Editing API (cache_edits)

The underlying implementation of the Microcompact cache-aware path. It is a public beta of the Anthropic API (header: `context-management-2025-06-27`), not exclusive to Claude Code; ordinary developers can use it.

**How it works**: The client sends the complete message history (without any modification) while passing a `cache_edits` parameter in the API request. On receipt, the server:
1. Finds the old tool_result blocks inside the cache
2. Replaces them with empty or placeholder text
3. Leaves the cache prefix unchanged (because the client's sent messages did not change; the server only modified the data inside the cache)
4. Requires the client to know nothing about which were cleared

It is merged with a normal LLM call rather than being an extra request — while sending messages, it tells the server in passing to "clear the old tool_result."

| Strategy | What it does |
|---|---|
| `clear_tool_uses` | Clear old tool_result, keeping only the most recent N (the exact value of N is not confirmed from source; reverse engineering did not give it) |
| `clear_thinking` | Clear old thinking blocks |
| `clear_at_least` | Control the minimum number of tokens to clear |

**Relationship to other Tiers**: Context Editing is not an independent Tier; it is the underlying implementation of the Tier 1 Microcompact cache-aware path. Tiers 2-5 do not use this API — they directly modify the client's message list.

**Limitation**: Only the Anthropic API supports it. OpenAI / Google / other providers have no equivalent capability and can only use the time-based path (client replacement, which breaks the cache).

### Tier 2: Snip

**What it does**: Directly delete the oldest few turns of conversation. No summarization, no disk storage — just discard.

**Trigger**: Still over budget after Tier 1 is done (a ~13K tokens buffer is mentioned in reverse engineering, but the specific threshold may vary by version)

**Parameters**:
- Deletion granularity: **whole turn** (user + assistant + all tool calls of that turn deleted together), so no inconsistency like "user present but assistant deleted"
- How much to delete: delete turn by turn starting from the oldest, recomputing the token count after each deletion, **until below the threshold** (not a fixed N turns)
- Deleted content disappears entirely; the model does not know what was discussed before
- No deletion marker (no hint like "5 turns deleted" left in the context)

**Cost**: Total information loss, and the cache prefix is broken. But free (no LLM call) and fast to execute.

### Tier 3: Context Collapse

**What it does**: Splits old conversation into several segments (5-10 turns per segment) and generates an LLM summary for each segment.

**Trigger**: Still over the threshold after Tier 2 Snip is done (about 90%, non-blocking trigger; at 95% it blocks and forces a trigger)

**Key property**: The original messages are **retained** in the collapse store. This is a **read-time projection** — similar to a database View, where the base table is untouched and the query sees a summary view. The original history is retained, so it can in theory be reconstructed/rolled back.

**Parameters**:
- Segmentation criterion: **5-10 turns per segment** (exact value not confirmed from source)
- Each segment is summarized independently by the LLM, each summary about **100-300 tokens** (estimated, unconfirmed)
- The most recent N turns are kept in full; only older segments are summarized (the value of N is unconfirmed)
- Number of LLM calls = number of segments (example: 30 turns split into 3 segments = 3 LLM calls)

**Difference from Tier 4**:
- Context Collapse is **segmented** summarization, each segment independent, preserving segment boundaries and the timeline structure
- Auto-Compact is **full** summarization, compressed into a single segment, losing structure

**Example**:
```
Before compaction (35 turns):
[turns 6-10] read code, find bug, fix, test
[turns 11-15] refactor config module
[turns 16-35] modify API module...

After compaction:
[summary] "Turns 6-10: fixed a bug in utils.py, added a rollback script"
[summary] "Turns 11-15: refactored the config module, extracted the Settings class"
[turns 16-35] ... (most recent kept in full)
```
The original turns 6-15 are still kept in the collapse store; the model sees the collapsed version.

### Tier 4: Auto-Compact

**What it does**: Sends the entire conversation history to the LLM at once, generates a single summary block, and replaces all old messages.

**Trigger**: Still over the threshold after Tier 3 is done. Or the user manually runs `/compact`.

**Key property**: The original messages are **replaced**, not rollback-able. Information loss is maximal.

**Parameters**:
- Summary block size: about **2000-5000 tokens** (depends on conversation complexity, decided by the LLM itself)
- Keeps the most recent **1-2 turns** of full conversation (not summarized)
- LLM calls: **1** (full summarization)
- After compaction, context drops from the original 70-90% to about **15-25%**
- The trigger percentage can be adjusted via the `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` environment variable

**The specific process**:
1. Run the PreCompact hooks (notify the system that compaction is imminent)
2. Construct the compaction prompt with `getCompactPrompt()` (something like "Please compress the following conversation history into key facts")
3. Send the entire conversation history to the LLM to generate the summary
4. Build the post-compaction messages with `buildPostCompactMessages()`
5. The system prompt is **reloaded** from CLAUDE.md (not derived from the summary, so the contents of CLAUDE.md are never lost)

**The `/compact` manual command**:
- `/compact` or `/compact keep the discussion about database migration`
- Directly triggers Auto-Compact, bypassing Tiers 1-3
- Summary quality is better when given a prompt (the user guides what to keep)
- The official recommendation is to manually `/compact` at **60% usage**

**Lost**: early instructions, design discussions, reasoning processes, code snippets from 50+ turns ago, style preferences.
**Kept**: the current task, recently modified filenames, recent errors and their solutions, the contents of CLAUDE.md.

**Example**:
```
Before compaction (35 turns, 150K tokens):
[system prompt]
[35 turns of full conversation history]

After compaction (~30K tokens):
[system prompt] (reloaded from CLAUDE.md)
[compaction block]
"Session summary: The user is refactoring a Python project.
Completed: fixed a bug in utils.py, refactored the config module, added 3 tests.
Current state: all tests pass. The user is working on the API module.
Key decisions: replace Flask with FastAPI, use PostgreSQL for the database.
Modified files: src/utils.py, src/config.py, tests/test_utils.py"
[most recent 1-2 turns kept in full]
```

**Chained compaction**: After compaction, the conversation continues, and when it fills again it is compacted again. Each time compacts on top of the previous summary, and information decays layer by layer — the first pass loses early detail, the second loses mid-stage detail. A long session may be compacted 3-5 times.

### Tier 5: Reactive

**What it does**: Emergency compaction when the API returns a 413 (prompt too long) error.

**Trigger**: The LLM call fails, returning a prompt_too_long / 413 error.

**Method**: Attempt context-collapse overflow recovery; if that fails, do an emergency compact. Triggers at most once per turn. If both fail, terminate the session.

---

## 4. Execution Flow

The complete flow before every LLM call:

```
1. Budget Reduction → truncate oversized tool outputs (done every turn)

2. Cascade check (in Tier order):
   Tier 1: Microcompact condition met? → clear old tool outputs
   Tier 2: still over threshold? → Snip deletes old turns
   Tier 3: still over threshold? → Context Collapse segmented summarization
   Tier 4: still over threshold? → Auto-Compact full summarization

3. Call the LLM

4. If the API returns 413:
   Tier 5: Reactive → emergency compaction → retry
   → terminate on failure
```

---

## 5. Full Run-Through Example

Scenario: 200K context, continuous work over several hours.

**Phase 1 (0-30%)**: Normal conversation. Budget Reduction checks tool output sizes; those not exceeding are skipped. None of the cascade triggers.

**Phase 2 (30-50%)**: Tool calls exceed 50. Tier 1 Microcompact cache-aware triggers for the first time, clearing the outputs of the first 30 tool calls and freeing about 15K tokens. After that it clears another round every 25. The cascade gets to Tier 1 and that is enough; Tiers 2-4 do not trigger.

**Phase 3 (over threshold)**: Conversation messages accumulate beyond the threshold. Tier 1 Microcompact runs but is still over. Tier 2 Snip triggers, deleting the oldest 5 turns and freeing about 25K tokens. It drops below the threshold; Tiers 3-4 do not trigger.

**Phase 4**: Continued use. When over the threshold again, repeat Phase 3 (Microcompact + Snip).

**Phase 5 (extreme)**: After repeated Snips there are few old turns left to delete, and Snip is no longer enough. Tier 3 Context Collapse triggers, segmented summarization. If still not enough, Tier 4 Auto-Compact. This situation is rare — because Microcompact keeps the growth of tool output under control day to day, Snip is usually enough.

---

## 6. Key Design Principles

1. **Lazy degradation**: Do the cheap things first. Microcompact (free, no cache break) → Snip (free, breaks cache) → Context Collapse (calls LLM) → Auto-Compact (calls LLM, most aggressive).

2. **Tool output cleared first**: Old grep / read_file / bash outputs are the largest and least important consumers. Clear them first, and keep conversation messages as much as possible.

3. **Day-to-day relies on Microcompact**: The cache-aware path continuously frees space without breaking the cache. This makes Snip/Compact trigger very rarely.

4. **Cache protection**: Microcompact cache-aware clears server-side via the Context Editing API, so the cache is not broken. After Budget Reduction truncation, content is fixed. Snip/Compact break the cache but trigger infrequently.

5. **Conversation messages are not truncated individually**: user/assistant messages are either deleted by the whole turn (Snip) or LLM-summarized (Compact). Individual messages are not truncated.

6. **CLAUDE.md does not participate in compaction**: It is reloaded from disk and never lost.

7. **User control**: `/compact` + `/context` let the user take the lead. Automatic is the fallback.
