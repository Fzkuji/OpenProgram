# Context Practice

Strategies for using the Context system effectively.

---

## The Core Question

We have a complete Context tree. `summarize()` can query any slice.
Every function call must decide: **what context do I need from the tree?**

This depends on:
- Where the function sits in the tree (top-level? deep leaf?)
- How many times it's been called (1st call vs 20th call)
- What it needs to do (big-picture planning? focused execution?)

---

## Part 1: Context Configuration by Role

Different functions in the tree need different amounts of context.

### Orchestrator (top-level, e.g. `navigate`)

Sees all children's results. Makes high-level decisions.

```python
@agentic_function(compress=True)
def navigate(target):
    """Navigate to a target."""
    # Default summarize: sees everything
    # compress=True: after completion, others see only the final result
    ...
```

### Worker (mid-level, e.g. `observe`, `act`)

Needs parent's goal + recent siblings. Doesn't need ancient history.

```python
@agentic_function(summarize={"depth": 1, "siblings": 3})
def observe(task):
    """Look at the screen."""
    # depth=1: sees parent (what task I'm part of)
    # siblings=3: sees last 3 siblings (recent steps)
    ...
```

### Leaf (pure computation, e.g. `run_ocr`, `detect_all`)

Needs nothing from the tree. Just takes input, returns output.

```python
@agentic_function(summarize={"depth": 0, "siblings": 0})
def run_ocr(img):
    """Extract text from image."""
    # No context injection — pure function
    ...
```

### Recommended defaults

| Role | render | summarize | compress |
|------|--------|-----------|----------|
| Orchestrator | `"summary"` | `None` (see all) | `True` |
| Planner | `"summary"` | `{"depth": 1, "siblings": 5}` | `False` |
| Worker | `"summary"` | `{"depth": 1, "siblings": 3}` | `False` |
| Actor | `"result"` | `{"depth": 1, "siblings": 1}` | `False` |
| Leaf | `"result"` | `{"depth": 0, "siblings": 0}` | `False` |

---

## Part 2: Long-Running Sessions

In a long-running session (like a Claude Code conversation), the tree
keeps growing. After 50 steps, the tree has 50+ nodes.

### The approach: tree grows, summarize manages

The tree NEVER gets modified or pruned. It's an immutable record.

Context management happens entirely through `summarize`:

```python
# Step 50 of a long session
@agentic_function(summarize={"depth": 1, "siblings": 5})
def observe(task):
    # Only sees the last 5 siblings + parent
    # Steps 1-44 are still in the tree, just not sent to the LLM
    ...
```

### compress for natural boundaries

When a high-level task finishes, compress hides its internals:

```python
@agentic_function(compress=True)
def navigate(target):
    # 10 observe/act/verify steps inside
    # After completion: others see "navigate: {success: true}"
    # The 10 internal steps are hidden from summarize
    ...
```

This creates natural compression boundaries:

```
root
├── navigate("login") ← compressed: one line
├── navigate("settings") ← compressed: one line
└── navigate("wifi")
    ├── observe[0] → "I see settings"
    ├── act[0] → {click: wifi}
    └── observe[1] ← current call
```

observe[1] sees:
- `navigate("login")` — one line (compressed)
- `navigate("settings")` — one line (compressed)
- navigate("wifi") ancestors + siblings inside it

---

## Part 3: Prompt Cache Optimization

LLM APIs cache prompt prefixes. Cache hits are **10x cheaper** than base input.

The key insight: `summarize()` output grows **monotonically** (append-only).
Each call sees everything the previous call saw, plus the new sibling.
This means the prefix is naturally stable → cache hits are automatic.

### How it works

```
Call 1 context: [ancestors] [sibling_0]
Call 2 context: [ancestors] [sibling_0] [sibling_1]      ← prefix cached
Call 3 context: [ancestors] [sibling_0] [sibling_1] [sibling_2]  ← prefix cached
```

The `[ancestors] [sibling_0]` portion is identical in call 2 and call 3,
so the LLM API serves it from cache.

### What breaks the cache

Anything that changes the prefix:
- Re-rendering old siblings differently (→ render level is fixed per node)
- Reordering nodes (→ summarize always uses insertion order)
- Inserting content before existing siblings (→ summarize only appends)

### Cost comparison

20 calls, ~1000 tokens of context each:

| Strategy | Total input cost |
|----------|-----------------|
| Rebuild context each call | ~$1.05 |
| Stable prefix (default) | ~$0.22 |

**~5x cost reduction** from the default behavior. No configuration needed.

### compress and caching

compress helps caching too. When navigate(compress=True) finishes:
- Before: 10 lines of observe/act/verify in the context
- After: 1 line of navigate result

Subsequent calls start with a shorter, more stable prefix.

---

## Part 4: Complete Example

A GUI navigation agent with different context strategies per function:

```python
from agentic import agentic_function, runtime

# Orchestrator: sees everything, compresses its internals
@agentic_function(compress=True)
def navigate(target):
    """Navigate to a target UI element."""
    for step in range(20):
        obs = observe(f"find {target}")
        if obs.get("found"):
            act("click", obs["location"])
            if verify(target):
                return {"success": True, "steps": step}
    return {"success": False, "steps": 20}

# Worker: parent + last 3 siblings
@agentic_function(summarize={"depth": 1, "siblings": 3})
def observe(task):
    """Look at the screen and describe what you see."""
    img = take_screenshot()
    return runtime.exec(
        prompt=observe.__doc__,
        input={"task": task},
        images=[img],
        call=llm,
    )

# Actor: parent + last sibling only
@agentic_function(render="result", summarize={"depth": 1, "siblings": 1})
def act(action, location=None):
    """Execute a UI action."""
    perform_action(action, location)
    return {"action": action, "location": location}

# Leaf: no context needed
@agentic_function(render="result", summarize={"depth": 0, "siblings": 0})
def run_ocr(img):
    """Extract text from image."""
    return ocr_engine.run(img)
```

### What happens at step 15

```
Context tree:
root
└── navigate("wifi")
    ├── observe[0] → "home screen"        ← outside siblings=3 window
    ├── act[0] → {click, [100,200]}
    ├── ... (steps 1-11)
    ├── observe[12] → "settings page"     ← within siblings=3 window
    ├── act[12] → {scroll, down}
    ├── observe[13] → "wifi option"       ← within siblings=3 window
    ├── act[13] → {click, [347,291]}
    ├── observe[14] → "wifi settings"     ← within siblings=3 window
    └── observe[15] ← CURRENT CALL

observe[15] sees (summarize with depth=1, siblings=3):
    [Ancestor: navigate(target="wifi")]
    observe: "wifi option" 1200ms
    act: {"click", [347,291]} 820ms
    observe: "wifi settings" 900ms
```

Clean, focused context. Old history is still in the tree for debugging.
