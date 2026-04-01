# Agentic Programming — Design Specification

> A programming paradigm where LLM sessions are the compute units.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     User / Application                       │
│                                                              │
│   result = observe(session, task="find login")               │
│   if result.target_visible:                                  │
│       click(session, target="login button")                  │
│                                                              │
│   — or —                                                     │
│                                                              │
│   programmer.run("open Safari and search hello world")       │
└──────────────────┬───────────────────────┬───────────────────┘
                   │                       │
            Static Mode              Dynamic Mode
         (human writes flow)     (Programmer decides flow)
                   │                       │
                   ▼                       ▼
┌─────────────────────────────────────────────────────────────┐
│                      Function Layer                          │
│                                                              │
│   @function(return_type=ObserveResult)                        │
│   def observe(session, task: str):                           │
│       '''Docstring = prompt. Change it, change behavior.'''  │
│                                                              │
│   Built-ins: ask, extract, summarize, classify, decide       │
│                                                              │
│   Responsibilities:                                          │
│   • Assemble prompt from docstring + args + schema           │
│   • Send to Session                                          │
│   • Validate output against return_type                      │
│   • Retry on invalid output                                  │
│   • Return guaranteed Pydantic object                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      Session Layer                           │
│                                                              │
│   session.send(message) → reply                              │
│                                                              │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│   │ API Sessions │  │ CLI Sessions │  │   Gateway    │      │
│   │              │  │              │  │              │      │
│   │ Anthropic    │  │ Claude Code  │  │ OpenClaw     │      │
│   │ OpenAI       │  │ Codex        │  │              │      │
│   │              │  │              │  │              │      │
│   │ We manage    │  │ Built-in     │  │ Server-side  │      │
│   │ _history     │  │ memory via   │  │ memory       │      │
│   │              │  │ --session-id │  │              │      │
│   └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                              │
│   Responsibilities:                                          │
│   • Send message to LLM, receive reply                       │
│   • Maintain conversation history                            │
│   • Handle multimodal input (text + images)                  │
│   • Apply Scope (context injection / compaction)             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                        LLM                                   │
│                   (the actual runtime)                        │
│                                                              │
│   Claude, GPT, Gemini, local models, ...                     │
│   Executes the natural language instructions                 │
│   Returns structured JSON output                             │
└─────────────────────────────────────────────────────────────┘

Cross-cutting concerns (optional, any layer can use):

┌─────────────────────────┐  ┌─────────────────────────┐
│        Scope             │  │        Memory            │
│                          │  │                          │
│ Context visibility       │  │ Execution log            │
│ rules. Sessions read     │  │ (JSONL + Markdown +      │
│ what they understand.    │  │  media files)            │
└─────────────────────────┘  └─────────────────────────┘
```

---

## 2. Core Analogy

```
  Traditional Programming          Agentic Programming
  ────────────────────────         ────────────────────────
  CPU executes code          →     LLM executes instructions
  Function body = code       →     Function body = docstring
  Type signature             →     return_type (Pydantic)
  result = fn(args)          →     result = fn(session, args)
  Standard library           →     Built-in functions
  Class with methods         →     Python class with LLM methods
  if / for / while           →     Same (Python is control flow)
  Runtime / interpreter      →     Session (LLM interface)
  Debug log                  →     Memory
```

**There is no Runtime class.** The LLM *is* the runtime, accessed through Session.

---

## 3. Function

### What is a Function?

A Python function whose logic is described in natural language (the docstring)
and executed by an LLM (via a Session).

```
┌──────────────────────────────────────────────┐
│              @function decorator              │
│                                              │
│  def observe(session, task: str):            │
│      '''Look at the screen.                  │  ← Docstring = LLM instructions
│      Find all buttons and text fields.       │    (change this = change behavior)
│      Report what's visible.'''               │
│                                              │
│          │                                   │
│          ▼                                   │
│  ┌─────────────────────┐                     │
│  │  Assemble prompt    │                     │
│  │  from:              │                     │
│  │  • docstring        │                     │
│  │  • arguments        │                     │
│  │  • return schema    │                     │
│  │  • examples (opt)   │                     │
│  └────────┬────────────┘                     │
│           ▼                                  │
│  ┌─────────────────────┐                     │
│  │  session.send(      │                     │
│  │    prompt)          │  → LLM processes    │
│  │                     │  ← JSON reply       │
│  └────────┬────────────┘                     │
│           ▼                                  │
│  ┌─────────────────────┐                     │
│  │  Parse JSON         │                     │
│  │  Validate against   │                     │
│  │  return_type        │                     │
│  │  (Pydantic model)   │                     │
│  └────────┬────────────┘                     │
│           │                                  │
│     Valid? ─── No ──→ Retry (up to N times)  │
│           │                                  │
│          Yes                                 │
│           │                                  │
│           ▼                                  │
│  Return Pydantic object (guaranteed type)    │
└──────────────────────────────────────────────┘
```

### Two ways to define

**With decorator** (recommended — docstring = prompt):

```python
@function(return_type=ObserveResult)
def observe(session: Session, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements.
    Check if the target described in 'task' is visible.
    List every element you can see."""

result = observe(session, task="find the login button")
```

**Manual** (full control):

```python
def observe(session: Session, task: str) -> ObserveResult:
    reply = session.send(f"Observe the screen. Task: {task}")
    return ObserveResult.model_validate_json(reply)
```

### Built-in functions

Ready-to-use functions for common operations:

```
ask(session, question)              → str           Plain text Q&A
extract(session, text, schema)      → Pydantic      Structured extraction
summarize(session, text)            → str           Text summarization
classify(session, text, categories) → str           Classification
decide(session, question, options)  → str           Decision making
```

---

## 4. Session

### What is a Session?

The interface to the LLM. Like a CPU's instruction set — you send an instruction,
get back a result. Sessions also manage conversation history for context reuse.

```
┌──────────────────────────────────────────────┐
│                 Session                       │
│                                              │
│  send(message) → str     Core: send & reply  │
│  apply_scope(scope, ctx) Handle Scope        │
│  post_execution(scope)   Post-processing     │
│  reset()                 Clear history        │
│  has_memory → bool       Built-in memory?     │
│  history_length → int    Turn count           │
└──────────────────────────────────────────────┘
```

### Implementations

```
┌──────────────────────────────────────────────────────────────────┐
│                        Session Types                             │
│                                                                  │
│  API Sessions (we control history)                               │
│  ┌────────────────────┐  ┌────────────────────┐                  │
│  │ AnthropicSession   │  │ OpenAISession      │                  │
│  │ • API key auth     │  │ • API key auth     │                  │
│  │ • Text + images    │  │ • Text + images    │                  │
│  │ • _history in RAM  │  │ • _history in RAM  │                  │
│  │ • Can compact ✅   │  │ • Can compact ✅   │                  │
│  └────────────────────┘  └────────────────────┘                  │
│                                                                  │
│  CLI Sessions (built-in memory)                                  │
│  ┌────────────────────┐  ┌────────────────────┐                  │
│  │ ClaudeCodeSession  │  │ CodexSession       │                  │
│  │ • Subscription     │  │ • Subscription     │                  │
│  │ • Text + images    │  │ • Text + images    │                  │
│  │ • --session-id     │  │ • --session-id     │                  │
│  │ • Can't edit ❌    │  │ • Can't edit ❌    │                  │
│  │ • Compact = fork   │  │ • Compact = fork   │                  │
│  └────────────────────┘  └────────────────────┘                  │
│                                                                  │
│  Gateway Session                                                 │
│  ┌────────────────────┐  ┌────────────────────┐                  │
│  │ OpenClawSession    │  │ CLISession         │                  │
│  │ • /v1/chat/compl.  │  │ • Any CLI command  │                  │
│  │ • Gateway token    │  │ • Stateless        │                  │
│  │ • Server memory    │  │ • Text only        │                  │
│  └────────────────────┘  └────────────────────┘                  │
└──────────────────────────────────────────────────────────────────┘
```

### Context sharing via Session reuse

```
Shared Session (context flows between functions):

  session = AnthropicSession()
  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  │  observe()  │ →  │   learn()   │ →  │    act()    │
  │             │    │             │    │             │
  │  Session    │    │  Session    │    │  Session    │
  │  has 0 turns│    │  has 1 turn │    │  has 2 turns│
  │  (fresh)    │    │  (sees r1)  │    │  (sees r1+r2)
  └─────────────┘    └─────────────┘    └─────────────┘
       All three share the same Session object.
       Each call sees all prior conversation.
       KV cache prefix preserved → cheaper inference.

Separate Sessions (isolated):

  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  │  observe()  │    │   learn()   │    │    act()    │
  │             │    │             │    │             │
  │  Session A  │    │  Session B  │    │  Session C  │
  │  (fresh)    │    │  (fresh)    │    │  (fresh)    │
  └─────────────┘    └─────────────┘    └─────────────┘
       Each function has its own Session.
       No shared context. Clean slate each time.
```

---

## 5. Scope

### What is Scope?

An intent declaration for context visibility. Attached to a function,
read by the Session. Each Session type handles only the parameters it
understands — no if/else in framework code.

```
                    Scope
                 ┌─────────────────────────────────────┐
                 │  depth:   Optional[int]              │
                 │  detail:  Optional[str]   "io"|"full"│
  API Sessions   │  peer:    Optional[str]   "none"|    │
  read these  ←──│                           "io"|"full"│
                 │─────────────────────────────────────│
                 │  compact: Optional[bool]             │
  CLI Sessions   │                                      │
  read this   ←──│                                      │
                 └─────────────────────────────────────┘

  All parameters Optional. None = "no opinion, use default."
```

### How each Session type handles Scope

```
  API Session + Scope(peer="io"):
  ┌──────────────────────────────────────┐
  │  apply_scope():                      │
  │    Inject prior results into         │
  │    _history as summary messages      │
  │                                      │
  │  post_execution(compact=True):       │
  │    Replace last exchange in          │
  │    _history with a summary           │
  └──────────────────────────────────────┘

  CLI Session + Scope(compact=True):
  ┌──────────────────────────────────────┐
  │  apply_scope():                      │
  │    No-op (has built-in memory)       │
  │                                      │
  │  post_execution(compact=True):       │
  │    Fork to new --session-id          │
  │    (old session abandoned)           │
  └──────────────────────────────────────┘
```

### Presets

```python
Scope.isolated()   # depth=0, peer="none"   Pure function, no context
Scope.chained()    # depth=0, peer="io"     Sees sibling I/O summaries
Scope.aware()      # depth=1, peer="io"     Sees caller + siblings
Scope.full()       # depth=-1, peer="full"  Sees everything (shared session)
```

---

## 6. Memory

### What is Memory?

A persistent execution log. Like a program's debug log, but structured.
Records everything that happened during a run.

```
  memory = Memory(base_dir="./logs")
  memory.start_run(task="Click login button")

  ┌─────────────────────────────────────────────────────────────┐
  │                        Run Timeline                         │
  │                                                             │
  │  [run_start] task="Click login button"                      │
  │    │                                                        │
  │    ├── [function_call] observe(task="find button")           │
  │    │     ├── [message_sent] "## observe ..."                │
  │    │     ├── [message_received] '{"elements": [...]}'       │
  │    │     ├── [media] screenshot.png saved                   │
  │    │     └── [function_return] ✓ 150ms                      │
  │    │                                                        │
  │    ├── [decision] action=call, target=click                 │
  │    │                                                        │
  │    ├── [function_call] click(target="login")                │
  │    │     ├── [function_return] ✓ 200ms                      │
  │    │                                                        │
  │    ├── [function_call] verify(expected="login page")        │
  │    │     ├── [error] "element not found"                    │
  │    │     └── [function_return] ✗ 50ms                       │
  │    │                                                        │
  │  [run_end] status=partial, duration=400ms                   │
  └─────────────────────────────────────────────────────────────┘

  Output:
  logs/run_20260401_130000_abc123/
  ├── run.jsonl      Machine-readable (one JSON event per line)
  ├── run.md         Human-readable (Markdown with ✓/✗, timing, links)
  └── media/
      └── 001_screenshot.png
```

---

## 7. Programmer

### What is the Programmer?

An LLM that decides what functions to call and in what order.
It sees function signatures (docstrings), calls them, sees results,
and decides the next step.

```
  ┌─────────────────────────────────────────────────────────────┐
  │                      Programmer                             │
  │                                                             │
  │  Has: persistent Session (remembers across decisions)       │
  │  Sees: function list with docstrings                        │
  │  Does: decides what to call, with what arguments            │
  │                                                             │
  │  ┌─────────────────────────────────────────────────────┐    │
  │  │ Programmer Session (planning)                       │    │
  │  │                                                     │    │
  │  │ "Task: open Safari and search hello world"          │    │
  │  │ "Available: observe, click, type, verify"           │    │
  │  │                                                     │    │
  │  │  Decision: call observe(task="find Safari")         │    │
  │  │  Result: {app_visible: true, location: [100, 50]}   │    │
  │  │                                                     │    │
  │  │  Decision: call click(target="Safari icon")         │    │
  │  │  Result: {success: true}                            │    │
  │  │                                                     │    │
  │  │  Decision: call type(text="hello world")            │    │
  │  │  Result: {typed: true}                              │    │
  │  │                                                     │    │
  │  │  Decision: done                                     │    │
  │  └─────────────────────────────────────────────────────┘    │
  │         │              ▲                                    │
  │    call │              │ structured result only             │
  │         ▼              │ (never sees execution details)     │
  │  ┌──────────────┐      │                                    │
  │  │ observe()    │──────┘                                    │
  │  │ (own Session)│    Each function runs in its own Session  │
  │  └──────────────┘    Programmer only sees the return value  │
  └─────────────────────────────────────────────────────────────┘
```

**Key design:**
- Programmer Session accumulates **decisions and result summaries** (grows slowly)
- Function Sessions accumulate **execution details** (isolated, then destroyed)
- Programmer never sees function execution details → context stays small

**Programmer vs MCP / tool-calling:**
Both let an LLM decide what to call. The difference: MCP tools contain Python
code executed by a CPU. Our functions contain natural language executed by an LLM.
A Programmer could be implemented on top of MCP by registering functions as tools.

**Status:** Design finalized. Implementation deferred — the Function layer
comes first, Programmer builds on top of it.

---

## 8. Execution Modes

```
  Mode 1: Static (human writes the flow)
  ─────────────────────────────────────────

  session = ClaudeCodeSession()

  screen = observe(session, task="find login")     # call 1
  if screen.target_visible:                         # Python logic
      click(session, target="login button")         # call 2
      verify(session, expected="login page")        # call 3

  → Human controls the flow. LLM executes each step.
  → Good for: known workflows, scripts, automation.


  Mode 2: Dynamic (Programmer decides the flow)
  ─────────────────────────────────────────

  programmer = Programmer(session=AnthropicSession())
  programmer.register(observe, click, type, verify)
  programmer.run("open Safari and search hello world")

  → LLM controls the flow. LLM executes each step.
  → Good for: open-ended tasks, exploration, complex goals.


  Mode 3: Hybrid (human defines structure, LLM fills gaps)
  ─────────────────────────────────────────

  class GUIAgent:
      def __init__(self, session):
          self.session = session

      def find_and_click(self, target: str):
          screen = observe(self.session, task=f"find {target}")
          if screen.target_visible:
              return click(self.session, target=target)
          else:
              return decide(self.session,
                  f"Can't find {target}, what should I try?",
                  ["scroll down", "go back", "try different name"])

  → Human defines the pattern. LLM handles decisions within it.
  → Good for: robust automation with fallback logic.
```

---

## 9. Design Principles

| Principle | Description |
|-----------|-------------|
| **Functions are functions** | Call them, get results. No Runtime class. |
| **Docstring = prompt** | Change the docstring, change the behavior. |
| **LLM is the runtime** | Session.send() is the "CPU instruction". |
| **Python is the control flow** | if/for/while/async — not a custom DSL. |
| **Scope is intent** | Declare what you want, Session handles how. |
| **Sessions are pluggable** | Same function works with any LLM backend. |
| **Memory is optional** | Log everything or nothing — your choice. |
| **Programmer is deferred** | Function layer first, planning layer later. |

---

## 10. Comparison with Other Approaches

```
  Tool-calling (Pydantic AI, MCP)         Agentic Programming (ours)
  ───────────────────────────────         ──────────────────────────

  LLM decides what to call                Python code decides what to call
       │                                       │
       ▼                                       ▼
  Python function (CPU executes)          LLM function (LLM executes)
       │                                       │
       ▼                                       ▼
  Result back to LLM                      Result back to Python
       │                                       │
       ▼                                       ▼
  LLM decides next step                  Python decides next step
                                          (or Programmer LLM decides)

  Direction: LLM → Python → LLM          Direction: Python → LLM → Python
  Functions: contain code                 Functions: contain instructions
  Good for: data retrieval, APIs          Good for: reasoning, perception, analysis
```

---

## 11. Project Structure

```
harness/
├── __init__.py      Exports: function, ask, extract, classify, ...
├── function/        @function decorator + built-in functions
├── session/         Session interface + 6 implementations
├── scope/           Scope: context visibility rules
└── memory/          Memory: persistent execution log

tests/               53 tests covering all components
docs/
└── DESIGN.md        This file
```
