# Embedding in your own stack

You already have an application, an LLM client you trust, and a place where you
keep state. What you want from OpenProgram is the part that is hard to
rebuild — `@agentic_function` and the execution DAG that turns nested calls into
context — and nothing else. No web UI, no terminal UI, no CLI, and no
`~/.openprogram` directory appearing behind your back.

That is the embedded mode: OpenProgram used as a plain Python library inside
somebody else's runtime. Five steps get you there, and every snippet below is
lifted from the runnable example
[`examples/embed_in_your_stack.py`](https://github.com/Fzkuji/OpenProgram/blob/main/examples/embed_in_your_stack.py),
which executes offline against a fake client so you can run it with no API key.

## When this is the right mode

Reach for embedding when you are building the surrounding product yourself —
a service, an agent framework of your own, a research harness — and you want
DAG-context function calling as one component inside it. If instead you want
the whole harness (chat surfaces, sessions, provider management, tools), run
the platform normally; see [Getting started](../../start/GETTING_STARTED.md).

## 1. Bring your own LLM call

The integration surface is a single function:

```python
fn(content: list[dict], model: str, response_format: dict | None) -> str
```

`content` is a list of blocks, each `{"type": "text", "text": ...}` (or an
image block). Return the model's reply as a string. Whatever client you already
use goes inside:

```python
from openai import OpenAI

client = OpenAI()

def openai_call(content, model="gpt-4o-mini", response_format=None):
    text = "\n".join(b["text"] for b in content if b["type"] == "text")
    reply = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": text}],
        response_format=response_format,
    )
    return reply.choices[0].message.content
```

## 2. Wrap it in a Runtime

```python
from openprogram import Runtime

runtime = Runtime(call=openai_call, model="gpt-4o-mini")
```

`Runtime(call=...)` is the constructor that takes your function directly; no
provider is detected, no credential is read, no network call is made at
construction time. (The alternative, `create_runtime()`, builds a runtime from
the machine's configured provider — convenient for a standalone script, and
exactly what an embedded host does *not* want. See
[Providers](../../models/providers.md).)

The four embedding entry points — `agentic_function`, `Runtime`, `decision`,
`Session` — are re-exported from the package root and loaded lazily, so
`import openprogram` on its own pulls in neither the runtime, nor the store,
nor any provider.

## 3. Write your functions

The docstring is the instruction sent to the model. Calls nest, and the DAG
records who called whom — which is what later calls receive as context.

```python
from openprogram import agentic_function

@agentic_function
def classify_sentiment(review, runtime=None):
    """Classify the sentiment of a review as positive, negative, or neutral."""
    return runtime.exec(f"Sentiment of this review: {review}")


@agentic_function
def summarize_review(review, runtime=None):
    """Summarize a customer review in one sentence, noting its sentiment."""
    sentiment = classify_sentiment(review, runtime=runtime)
    return runtime.exec(f"Summarize this {sentiment} review: {review}")
```

Pass your runtime to the **outermost** call only. It propagates down the call
chain automatically; nested functions just forward it. Omit it at the top and
the entry point builds its own runtime from the machine's configured provider,
which reaches for credentials and the network.

Everything in [Writing functions](writing-functions/agentic-function.md)
applies unchanged — the decorator behaves the same embedded as it does inside
the platform.

## 4. Point session state at a directory you choose

`SessionStore(root_path=...)` is the explicit-directory entry point. Pass one
and nothing consults `~/.openprogram`. Each session is a git repo under that
root.

```python
from openprogram.store import SessionStore, session_scope

store = SessionStore(root_path="/var/lib/myapp/sessions")
store.create_session("review-42", agent_id="main")

with session_scope(store, "review-42"):
    summary = summarize_review(review, runtime=runtime)
```

`session_scope` routes DAG writes to that store for the duration of the block
and restores the previous binding on exit. Skipping the `with` block is also
valid: functions execute normally and simply persist nothing, which is what a
host that keeps its own trace wants.

## 5. Read the DAG back, or drive your own tool loop

**Reading the DAG.** Nodes carry a role (code / llm / user), a name, input,
output, and a `caller` edge. Following `caller` turns the flat node list into
the call tree:

```python
from openprogram.store import GraphStoreShim

graph = GraphStoreShim(store, "review-42").load()
for node in graph:
    kind = "fn" if node.is_code() else "llm" if node.is_llm() else "usr"
    print(kind, node.name, "→", node.output)
```

**Handing functions to your own loop.** Every `@agentic_function` exposes
`.spec` (a JSON schema built from the signature and docstring) and `.execute`.
`to_openai_tools` reshapes the spec for the Chat Completions `tools=[...]`
array:

```python
import json
from openprogram.agentic_programming.tool_format import to_openai_tools

tools = to_openai_tools([classify_sentiment, summarize_review])

reply = client.chat.completions.create(model=..., messages=..., tools=tools)
for call in reply.choices[0].message.tool_calls:
    fn = {"classify_sentiment": classify_sentiment}[call.function.name]
    result = fn.execute(runtime=runtime, **json.loads(call.function.arguments))
```

`runtime` goes in alongside the model's arguments. The model never sees that
parameter — it is filtered out of the spec — and never supplies it.

## Relationship to a full install

There is one `pip install openprogram`, and embedding uses only its library
face. The web UI and the terminal UI ship in the same package, but the
embedded path never imports them — the acceptance tests in `tests/embed/`
fail if it ever does — so they stay inert, and a deployment that strips
FastAPI, Uvicorn, or Textual still runs the library face. Start the platform
later and nothing about your embedded code changes.

## Host integration seams

Two hooks let a host that *does* have a front end wire itself in:
`set_cancellation_check` installs the checkpoint the exec loop calls before each
LLM attempt (raise from it to abort a run), and `set_session_id_provider` tells
the core which session a question should be routed back to. Both live in
`openprogram.agentic_programming.function` and default to headless no-ops, so
embedded code that never touches them behaves correctly: nothing cancels, and
`runtime.can_ask()` reports `False` because nobody is there to answer.

## See also

- [`examples/embed_in_your_stack.py`](https://github.com/Fzkuji/OpenProgram/blob/main/examples/embed_in_your_stack.py) — the runnable version of this page
- [`@agentic_function`](writing-functions/agentic-function.md) — the decorator in depth
- [Runtime API](../../reference/api/runtime.md) — `exec()` parameters and behaviour
- [Embedding seams](../../reference/design/runtime/overview.md) — the design contract behind this mode
