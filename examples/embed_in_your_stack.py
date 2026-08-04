"""
Embedding OpenProgram's DAG-context function calling in your own stack.

The premise: you already have an app, an LLM client, and somewhere you keep
state. You want @agentic_function and its execution DAG, and nothing else —
no webui, no TUI, no CLI, no ``~/.openprogram`` appearing behind your back.

Four things this shows:

  1. bringing your own LLM call (any client — a fake one here, so it runs
     with no API key; the OpenAI version is right below it, commented);
  2. pointing session state at a directory you choose;
  3. running @agentic_functions and reading the execution DAG back;
  4. handing ``fn.spec`` to a tool loop you drive yourself.

Run it:  python examples/embed_in_your_stack.py
"""

from pathlib import Path

from openprogram import agentic_function, Runtime
from openprogram.agentic_programming.tool_format import to_openai_tools
from openprogram.store import GraphStoreShim, SessionStore, session_scope


# ── 1. Your LLM call ────────────────────────────────────────────
#
# The contract is one function:
#
#     fn(content: list[dict], model: str, response_format: dict | None) -> str
#
# ``content`` is a list of blocks, each ``{"type": "text", "text": ...}`` or
# ``{"type": "image", ...}``. Return the reply as a string. That is the whole
# integration surface — whatever client you already use goes inside here.

def fake_call(content, model="demo", response_format=None):
    """Stand-in for a real client so the example runs offline."""
    prompt = next(
        (b["text"] for b in reversed(content) if b["type"] == "text"), ""
    )
    if "sentiment" in prompt.lower():
        return "positive"
    return f"[{model}] handled: {prompt[:60]}"


# The same thing against the OpenAI SDK:
#
# from openai import OpenAI
# client = OpenAI()
#
# def openai_call(content, model="gpt-4o-mini", response_format=None):
#     text = "\n".join(b["text"] for b in content if b["type"] == "text")
#     reply = client.chat.completions.create(
#         model=model,
#         messages=[{"role": "user", "content": text}],
#         response_format=response_format,
#     )
#     return reply.choices[0].message.content


runtime = Runtime(call=fake_call, model="demo")


# ── 2. Your functions ───────────────────────────────────────────
#
# The docstring is the instruction sent to the model. Calls nest: the DAG
# records who called whom, which is what later calls get as context.
#
# Pass your runtime to the OUTERMOST call. It then propagates down the call
# chain automatically, so nested functions only have to forward it. Omitting
# it at the top makes the entry point build its own runtime from the machine's
# configured provider — convenient for a standalone script, but not what an
# embedded app wants, since it reaches for credentials and the network.

@agentic_function
def classify_sentiment(review, runtime=None):
    """Classify the sentiment of a review as positive, negative, or neutral."""
    return runtime.exec(f"Sentiment of this review: {review}")


@agentic_function
def summarize_review(review, runtime=None):
    """Summarize a customer review in one sentence, noting its sentiment."""
    sentiment = classify_sentiment(review, runtime=runtime)
    return runtime.exec(f"Summarize this {sentiment} review: {review}")


def main():
    # ── 3. Your state directory ─────────────────────────────────
    #
    # SessionStore(root_path=...) is the explicit-directory entry point. Pass
    # one and nothing consults ~/.openprogram. Each session is a git repo
    # under this root; point it wherever your app keeps data.

    # A real app passes its own data directory here. This example writes into
    # ./embed_demo_state next to itself, rather than a temp dir the store's
    # atexit index flush would find already deleted.
    sessions_dir = Path(__file__).parent / "embed_demo_state" / "sessions"
    store = SessionStore(root_path=sessions_dir)
    if "review-42" not in {s["id"] for s in store.list_sessions()}:
        store.create_session("review-42", agent_id="main")

    # session_scope routes DAG writes to your store for the duration.
    # Skip the with-block and everything still runs, just without
    # persistence.
    with session_scope(store, "review-42"):
        review = "The battery lasts all week and setup took two minutes."
        summary = summarize_review(review, runtime=runtime)
        print("── Result ──")
        print(summary)

    # ── 4. Reading the DAG back ─────────────────────────────────
    #
    # Nodes carry role (code / llm / user), name, input, output, and a
    # ``caller`` edge. Following ``caller`` turns the flat list into the
    # call tree — this is the context later calls are assembled from.

    graph = GraphStoreShim(store, "review-42").load()
    print("\n── Execution DAG ──")
    for node in graph:
        kind = "fn " if node.is_code() else "llm" if node.is_llm() else "usr"
        label = node.name or "-"
        output = str(node.output or "").replace("\n", " ")[:52]
        print(f"  {kind}  {label:<20} → {output}")

    print(f"\nState written under: {sessions_dir}")


# ── 5. Driving your own tool loop ───────────────────────────────
#
# ``fn.spec`` is a JSON schema built from the signature and docstring.
# ``to_openai_tools`` reshapes it for the Chat Completions ``tools=[...]``
# array; dispatch whatever the model picks back through ``fn.execute``.

def tool_loop_demo():
    tools = to_openai_tools([classify_sentiment, summarize_review])

    print("\n── Tool specs for your own loop ──")
    for tool in tools:
        fn = tool["function"]
        params = ", ".join(fn["parameters"]["properties"])
        print(f"  {fn['name']}({params}) — {fn['description']}")

    # What you would do with a tool call your client returned:
    #
    #   reply = client.chat.completions.create(model=..., messages=..., tools=tools)
    #   for call in reply.choices[0].message.tool_calls:
    #       fn = {"classify_sentiment": classify_sentiment}[call.function.name]
    #       result = fn.execute(runtime=runtime, **json.loads(call.function.arguments))
    #
    # ``runtime`` goes in alongside the model's arguments — the model never
    # sees that parameter (it is absent from the spec) and never supplies it.
    # Executed directly here so the example stays runnable offline:
    result = classify_sentiment.execute(
        review="Works exactly as advertised.", runtime=runtime,
    )
    print(f"\n  classify_sentiment.execute(...) → {result}")


if __name__ == "__main__":
    main()
    tool_loop_demo()
