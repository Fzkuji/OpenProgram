# LLM Agent Harness — Design Document

## 1. Motivation

Current LLM agent frameworks have a fundamental problem: they treat the LLM as the brain and let it decide everything. The agent decides what tools to call, in what order, and when to stop. This makes agents powerful but unpredictable.

When you need a reliable, repeatable workflow — like GUI automation where every step must happen in order — this unpredictability is a blocker. The LLM might skip a step, take a shortcut, or decide the task is done when it isn't.

### The Core Problem

Today's approaches fall into two extremes:

- **Pure code**: deterministic but rigid, can't handle ambiguity
- **Pure LLM agent**: flexible but unpredictable, can't be relied upon

Neither works for complex, real-world workflows that need both structure and intelligence.

### The Insight

What if we treated LLM calls the way we treat function calls? A function has:

- A name and description (what it does)
- Typed inputs (what it receives)
- Typed outputs (what it must return)
- A runtime that executes it (CPU, JVM, interpreter)

A **Step** in this framework is exactly this — except the runtime is an LLM session, and the description is written in natural language. The LLM executes the function. The framework guarantees the output.

> **The LLM is not the orchestrator. The LLM is the runtime. Your code is the orchestrator.**

---

## 2. Core Concepts

### Step

The fundamental unit of execution. A typed function executed by an LLM session.

| Field | Required | Description |
|-------|----------|-------------|
| name | Yes | Identifier for this step |
| description | Yes | What this step does (1-2 sentences) |
| instructions | Yes | How to do it — the Skill content, in natural language |
| output_schema | Yes | Pydantic model this step MUST return |
| reads | No | Which context fields this step needs (None = full context) |
| examples | No | Sample input/output pairs to guide the LLM |

**Key principle**: a Step does not return until its output matches the output_schema. If the session's reply doesn't conform, the framework retries automatically.

### Session

The pluggable runtime. Single interface:

```
session.send(message: str) → reply: str
```

| Session Type | Description |
|-------------|-------------|
| AnthropicSession | Direct Anthropic API — full control |
| OpenClawSession | Routes through OpenClaw agent — uses its memory and tools |
| NanobotSession | Routes through nanobot agent |
| CustomSession | Any implementation of send() → reply |

The Session is passed to the Step at runtime. Switching platforms means switching the Session — the Step definition never changes.

### Workflow

An ordered sequence of Steps. The output of each Step becomes part of the shared context available to subsequent Steps.

The Workflow engine guarantees:
- Steps execute in order
- Context accumulates across Steps
- Failure is explicit — if a Step fails, the Workflow stops and reports which Step failed

### Context

A shared dictionary that flows through the entire Workflow. Each Step declares:
- `reads`: which fields it needs as input
- After execution, its output is written to `context[step.name]`

### Skill

The natural language instructions inside a Step — the `instructions` field. It tells the LLM what to do and how to think about it.

Skills are just text. They can be:
- Inline strings in the Step definition
- Loaded from a SKILL.md file (compatible with OpenClaw / nanobot skill format)
- Generated dynamically based on context

---

## 3. How a Step Executes

### Phase 1 — Message Assembly

The framework assembles a structured message:

```
## Step: {name}

### What this step does
{description}

### How to do it
{instructions}

### Input
{selected fields from context}

### Required output format
{output_schema as JSON schema}
```

### Phase 2 — Session Execution

The assembled message is sent to the Session. The Session's LLM reasons over it, may use tools available in its environment, and produces a reply.

The framework does not constrain what happens inside the Session. It only constrains the final output.

### Phase 3 — Output Validation

The reply is parsed and validated against output_schema:
- If valid → the Step returns the structured result
- If invalid → retry with a correction message (up to max_retries)
- If retries exhausted → raise StepFailure

---

## 4. Session Contract

Any Session must satisfy:

```python
class Session(ABC):
    @abstractmethod
    def send(self, message: str) -> str:
        pass
```

**The Session must provide:**
- Replies are text
- Replies are complete (not streamed)

**The Session does NOT need to provide:**
- Structured output (the Step handles parsing)
- Tool execution (the Session's environment handles it)
- Memory (Sessions without memory still work)

---

## 5. Design Principles

| Principle | What it means |
|-----------|---------------|
| LLM is runtime, not orchestrator | The framework drives execution; the LLM executes individual steps |
| Outputs are contracts | A Step doesn't complete until its output matches the schema |
| Sessions are pluggable | Switching platforms = switching Session; Steps never change |
| Skills are content, not code | Skills work with any Session |
| Context is explicit | Steps declare reads; no hidden state |
| Failure is loud | Steps fail explicitly; the Workflow never silently skips |

---

## 6. Comparison

| Feature | LangGraph | OpenClaw Skills | Instructor | LLM Agent Harness |
|---------|-----------|----------------|------------|-------------------|
| Structured output guarantee | Partial | No | Yes | Yes |
| Session pluggable | No | No | No | Yes |
| Step ordering enforced | Yes | No | No | Yes |
| Natural language instructions | No | Yes | No | Yes |
| Works with external agents | No | N/A | No | Yes |
| Context isolation per Step | Partial | No | No | Yes |

---

## 7. What This Framework Is Not

- **Not an agent framework** — the LLM has no autonomy to decide what to do next
- **Not a prompt library** — Skills are execution instructions, not templates
- **Not a replacement for LangGraph** — for fully autonomous agents, LangGraph is more appropriate
- **Not tied to one LLM provider** — the Session abstraction means the framework has no opinion on which LLM you use
