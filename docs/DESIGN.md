# Agentic Programming — Design Specification

> A programming paradigm where LLM sessions are the compute units.

---

## 1. Core Insight

In programming, a CPU executes functions. In agentic programming, an LLM executes functions. Everything else follows from this analogy:

| Programming | Agentic Programming |
|-------------|---------------------|
| CPU | LLM (via Session) |
| Function body (code) | Function body (natural language instructions) |
| Type signature | return_type (Pydantic schema) |
| Function call | `result = fn(session, **args)` |
| Standard library | Built-in functions (ask, extract, classify, ...) |
| Class | Python class with LLM-backed methods |
| Control flow | Python (if/for/while/async) |

**There is no Runtime class.** The LLM *is* the runtime, accessed through Session.

---

## 2. Function

The fundamental unit. A Python function whose body is executed by an LLM.

### With decorator

```python
@function(return_type=ObserveResult)
def observe(session: Session, task: str) -> ObserveResult:
    """Observe the screen and identify all visible UI elements."""

result = observe(session, task="find login button")
```

The decorator:
1. Assembles a prompt from the docstring + arguments + return schema
2. Sends it to the Session
3. Parses and validates the JSON output
4. Retries if invalid (up to max_retries)
5. Returns a guaranteed Pydantic object

### Manual

```python
def observe(session: Session, task: str) -> ObserveResult:
    reply = session.send(f"Observe the screen. Task: {task}")
    return ObserveResult.model_validate_json(reply)
```

Same result, full control over the prompt.

### Built-in functions

```python
ask(session, question)              → str
extract(session, text, schema)      → Pydantic model
summarize(session, text)            → str
classify(session, text, categories) → str
decide(session, question, options)  → str
```

---

## 3. Session

The LLM interface. Like a CPU's instruction set.

```python
class Session(ABC):
    def send(self, message) -> str: ...
    def apply_scope(self, scope, context): ...
    def post_execution(self, scope): ...
    def reset(self): ...
```

### Implementations

| Session | Backend | Images | History | Auth |
|---------|---------|--------|---------|------|
| AnthropicSession | Anthropic API | ✅ base64 | In-memory | API key |
| OpenAISession | OpenAI API | ✅ base64 | In-memory | API key |
| ClaudeCodeSession | Claude Code CLI | ✅ stream-json | --session-id | Subscription |
| CodexSession | Codex CLI | ✅ --image | --session-id | Subscription |
| OpenClawSession | OpenClaw gateway | ✅ OpenAI format | Server-side | Gateway token |
| CLISession | Any CLI | ❌ | None | Depends |

### Two kinds of Sessions

**API Sessions** (AnthropicSession, OpenAISession):
- No built-in memory — we manage `_history`
- Can edit/compress history → supports all Scope parameters
- Each `send()` sends full history → KV cache on prefix match

**CLI Sessions** (ClaudeCodeSession, CodexSession):
- Built-in memory via `--session-id` + `--resume`
- Cannot edit history → ignores depth/detail/peer
- Reads `compact` → forks to new session

### Session as context

```python
# Shared session = shared context (like a conversation)
session = AnthropicSession()
r1 = observe(session, task="look")     # session remembers
r2 = act(session, target="click")      # sees r1's context

# Separate sessions = isolated
s1, s2 = AnthropicSession(), AnthropicSession()
r1 = observe(s1, task="look")          # s1 only
r2 = act(s2, target="click")           # s2 only, no r1 context
```

---

## 4. Scope

Intent declaration for context visibility. Sessions read what they understand.

```python
Scope(
    depth: Optional[int],      # Call stack layers (API Sessions)
    detail: Optional[str],     # "io" or "full" (API Sessions)
    peer: Optional[str],       # "none", "io", "full" (API Sessions)
    compact: Optional[bool],   # Compress after execution (CLI Sessions)
)
```

All parameters are Optional. None = "no opinion, use default."

### How Sessions handle Scope

| Parameter | API Session | CLI Session |
|-----------|-------------|-------------|
| depth | Injects call stack | Ignored (has memory) |
| detail | Controls how much to show | Ignored |
| peer | Injects peer summaries | Ignored |
| compact | Compresses history | Forks to new session |

This is polymorphism, not if/else. Each Session type overrides `apply_scope()` and `post_execution()`.

---

## 5. Memory

Persistent execution log. Like a program's debug log.

```
logs/run_20260401_130000_abc123/
├── run.jsonl          # One JSON event per line
├── run.md             # Human-readable summary
└── media/             # Screenshots and other files
    └── 001_screen.png
```

### Event types

| Type | Description |
|------|-------------|
| run_start | Task started |
| function_call | Function invoked (name, params, scope) |
| function_return | Function completed (result, timing, status) |
| message_sent | Message sent to Session |
| message_received | Reply from Session |
| decision | Programmer made a choice |
| error | Something went wrong |
| media | File saved |
| run_end | Task completed |

---

## 6. Programmer (Future)

The planning agent. Uses LLM to decide what functions to call.

```python
# Not yet implemented — design concept:
programmer = Programmer(session=planning_session)
programmer.register(observe, click, verify)
programmer.run("Open Safari and search for hello world")
```

The Programmer:
- Has a persistent Session (remembers across decisions)
- Sees available functions and their signatures
- Decides what to call next
- Can create new functions dynamically
- Only sees structured results from function calls

---

## 7. Design Principles

| Principle | Description |
|-----------|-------------|
| **Functions are functions** | Call them, get results. No Runtime needed. |
| **LLM is the runtime** | Session.send() is the "CPU instruction". |
| **Python is the control flow** | if/for/while/async — not a custom DSL. |
| **Scope is intent** | Declare what you want, Session handles how. |
| **Sessions are pluggable** | Same function works with any LLM backend. |
| **Built-in basics** | ask, extract, classify, etc. — ready to use. |
| **Memory is optional** | Log everything or nothing — your choice. |

---

## 8. Comparison

| | Tool-calling (Pydantic AI) | Agentic Programming |
|---|---|---|
| Direction | LLM calls Python functions | Python calls LLM functions |
| Who decides | LLM decides what tools to use | Program decides what to execute |
| Functions contain | Python code (for LLM to call) | Natural language (for LLM to execute) |
| Control flow | LLM-driven | Python-driven |
| Context management | Implicit (one conversation) | Explicit (Scope) |
| Session optimization | N/A | KV cache via Session reuse |
