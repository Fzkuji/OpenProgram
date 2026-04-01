# Agentic Programming — Design Specification

> A programming paradigm where LLM and Python co-execute functions.

---

## 1. Core Concepts

The entire framework has only two concepts:

| Concept | Definition |
|---------|-----------|
| **Agentic Function** | A function executed by Python Runtime + Agentic Runtime together. Docstring = prompt. |
| **Meta Agentic Function** | An Agentic Function that creates other Agentic Functions. The bootstrap point of the system. |

Everything else is infrastructure:

| Infrastructure | Purpose |
|----------------|---------|
| **Agentic Session** | Interface to the Agentic Runtime (LLM). Manages history, context, images. |
| **Agentic Scope** | Controls what a Session can see (context visibility). |
| **Agentic Memory** | Persistent execution log (calls, results, media). |
| **Agentic Type** | Pydantic model that guarantees output format. |
| **MCP Server** | Single entry point — all functions registered as MCP tools. |

---

## 2. Architecture Overview

```mermaid
graph TB
    subgraph Caller["Human or LLM Agent"]
        Human["Human (writes Python)"]
        Agent["LLM Agent (Claude Code, Codex, OpenClaw)"]
    end

    subgraph MCP["MCP Server"]
        Meta["Meta Agentic Function<br/>(creates other functions)"]
        FnA["Agentic Function A"]
        FnB["Agentic Function B"]
        FnC["Agentic Function C"]
        Meta -.->|creates| FnA
        Meta -.->|creates| FnB
        Meta -.->|creates| FnC
    end

    subgraph Execution["Dual Runtime Execution"]
        PyRT["Python Runtime<br/>(OCR, click, file I/O)"]
        AgRT["Agentic Runtime<br/>(LLM reasoning)"]
    end

    subgraph Sessions["Agentic Session Layer"]
        API["API Sessions<br/>Anthropic, OpenAI"]
        CLI["CLI Sessions<br/>Claude Code, Codex"]
        GW["Gateway<br/>OpenClaw"]
    end

    LLM["LLM<br/>Claude / GPT / Gemini"]

    Human -->|"Python call"| MCP
    Agent -->|"MCP JSON-RPC"| MCP
    MCP --> Execution
    AgRT --> Sessions
    API --> LLM
    CLI --> LLM
    GW --> LLM

    Scope["Scope<br/>(context visibility)"]
    Memory["Memory<br/>(execution log)"]
    Scope -.-> Sessions
    Memory -.-> MCP
```

---

## 3. Agentic Function

### What is it?

A Python function whose logic is split between two Runtimes:
- **Python Runtime**: deterministic code (screenshots, OCR, detection, clicking)
- **Agentic Runtime**: LLM reasoning (understanding, finding targets, deciding)

The **docstring IS the prompt**. Change the docstring → change the behavior.

### Execution Flow

```mermaid
flowchart TD
    A["Call: observe(session, task='find login')"] --> B["Assemble prompt:<br/>• docstring (instructions)<br/>• arguments (input)<br/>• return schema (output format)"]
    B --> C["session.send(prompt)"]
    C --> D["LLM processes"]
    D --> E["Parse JSON reply"]
    E --> F{Valid against<br/>return_type?}
    F -- Yes --> G["Return Pydantic object ✓"]
    F -- No --> H{Retries left?}
    H -- Yes --> I["Send error + schema<br/>as retry prompt"]
    I --> C
    H -- No --> J["Raise FunctionError ✗"]
```

### Dual Runtime Cooperation

```python
def observe(programmer, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements."""

    # ── Python Runtime (deterministic) ──
    screenshot = take_screenshot()        # Python: capture screen
    ocr_data = run_ocr(screenshot.path)   # Python: extract text
    elements = detect_all(screenshot.path) # Python: detect UI elements

    # ── Agentic Runtime (reasoning) ──
    worker = create_session(model="sonnet")
    reply = worker.send({
        "text": f"Analyze this screen. Task: {task}\nOCR: {ocr_data}\nElements: {elements}",
        "images": [screenshot.path]
    })

    # ── Python Runtime (parse + validate) ──
    result = ObserveResult.parse(reply)
    return result
```

### Two Ways to Define

**With decorator** (recommended):

```python
@function(return_type=ObserveResult)
def observe(session: Session, task: str) -> ObserveResult:
    """Look at the screen and find all visible UI elements.
    Check if the target described in 'task' is visible."""
```

**Manual** (full control over both Runtimes):

```python
def observe(session: Session, task: str) -> ObserveResult:
    screenshot = take_screenshot()  # Python Runtime
    reply = session.send(...)       # Agentic Runtime
    return ObserveResult.parse(reply)
```

### Built-in Functions

| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `ask` | question | str | Plain text Q&A |
| `extract` | text, schema | Pydantic model | Structured data extraction |
| `summarize` | text | str | Text summarization |
| `classify` | text, categories | str | Classification |
| `decide` | question, options | str | Decision making |

---

## 4. Meta Agentic Function

### What is it?

The only "hardcoded" function in the system. It creates all other Agentic Functions and registers them as MCP tools. Both humans and LLMs call it the same way.

### Why it matters

- **Self-evolving**: LLM encounters a new task → calls Meta → new function exists → reuses it
- **Unified bootstrap**: the entire system grows from this one function
- **No Programmer role needed**: any LLM agent + MCP + Meta = complete system

### How it works

```mermaid
sequenceDiagram
    participant Caller as Human or LLM
    participant Meta as Meta Agentic Function
    participant MCP as MCP Server
    participant New as New Agentic Function

    Caller->>Meta: create(name, docstring, params, returns)
    Meta->>Meta: Generate Python code
    Meta->>Meta: Write to file
    Meta->>MCP: Register as MCP tool
    Meta-->>Caller: "Function {name} created ✓"

    Note over Caller,New: Now the new function is callable:
    Caller->>New: new_function(args...)
    New-->>Caller: result
```

### Bootstrap Sequence

```
System start:
  1. MCP Server starts with only Meta Agentic Function
  2. Human or LLM calls Meta to create domain functions
  3. Domain functions become available as MCP tools
  4. Human or LLM calls domain functions to do work

Self-evolving:
  1. LLM encounters unknown task
  2. LLM calls Meta to create a new function for it
  3. LLM calls the new function
  4. Next time, function already exists — no Meta call needed
```

---

## 5. Agentic Session

### What is it?

The interface to the Agentic Runtime (LLM). You send a message, get a reply. Sessions manage conversation history for context reuse.

```mermaid
classDiagram
    class Session {
        <<abstract>>
        +send(message) str
        +apply_scope(scope, context)
        +post_execution(scope)
        +reset()
        +has_memory bool
    }

    class AnthropicSession {
        _history: list
        +send() → Anthropic API
    }

    class OpenAISession {
        _history: list
        +send() → OpenAI API
    }

    class ClaudeCodeSession {
        _session_id: str
        +send() → Claude Code CLI
    }

    class CodexSession {
        _session_id: str
        +send() → Codex CLI
    }

    class OpenClawSession {
        _session_key: str
        +send() → OpenClaw gateway
    }

    class CLISession {
        _command: str
        +send() → any CLI
    }

    Session <|-- AnthropicSession
    Session <|-- OpenAISession
    Session <|-- ClaudeCodeSession
    Session <|-- CodexSession
    Session <|-- OpenClawSession
    Session <|-- CLISession
```

### Session Types

| Session | Backend | Images | History managed by | Auth |
|---------|---------|--------|--------------------|------|
| AnthropicSession | Anthropic API | ✅ base64 | Us (`_history`) | API key |
| OpenAISession | OpenAI API | ✅ base64 | Us (`_history`) | API key |
| ClaudeCodeSession | Claude Code CLI | ✅ stream-json | CLI (`--session-id`) | Subscription |
| CodexSession | Codex CLI | ✅ `--image` | CLI (`--session-id`) | Subscription |
| OpenClawSession | OpenClaw gateway | ✅ OpenAI format | Server-side | Gateway token |
| CLISession | Any CLI command | ❌ | None (stateless) | Depends |

### Two-Layer Session Design

```mermaid
graph TB
    subgraph Caller["Caller's Session (summaries only)"]
        S1["observe → {app: Discord, found: true}"]
        S2["act → {clicked: login, success: true}"]
        S3["verify → {verified: true}"]
    end

    subgraph Workers["Worker Sessions (full data, destroyed)"]
        W1["Worker A: 77 OCR texts, 106 elements, screenshot"]
        W2["Worker B: template match, coordinate calc, click"]
        W3["Worker C: screenshot + OCR, judgment"]
    end

    Caller --> W1
    Caller --> W2
    Caller --> W3
```

- **Caller's Session** grows slowly (only result summaries)
- **Worker Sessions** have full data but are destroyed after each function call
- Like Python's local variables: function returns → locals gone, only return value survives

---

## 6. Agentic Scope

### What is it?

An intent declaration for context visibility. Attached to a function, read by the Session. Each Session type handles only the parameters it understands.

### Parameters

| Parameter | Type | Read by | Description |
|-----------|------|---------|-------------|
| `depth` | Optional[int] | API Sessions | Call stack layers visible (0=none, -1=all) |
| `detail` | Optional[str] | API Sessions | "io" (summary) or "full" (reasoning) |
| `peer` | Optional[str] | API Sessions | Sibling visibility: "none", "io", "full" |
| `compact` | Optional[bool] | CLI Sessions | Compress after execution |

All parameters are **Optional**. `None` = "no opinion, use default."

### How Sessions Handle Scope

```mermaid
flowchart LR
    S["Scope<br/>(depth, detail, peer, compact)"]
    S --> API["API Session<br/>• Reads depth/detail/peer<br/>• Injects context into _history<br/>• compact → compress history"]
    S --> CLI_S["CLI Session<br/>• Ignores depth/detail/peer<br/>  (has built-in memory)<br/>• compact → fork to new session"]
```

### Presets

| Preset | depth | detail | peer | Use case |
|--------|-------|--------|------|----------|
| `Scope.isolated()` | 0 | "io" | "none" | Pure function, no context |
| `Scope.chained()` | 0 | "io" | "io" | Sees sibling I/O summaries |
| `Scope.aware()` | 1 | "io" | "io" | Sees caller + siblings |
| `Scope.full()` | -1 | "full" | "full" | Sees everything |

---

## 7. Agentic Memory

### What is it?

A persistent execution log. Records every function call, result, decision, and media file during a run.

### Event Flow

```mermaid
flowchart TD
    RS["run_start"] --> FC1["function_call: observe"]
    FC1 --> MS1["message_sent"]
    MS1 --> MR1["message_received"]
    MR1 --> MD1["media: screenshot.png"]
    MD1 --> FR1["function_return ✓ 150ms"]
    FR1 --> FC2["function_call: act"]
    FC2 --> FR2["function_return ✓ 200ms"]
    FR2 --> FC3["function_call: verify"]
    FC3 --> FR3["function_return ✓ 50ms"]
    FR3 --> RE["run_end: success"]
```

### Output Format

```
logs/run_<timestamp>/
├── run.jsonl      ← Machine-readable (one JSON event per line)
├── run.md         ← Human-readable (Markdown with ✓/✗, timing, media links)
└── media/
    └── 001_screenshot.png
```

---

## 8. MCP Integration

### Why MCP?

MCP is the **transport protocol** (how to call). Agentic Programming is the **execution model** (how functions run). They are orthogonal — our functions are exposed via MCP.

### How it works

```mermaid
sequenceDiagram
    participant Agent as LLM Agent
    participant Platform as Claude Code / OpenClaw
    participant MCP as Our MCP Server
    participant Fn as Agentic Function

    Note over Agent,Platform: Startup: platform asks MCP for tool list
    Platform->>MCP: tools/list
    MCP-->>Platform: [{name: "observe", ...}, {name: "act", ...}, ...]
    Platform->>Agent: Inject tools into system prompt

    Note over Agent,Fn: Runtime: agent calls a function
    Agent->>Platform: tool_use: observe(task="find button")
    Platform->>MCP: JSON-RPC call
    MCP->>Fn: Execute (Python + LLM)
    Fn-->>MCP: Result JSON
    MCP-->>Platform: tool_result
    Platform-->>Agent: Show result
```

### Configuration

```json
// .mcp.json — one file, any MCP client can connect
{
  "mcpServers": {
    "gui-agent": {
      "command": "python3",
      "args": ["mcp_server.py"]
    }
  }
}
```

---

## 9. Execution Modes

| Mode | Who controls flow | How | Good for |
|------|-------------------|-----|----------|
| **Static** | Human writes Python | `observe()` → `act()` → `verify()` | Known workflows |
| **Dynamic** | LLM agent via MCP | Agent decides which tools to call | Open-ended tasks |
| **Self-evolving** | LLM + Meta Function | Agent creates new functions as needed | Unknown tasks |

```mermaid
graph TB
    subgraph Mode1["Static: Human writes code"]
        H["Python script"] --> F1["observe()"] --> F2["act()"] --> F3["verify()"]
    end

    subgraph Mode2["Dynamic: LLM decides"]
        A["LLM Agent"] -->|MCP| FF1["observe()"]
        A -->|MCP| FF2["act()"]
        A -->|MCP| FF3["verify()"]
    end

    subgraph Mode3["Self-evolving: LLM creates + calls"]
        B["LLM Agent"] -->|MCP| Meta["meta_create()"]
        Meta -->|creates| New["new_function()"]
        B -->|MCP| New
    end
```

---

## 10. Design Principles

| Principle | Description |
|-----------|-------------|
| **Functions are functions** | Call them, get results. No Runtime class needed. |
| **Docstring = prompt** | Change the docstring, change the behavior. |
| **Dual runtime** | Every function uses Python + LLM together. |
| **Python is the control flow** | if/for/while — not a custom DSL. |
| **Scope is intent** | Declare what you want, Session handles how. |
| **Sessions are pluggable** | Same function works with any LLM backend. |
| **Meta bootstraps everything** | One function creates the entire system. |
| **MCP is transport** | How functions are called. Orthogonal to execution. |

---

## 11. Comparison

```mermaid
graph LR
    subgraph TC["Tool-calling / MCP"]
        direction TB
        LLM1["LLM decides"] --> Py["Python function<br/>(CPU executes)"] --> LLM2["Result back to LLM"]
    end

    subgraph AP["Agentic Programming"]
        direction TB
        Py2["Python + LLM cooperate"] --> Both["Dual Runtime execution<br/>(Python does OCR/click,<br/>LLM does reasoning)"] --> Py3["Structured result"]
    end
```

| | Tool-calling / MCP | Agentic Programming |
|---|---|---|
| **Direction** | LLM → Python → LLM (give LLM hands) | Python + LLM → cooperate (give Python a brain) |
| **Functions contain** | Python code (CPU executes) | Docstring (Python + LLM execute) |
| **Execution** | Single runtime (CPU) | Dual runtime (Python + LLM) |
| **Context** | Implicit (one conversation) | Explicit (Agentic Scope) |
| **Self-evolving** | No | Yes (Meta Agentic Function) |
| **Prompt optimization** | Manual | Programmatic (change docstring, iterate) |

---

## 12. Project Structure

```
harness/
├── __init__.py      Exports: function, ask, extract, classify, ...
├── function/        @function decorator + built-in Agentic Functions
├── session/         Agentic Session interface + 6 implementations
├── scope/           Agentic Scope: context visibility rules
└── memory/          Agentic Memory: persistent execution log

tests/               53 tests covering all components
docs/
└── DESIGN.md        This file
```
