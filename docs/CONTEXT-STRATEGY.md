# Context Strategy: Detailed Design

## Problem Statement

We have a complete Context tree. `summarize()` can query any slice.
The core question: **each function call should see WHAT context, and HOW MUCH?**

This isn't one-size-fits-all. It depends on:
- **Where** the function is in the tree (root? deep leaf?)
- **How many times** it's been called (1st observe vs 20th observe)
- **What it needs** (full picture? just its own branch? nothing?)
- **Cost** (every injected token costs money — wasted context = wasted money)

## Two Modes, Same Problem

Both API mode and Session mode face the same fundamental challenge:
**How to give the LLM enough context to be useful, without drowning it in irrelevant information.**

The difference is just mechanics:
- **API mode**: We build the ENTIRE message from scratch each call
- **Session mode**: Provider keeps a conversation, we add incremental messages

In both cases, WE control what gets injected. The Context tree is the source of truth.

---

## Part 1: API Mode — Context Injection Strategy

### 1.1 The Context Relevance Problem

Not all context is equally relevant. Consider this tree after 20 steps:

```
root
└── navigate("settings")                  ← orchestrator
    ├── observe("find menu")       [0]    ← old, probably irrelevant
    ├── act("click menu")          [1]
    ├── observe("find settings")   [2]
    ├── act("click settings")      [3]
    ├── observe("find wifi")       [4]
    ├── act("scroll down")         [5]
    ├── observe("find wifi")       [6]    ← retry, same task
    ├── act("scroll down")         [7]
    ├── observe("find wifi")       [8]    ← still looking...
    ├── act("click wifi")          [9]
    ├── observe("check wifi")      [10]
    ├── ...                               ← 10 more steps
    └── observe("verify done")     [20]   ← CURRENT CALL
```

If observe[20] gets ALL 20 siblings injected, that's:
- Mostly irrelevant (steps 0-8 are about finding wifi, we're past that)
- Expensive (20 × ~200 tokens = 4000 tokens of context)
- Diluting signal (the LLM pays attention to everything equally)

### 1.2 Context Injection Levels by Tree Position

Different positions in the tree need different strategies:

#### Level A: Orchestrator (root-level, e.g. `navigate`)
- **Sees**: Summaries of ALL children (result-level only)
- **Purpose**: Track overall progress, decide next step
- **Strategy**: `summarize(level="result", siblings=-1)`
- **Why**: Orchestrator needs the big picture, not OCR details

#### Level B: Mid-level function (e.g. `plan`, `decide_action`)
- **Sees**: Recent siblings (last 3-5), parent's goal
- **Purpose**: Make decisions based on recent observations
- **Strategy**: `summarize(depth=1, siblings=5, level="summary")`
- **Why**: Older siblings are less relevant; parent gives overall direction

#### Level C: Worker function (e.g. `observe`, `act`)
- **Sees**: Only its own inputs + maybe last sibling's result
- **Purpose**: Do one specific thing well
- **Strategy**: `summarize(depth=1, siblings=1)`
- **Why**: Workers don't need history — they need clear instructions

#### Level D: Leaf function (e.g. `run_ocr`, `detect_all`)
- **Sees**: Nothing from the tree
- **Purpose**: Pure computation, no reasoning needed
- **Strategy**: `summarize(depth=0, siblings=0)` or `context="none"`
- **Why**: Zero context overhead, maximum efficiency

### 1.3 Dynamic Context Window (Recency Bias)

For functions called repeatedly (like observe in a loop), context should **decay**:

```
Call #1:  Full context (ancestors + all siblings)
Call #5:  Last 3 siblings + ancestors
Call #10: Last 2 siblings + ancestors  
Call #20: Last 1 sibling + ancestors (most recent only)
```

Implementation via a **context policy** on the decorator:

```python
@agentic_function(context_policy="recency")
def observe(task):
    """..."""
    # summarize() automatically applies recency decay based on sibling count
```

Or explicitly:

```python
@agentic_function
def observe(task):
    """..."""
    ctx = get_context()
    n_siblings = len([c for c in ctx.parent.children if c is not ctx])
    
    # Decay: more siblings → fewer visible
    if n_siblings < 3:
        window = -1       # See all
    elif n_siblings < 10:
        window = 3        # Last 3
    else:
        window = 1        # Only the most recent
    
    return runtime.exec(
        prompt=observe.__doc__,
        input={"task": task},
        context=ctx.summarize(depth=1, siblings=window),
    )
```

### 1.4 Context Hit Rate — Cost Optimization

**Context hit rate** = fraction of injected context that the LLM actually uses in its response.

Low hit rate means you're paying for tokens the LLM ignores. Strategies:

#### 1.4.1 Selective injection by relevance

```python
# Instead of injecting ALL siblings, filter by task similarity
@agentic_function
def observe(task):
    ctx = get_context()
    
    # Only inject siblings whose task was similar to mine
    relevant_paths = []
    for c in ctx.parent.children:
        if c is ctx:
            break
        if c.name == "observe" and _tasks_related(c.params.get("task"), task):
            relevant_paths.append(c.path)
    
    return runtime.exec(
        prompt=observe.__doc__,
        input={"task": task},
        context=ctx.summarize(include=relevant_paths) if relevant_paths else None,
    )
```

#### 1.4.2 Progressive detail levels

Closer siblings get more detail, older ones get less:

```python
def smart_summarize(ctx):
    """Progressive detail: recent=detail, older=summary, oldest=result."""
    parts = []
    siblings = [c for c in ctx.parent.children if c is not ctx and c.status != "running"]
    
    n = len(siblings)
    for i, s in enumerate(siblings):
        age = n - i  # distance from current
        if age <= 2:
            parts.append(s._render("detail"))    # Last 2: full detail
        elif age <= 5:
            parts.append(s._render("summary"))   # 3-5 ago: one-line summary
        elif age <= 10:
            parts.append(s._render("result"))    # 6-10 ago: just the result
        # else: skip entirely (too old)
    
    return "\n".join(parts)
```

#### 1.4.3 Branch-aware injection

When an orchestrator calls different functions (observe, plan, act), each type of sibling has different relevance:

```python
@agentic_function
def plan(task):
    """Decide next action based on observations."""
    ctx = get_context()
    
    # Plan needs: recent observations (detail) + recent actions (result only)
    return runtime.exec(
        prompt=plan.__doc__,
        context=ctx.summarize(
            include=["*/observe_*"],   # All observations
            siblings=5,                # But only last 5
            branch=["observe"],        # With their children (OCR results etc.)
        ),
    )
```

### 1.5 Recommended Default Policies

| Function type | `expose` | `context` mode | `summarize()` default |
|---|---|---|---|
| Orchestrator | `"result"` | `"auto"` | `level="result", siblings=-1` |
| Planner | `"summary"` | `"inherit"` | `depth=1, siblings=5` |
| Observer | `"summary"` | `"inherit"` | `depth=1, siblings=3` + recency decay |
| Actor | `"result"` | `"inherit"` | `depth=1, siblings=1` |
| Leaf (OCR, detect) | `"result"` | `"inherit"` | `depth=0, siblings=0` |
| Background | any | `"new"` | independent tree |

---

## Part 2: Session Mode — Conversation Management

### 2.1 Session as Managed Conversation

A Session wraps a stateful LLM conversation. The provider keeps message history.
But we can't just blindly append messages — we need to manage:

- **What to inject** (incremental context from the tree)
- **When to compress** (conversation getting too long)
- **How to compress** (what to keep, what to summarize)
- **Recovery** (if session is lost, rebuild from Context tree)

### 2.2 Session Lifecycle

```
┌─────────────┐
│   Create     │ ← Empty session, optional system prompt
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌──────────────┐
│   Active     │◄───►│  Inject ctx  │ ← Incremental from Context tree
└──────┬──────┘     └──────────────┘
       │
       │ token_count > soft_limit
       ▼
┌─────────────┐
│  Compress    │ ← Summarize old messages, keep recent
└──────┬──────┘
       │
       │ token_count > hard_limit  OR  provider rejects
       ▼
┌─────────────┐
│  Checkpoint  │ ← Save summary to Context tree, create new Session
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ New Session  │ ← Seeded with checkpoint summary
└─────────────┘
```

### 2.3 Compression Strategies

#### 2.3.1 Auto-compression (soft limit)

When `token_count` exceeds a configurable threshold, compress automatically:

```python
@dataclass
class Session:
    messages: list[dict] = field(default_factory=list)
    model: str = "sonnet"
    token_count: int = 0
    
    # Compression config
    soft_limit: int = 50_000       # Trigger auto-compression
    hard_limit: int = 100_000      # Force checkpoint + new session
    keep_recent: int = 10          # Always keep last N messages
    compression_model: str = None  # Use different (cheaper) model for compression
    
    def maybe_compress(self):
        """Auto-compress if over soft limit. Called after each append."""
        if self.token_count <= self.soft_limit:
            return
        
        # Keep system prompt (index 0) + last N messages
        keep = self.messages[:1] + self.messages[-self.keep_recent:]
        old = self.messages[1:-self.keep_recent]
        
        if not old:
            return  # Nothing to compress
        
        # Summarize old messages
        summary = self._compress(old)
        
        # Replace: [system, old..., recent...] → [system, summary, recent...]
        self.messages = (
            self.messages[:1] +
            [{"role": "user", "content": f"[Previous context summary]\n{summary}"}] +
            [{"role": "assistant", "content": "Understood. I'll continue from here."}] +
            self.messages[-self.keep_recent:]
        )
        self._recount_tokens()
```

#### 2.3.2 Manual compression

User can trigger compression explicitly:

```python
session.compress(
    keep_recent=5,        # Keep last 5 turns
    level="summary",      # Compress at summary level (vs detail)
    preserve=["plan"],    # Always keep messages containing "plan"
)
```

#### 2.3.3 Checkpoint + restart (hard limit)

When compression isn't enough (or provider enforces a window):

```python
def checkpoint(self) -> str:
    """Save session state to a summary string, for seeding a new session."""
    # Ask the LLM to summarize its own conversation
    summary = runtime.exec(
        prompt="Summarize everything accomplished so far, "
               "including current state and next steps. Be thorough.",
        session=self,
    )
    return summary

def restart_from(self, checkpoint: str) -> "Session":
    """Create a new session seeded with a checkpoint summary."""
    new = Session(model=self.model)
    new.append("user", f"[Checkpoint from previous session]\n{checkpoint}")
    new.append("assistant", "Understood. Continuing from the checkpoint.")
    return new
```

### 2.4 Incremental Context Injection

The key problem in session mode: **what's new since the last LLM call?**

```python
@dataclass 
class Session:
    # ... other fields ...
    _seen_paths: set = field(default_factory=set)  # Context paths already injected
    
    def inject_incremental(self, ctx: Context) -> Optional[str]:
        """Build context string for only NEW information since last injection."""
        parts = []
        
        if ctx.parent:
            for c in ctx.parent.children:
                if c is ctx:
                    break
                if c.path in self._seen_paths:
                    continue  # Already in session history
                if c.status == "running":
                    continue
                    
                parts.append(c._render(c.expose))
                self._seen_paths.add(c.path)
        
        return "\n".join(parts) if parts else None
```

Usage in `runtime.exec()`:

```python
def exec(prompt, input=None, session=None, ...):
    ctx = _current_ctx.get(None)
    
    if session is not None:
        # Session mode: incremental injection
        new_context = session.inject_incremental(ctx)
        if new_context:
            session.append("user", f"[Update]\n{new_context}")
            session.append("assistant", "Noted.")
        
        # Add the actual prompt
        session.append("user", _format_prompt(prompt, input))
        reply = call(session.messages, model=session.model)
        session.append("assistant", reply)
        
        # Auto-compress if needed
        session.maybe_compress()
    else:
        # API mode: full context injection (using summarize policy)
        context = ctx.summarize() if ctx else None
        messages = _build_messages(prompt, input, context=context)
        reply = call(messages, model=model)
    
    return reply
```

### 2.5 Session-Context Tree Synchronization

The Session and Context tree record the same events from different perspectives:

```
Context tree (source of truth):        Session (conversation cache):
─────────────────────────────          ──────────────────────────────
root                                   [system] You are an agent...
├── observe[0] ✓ {found: menu}        [user] Look at the screen...
├── plan[0] ✓ {click menu}            [assistant] I see a menu...
├── act[0] ✓ {clicked}                [user] [Update] act: clicked
├── observe[1] ✓ {found: settings}    [user] Look at the screen...
│                                      [assistant] I see settings...
│                                      --- COMPRESSED ---
│                                      [user] [Summary] Found menu,
│                                             clicked it, now at settings
├── observe[2] ✓ {found: wifi}        [user] Look at the screen...
└── plan[1] → CURRENT                 [assistant] I see wifi option...
                                       [user] What should I do next?
```

When Session is lost (crash, provider error):
```python
def rebuild_session_from_context(root: Context, model: str) -> Session:
    """Reconstruct a session from the Context tree after a crash."""
    session = Session(model=model)
    summary = root.summarize(level="summary", siblings=-1)
    session.append("user", f"[Recovered context]\n{summary}")
    session.append("assistant", "Understood. Ready to continue.")
    return session
```

---

## Part 3: Context Policy — The Unifying Abstraction

Both modes need the same thing: a **policy** that decides what to inject.

### 3.1 ContextPolicy

```python
@dataclass
class ContextPolicy:
    """Controls what context gets injected into LLM calls."""
    
    # --- Visibility scope ---
    depth: int = -1              # Ancestor depth (-1=all)
    siblings: int = -1           # Sibling count (-1=all)
    level: str = "summary"       # Default render level
    
    # --- Recency decay ---
    decay: bool = False          # Enable recency decay
    decay_thresholds: list = field(default_factory=lambda: [
        (3, -1, "detail"),    # <3 siblings: all, detail level
        (10, 3, "summary"),   # <10 siblings: last 3, summary level
        (50, 1, "result"),    # <50 siblings: last 1, result only
    ])
    # Format: (max_siblings, window, level)
    # When n_siblings < threshold, use that window + level
    
    # --- Filtering ---
    include: Optional[list] = None
    exclude: Optional[list] = None
    branch: Optional[list] = None
    
    # --- Budget ---
    max_tokens: Optional[int] = None
    
    def apply(self, ctx: "Context") -> str:
        """Apply this policy to generate a context string."""
        if self.decay:
            n = len([c for c in (ctx.parent.children if ctx.parent else []) if c is not ctx])
            for threshold, window, level in self.decay_thresholds:
                if n < threshold:
                    return ctx.summarize(
                        depth=self.depth, siblings=window, level=level,
                        include=self.include, exclude=self.exclude,
                        branch=self.branch, max_tokens=self.max_tokens,
                    )
            # Over all thresholds: most aggressive
            _, window, level = self.decay_thresholds[-1]
            return ctx.summarize(
                depth=self.depth, siblings=window, level=level,
                max_tokens=self.max_tokens,
            )
        
        return ctx.summarize(
            depth=self.depth, siblings=self.siblings, level=self.level,
            include=self.include, exclude=self.exclude,
            branch=self.branch, max_tokens=self.max_tokens,
        )
```

### 3.2 Preset Policies

```python
# Presets for common patterns
ORCHESTRATOR = ContextPolicy(level="result", siblings=-1)
PLANNER = ContextPolicy(depth=1, siblings=5, level="summary")
WORKER = ContextPolicy(depth=1, decay=True)
LEAF = ContextPolicy(depth=0, siblings=0)

# Usage
@agentic_function(context_policy=WORKER)
def observe(task):
    """..."""
```

### 3.3 Integration with @agentic_function

```python
@agentic_function(
    expose="summary",           # How others see MY results
    context="inherit",          # How I attach to the tree
    context_policy=WORKER,      # How I see OTHERS' results
)
def observe(task):
    """..."""
    # runtime.exec() uses context_policy automatically
    # No need to manually call summarize()
```

---

## Part 4: Putting It All Together

### 4.1 Complete Example: GUI Navigation Task

```python
# Orchestrator: sees everything at result level
@agentic_function(expose="result", context_policy=ORCHESTRATOR)
def navigate(target):
    """Navigate to a target UI element."""
    session = Session(model="sonnet", soft_limit=30000)
    
    for step in range(20):
        obs = observe(f"find {target}")
        
        plan = runtime.exec(
            prompt="What should I do next?",
            input={"observation": obs, "step": step},
            session=session,  # Stateful: builds on previous reasoning
        )
        
        if plan["action"] == "done":
            return {"success": True, "steps": step}
        
        act(plan["action"], plan.get("location"))
    
    return {"success": False, "steps": 20}

# Worker: recency decay, sees recent siblings
@agentic_function(expose="summary", context="inherit", context_policy=WORKER)
def observe(task):
    """Look at the screen and describe what you see."""
    img = take_screenshot()
    return runtime.exec(
        prompt=observe.__doc__,
        input={"task": task},
        images=[img],
        # context auto-injected by WORKER policy with decay
    )

# Actor: minimal context, just needs instructions
@agentic_function(expose="result", context="inherit", context_policy=ContextPolicy(depth=1, siblings=1))
def act(action, location=None):
    """Execute a UI action."""
    if action == "click":
        click(location)
    elif action == "scroll":
        scroll(location)
    return {"action": action, "location": location}

# Leaf: zero context overhead
@agentic_function(expose="result", context="inherit", context_policy=LEAF)
def run_ocr(img):
    """Extract text from image."""
    return ocr_engine.run(img)
```

### 4.2 What happens at step 15:

```
Context tree at step 15:
root
└── navigate("wifi")
    ├── observe[0] → "I see home screen"        ← old, decayed out
    ├── act[0] → {click, [100,200]}              ← old, decayed out
    ├── ...                                       ← steps 1-12: decayed
    ├── observe[13] → "I see settings page"      ← recent: summary
    ├── act[13] → {scroll, down}                 ← recent: result
    ├── observe[14] → "I see wifi option"        ← most recent: detail
    └── observe[15] → CURRENT CALL

observe[15] sees (via WORKER policy with decay):
  [Ancestor: navigate(target="wifi")]
  observe: "I see wifi option" 1200ms              ← sibling[14], detail
  act: {"scroll", "down"}                          ← sibling[13], result

Session at step 15 sees:
  [system] You are an agent...
  [compressed summary] Found settings, scrolled 3 times, now see wifi
  [user] observe: "I see wifi option"              ← incremental
  [user] What should I do next?
  [assistant] Click the wifi option at [347, 291]
  [user] act: clicked [347, 291]                   ← incremental
  [user] observe: "wifi settings open"             ← incremental
  [user] What should I do next?                    ← CURRENT
```

---

---

## Part 5: Cache-Aware Context Design (Cost Optimization)

### 5.1 Prompt Caching Pricing Model

Using Claude Opus 4.6 as reference:

| Token Type | Price / MTok | Relative Cost |
|---|---|---|
| Base input | $5.00 | 1x |
| 5-min cache write | $6.25 | 1.25x (premium to establish cache) |
| 1-hour cache write | $10.00 | 2x (longer TTL, higher upfront) |
| **Cache hit** | **$0.50** | **0.1x (10x cheaper than base!)** |
| Output | $25.00 | 5x |

The key insight: **cache hits are 10x cheaper than base input.**
If we can structure our messages so the PREFIX stays stable across calls,
subsequent calls pay $0.50/MTok instead of $5.00/MTok for the cached portion.

### 5.2 How Prompt Caching Works

Anthropic's prompt caching is **prefix-matching**:

```
Call 1: [system][context_A][context_B][prompt_1]  ← all base price ($5/MTok)
Call 2: [system][context_A][context_B][prompt_2]  ← [system][A][B] = cache hit ($0.50)
                                      ^^^^^^^^      only this is base price
Call 3: [system][context_A][context_C][prompt_3]  ← [system][A] = cache hit
                           ^^^^^^^^^ ^^^^^^^^        these are base price
```

**Rule: the moment a token differs from the cached prefix, everything after is a cache miss.**

This means:
- ✅ Stable content at the FRONT → cached
- ✅ Dynamic content at the END → only the changing part pays full price
- ❌ Changing content in the middle → BREAKS the cache for everything after

### 5.3 Cache-Optimal Message Layout

Design principle: **sort by stability. Most stable first, most volatile last.**

```
┌─────────────────────────────────────┐
│  Layer 1: System prompt             │ ← Never changes. Always cached.
│  (role, instructions, tools)        │    Cost after 1st call: $0.50/MTok
├─────────────────────────────────────┤
│  Layer 2: Ancestor chain            │ ← Changes rarely (only when
│  (root → parent goals/params)       │    entering a new function).
│                                     │    Cached across sibling calls.
├─────────────────────────────────────┤
│  Layer 3: Historical siblings       │ ← Grows monotonically (append-only).
│  (older results, compressed)        │    Previously injected siblings
│                                     │    stay in same position → cached.
├─────────────────────────────────────┤
│  Layer 4: Recent siblings           │ ← The newest sibling is new content.
│  (last 1-3, detailed)              │    But older ones in this section
│                                     │    are cached from last call.
├─────────────────────────────────────┤
│  Layer 5: Current prompt + input    │ ← Always new. Always base price.
│  (task, images, schema)             │    This is the only part that
│                                     │    MUST be paid at full rate.
└─────────────────────────────────────┘

Cache boundary moves DOWN with each call:
  Call 1: Layers 1-2 cached from prior functions
  Call 2: Layers 1-3 cached (siblings from call 1 are now stable prefix)
  Call 3: Layers 1-3+part of 4 cached (sibling from call 2 joined the prefix)
```

### 5.4 Cache-Aware summarize() Implementation

The critical rule: **never reorder or rewrite historical siblings.**

If `observe[0]` was rendered as `observe: {"found": true} 1200ms` in call 1,
it MUST appear as exactly the same string in call 2, 3, ... N.
Any change (even whitespace) breaks the prefix cache.

```python
def summarize_cache_aware(self, ctx: Context) -> list[dict]:
    """Build messages optimized for prompt caching.
    
    Returns a list of message dicts (not a single string) so that
    stable sections can be grouped into cacheable blocks.
    """
    messages = []
    
    # --- Layer 2: Ancestors (stable within a function's execution) ---
    ancestor_parts = []
    node = ctx.parent
    while node and node.name:
        ancestor_parts.append(f"[Ancestor: {node.name}({_fmt_params(node.params)})]")
        node = node.parent
    if ancestor_parts:
        messages.append({
            "role": "user",
            "content": "\n".join(reversed(ancestor_parts)),
            # Anthropic: add cache_control to mark this as cacheable block
            "cache_control": {"type": "ephemeral"},
        })
        messages.append({"role": "assistant", "content": "Understood."})
    
    # --- Layer 3+4: Siblings (append-only, most stable first) ---
    sibling_text = []
    siblings = [c for c in ctx.parent.children if c is not ctx and c.status != "running"]
    
    for i, s in enumerate(siblings):
        # Key: render EXACTLY the same way every time
        # Do NOT change older siblings' rendering between calls
        sibling_text.append(s._render(s.expose))
    
    if sibling_text:
        messages.append({
            "role": "user",
            "content": "[Previous steps]\n" + "\n".join(sibling_text),
            # This block grows (appended to), but the prefix is stable
            "cache_control": {"type": "ephemeral"},
        })
        messages.append({"role": "assistant", "content": "Noted."})
    
    # --- Layer 5: Current prompt (always new, always full price) ---
    # This is added by runtime.exec(), not by summarize()
    
    return messages
```

### 5.5 Cost Analysis: With vs Without Cache Optimization

Scenario: `observe()` called 20 times in a navigate loop.
Each call injects ~1000 tokens of context.

#### Without cache awareness (rewritten context each call):
```
Call  1: 1000 tokens × $5.00/MTok = $0.005
Call  2: 2000 tokens × $5.00/MTok = $0.010  (all re-rendered)
Call  3: 3000 tokens × $5.00/MTok = $0.015
...
Call 20: 20000 tokens × $5.00/MTok = $0.100
Total: ~$1.05 for input tokens alone
```

#### With cache-aware layout (stable prefix cached):
```
Call  1: 1000 tokens × $5.00/MTok  = $0.005  (cache write)
Call  2: 1000 cached × $0.50/MTok  = $0.0005
       + 1000 new    × $5.00/MTok  = $0.005   (new sibling + prompt)
Call  3: 2000 cached × $0.50/MTok  = $0.001
       + 1000 new    × $5.00/MTok  = $0.005
...
Call 20: 19000 cached × $0.50/MTok = $0.0095
       +  1000 new    × $5.00/MTok = $0.005
Total: ~$0.22 for input tokens
```

**~5x cost reduction** just from ordering context correctly.
(Real savings depend on cache TTL, but the principle holds.)

### 5.6 Cache-Aware Compression Strategy

Compression is the enemy of caching. When you compress old messages,
you rewrite the prefix → all cached content is invalidated.

**Rule: delay compression as long as possible.**

```
Without compression:
  [system][sib_0][sib_1]...[sib_19][prompt]  ← sib_0..18 all cached
  Cost for sib_0..18: $0.50/MTok (cache hit)

With premature compression:
  [system][COMPRESSED_SUMMARY][sib_18][sib_19][prompt]  ← cache broken!
  Cost for everything after system: $5.00/MTok (cache miss)
```

Optimal compression timing:
1. **Never compress if within context window.** Cache savings > compression savings.
2. **Compress at natural boundaries** (function return, checkpoint) not mid-execution.
3. **When you must compress, compress EVERYTHING old at once.**
   Don't partially compress — it breaks the prefix without saving much.
4. **After compression, the new summary becomes the new stable prefix.**
   Subsequent calls cache against it.

```python
def should_compress(self, token_count: int, window_limit: int) -> bool:
    """Compression decision that accounts for caching economics."""
    headroom = window_limit - token_count
    
    if headroom > 10000:
        return False  # Plenty of room, caching is saving money
    
    if headroom < 2000:
        return True  # Must compress, about to hit the wall
    
    # In the middle: compress only if cache hit rate is low
    # (i.e., we're already paying base price anyway)
    cached_fraction = self._estimate_cached_fraction()
    return cached_fraction < 0.3  # Less than 30% cached → compress
```

### 5.7 Cache-Aware Context Policy

Extend ContextPolicy with cache hints:

```python
@dataclass
class ContextPolicy:
    # ... existing fields ...
    
    # Cache optimization
    cache_aware: bool = True              # Enable cache-optimal ordering
    stable_render: bool = True            # Never change how a sibling is rendered
    compress_threshold: float = 0.8       # Compress at 80% of context window
    cache_ttl: str = "5m"                 # "5m" or "1h" — affects write pricing
```

### 5.8 Impact on recency decay

Recency decay (showing fewer old siblings) has a cache trade-off:

```
# Scenario: decay drops sibling[3] from "detail" to "result" at call 10

Call 9:  [sib_3_detail][sib_4]...[sib_9][prompt]   ← sib_3 is in the prefix
Call 10: [sib_3_result][sib_4]...[sib_10][prompt]   ← sib_3 CHANGED → cache broken!
```

**Solution: render level is fixed at creation time, not at query time.**

When a sibling is first rendered, its expose level determines the rendering.
Subsequent calls always use that same rendering, even if the decay policy
would normally show less detail. This preserves the cache prefix.

```python
# In Context:
_cached_render: Optional[str] = None  # Frozen rendering for cache stability

def render_stable(self) -> str:
    """Render once, cache forever. For prompt cache optimization."""
    if self._cached_render is None:
        self._cached_render = self._render(self.expose)
    return self._cached_render
```

Decay is still useful: it controls **whether to include** the sibling at all.
But once included, its rendering never changes.

```
Call 9:  [sib_3_detail][sib_4]...[sib_9]   ← sib_3 rendered as detail
Call 10: [sib_3_detail][sib_4]...[sib_10]  ← same! cache preserved ✅
Call 15: [sib_4]...[sib_15]                 ← sib_3 dropped entirely (decay)
                                               prefix shortened but still stable ✅
```

### 5.9 Provider-Specific Cache Considerations

| Provider | Cache mechanism | Prefix? | TTL | Implications |
|---|---|---|---|---|
| Anthropic | Explicit `cache_control` | Yes | 5min / 1hr | Mark cacheable blocks explicitly |
| OpenAI | Automatic prefix caching | Yes | ~5-10min | Just keep prefix stable, it works |
| Google | Context caching API | Configurable | Custom | Separate API call to create cache |
| Local (vLLM) | Prefix caching | Yes | Session | KV cache reuse for shared prefixes |

Our framework should be cache-strategy-agnostic but expose hooks:

```python
runtime.exec(
    prompt=...,
    cache_policy="auto",  # "auto" | "aggressive" | "none"
    # auto: framework decides based on provider capabilities
    # aggressive: mark maximum cacheable, use 1hr TTL
    # none: no caching hints (for testing, or unsupported providers)
)
```

---

## Design Lessons (from building this system)

1. **v1 mistake: Session was the center of everything.** We had 6 Session implementations
   (Anthropic, OpenAI, Claude Code...) with 905+ lines. Session should be a thin cache,
   not the architecture's foundation. The Context tree is the real source of truth.

2. **"Just inject everything" doesn't scale.** In our GUI agent, after 20 steps the
   context was 8000+ tokens of mostly irrelevant history. The LLM started hallucinating
   about UI elements from step 3 that were no longer on screen.

3. **Recency decay emerged from practice.** We noticed that observe() at step 20
   doesn't need to know what happened at step 1. But it DOES need step 19's result.
   Fixed-window (siblings=3) was too rigid — decay thresholds solved it.

4. **Session compression is inevitable.** Every multi-step task eventually hits the
   context window. The question isn't IF you compress, but WHEN and HOW.
   Auto-compression at a soft limit prevents sudden failures at the hard limit.

5. **Context hit rate matters more than context size.** 500 tokens of highly relevant
   context beats 5000 tokens of mostly-irrelevant history. The ContextPolicy abstraction
   exists specifically to optimize this ratio.

6. **The two-layer pattern (Session + API) isn't a choice — it's both.**
   Orchestrators need session continuity. Workers need focused, minimal context.
   Trying to use one mode for everything is the real anti-pattern.
