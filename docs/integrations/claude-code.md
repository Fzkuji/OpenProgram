# Claude Code

## What Is This?

The `claude-code` provider lets you use Agentic Programming **without any API key**. It uses your Claude subscription's OAuth token to connect directly to `api.anthropic.com` — the token is resolved from the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)'s login credentials (`~/.claude/.credentials.json`) and re-read on every call, so the CLI's token refreshes take effect automatically.

If you have `claude` installed and logged in, you're ready to go.

## Prerequisites

1. **Install the Claude Code CLI:**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

2. **Log in:**
   ```bash
   claude auth login
   ```

3. **Verify it works:**
   ```bash
   claude -p "Hello, world!"
   ```

That's all the setup needed. No API keys, no environment variables.

## Basic Usage

```python
from openprogram import agentic_function
from openprogram.agentic_programming import llm
from openprogram.providers.registry import create_runtime

# No API key needed — uses your Claude Code subscription
runtime = create_runtime(provider="claude-code", model="haiku")

@agentic_function
def explain(concept, runtime=None):
    """Explain a concept clearly and concisely."""
    return llm([
        {"type": "text", "text": f"Explain '{concept}' in 2-3 sentences. Be clear and concise."},
    ])

result = explain(concept="gradient descent", runtime=runtime)
print(result)
```

## Configuration Options

```python
runtime = create_runtime(
    provider="claude-code",
    model="haiku",        # model name or family alias (see the table below)
    api_key=None,         # usually omitted; when omitted, resolved from the credential pool on every call
    max_retries=2,        # retries for transient API-layer failures
)
```

### Model Names

`model` accepts a family alias or a full model id. Aliases expand to the current default version:

| Value | Expands to |
|-------|-------------|
| `"sonnet"` | `claude-sonnet-4-6` (the default family) |
| `"opus"` | `claude-opus-4-6` |
| `"haiku"` | `claude-haiku-4-5` |

More specific ids (such as `claude-opus-4-5-20251101`) are passed through as is and validated by the Anthropic API.

## How It Works

Under the hood, the `claude-code` runtime:

1. Resolves your Claude subscription's OAuth token (`sk-ant-oat` prefix), or a plain Anthropic API key, from the credential pool
2. Connects directly to `api.anthropic.com` over the standard Anthropic Messages protocol, using Bearer auth plus the Claude Code identity header for subscription tokens
3. Writes the reply back into the session DAG

```
Your Python code
    → @agentic_function decorator (records a DAG node)
        → llm() (builds the prompt from the DAG through the ambient runtime)
            → api.anthropic.com (direct subscription OAuth connection)
            ← response text
        ← reply written back as a DAG node
    ← return value
```

No subprocess is involved — it is the standard Anthropic protocol, just with credentials from your subscription.

## Limitations

- **Requires valid credentials.** Construction verifies that a Claude subscription OAuth token or an Anthropic API key exists in the credential pool, and raises `ValueError` otherwise.
- **Subscription tokens expire** (roughly every 8 hours). The runtime re-resolves them on every call, so refreshes on the Claude Code CLI side take effect automatically; if you haven't used `claude` in a long time, just log in again.

## Complete Example

```python
"""
Claude Code integration demo — no API key needed.
Demonstrates a multi-step agentic workflow.
"""
from openprogram import agentic_function
from openprogram.agentic_programming import llm
from openprogram.providers.registry import create_runtime

runtime = create_runtime(provider="claude-code", model="haiku")


@agentic_function
def brainstorm(topic, runtime=None):
    """Generate 3 creative ideas about a topic."""
    return llm([
        {"type": "text", "text": f"Generate exactly 3 creative ideas about: {topic}\nNumber them 1-3, one per line."},
    ])


@agentic_function
def evaluate(idea, runtime=None):
    """Rate an idea's feasibility on a scale of 1-10 with brief reasoning."""
    return llm([
        {"type": "text", "text": f"Rate this idea's feasibility (1-10) and explain in one sentence:\n{idea}"},
    ])


@agentic_function
def ideate(topic, runtime=None):
    """Brainstorm ideas and evaluate each one."""
    ideas_text = brainstorm(topic=topic, runtime=runtime)
    print(f"Ideas:\n{ideas_text}\n")

    lines = [l.strip() for l in ideas_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        rating = evaluate(idea=line, runtime=runtime)
        print(f"  {rating}\n")

    return llm([
        {"type": "text", "text": "Pick the best idea from the evaluation above and explain why in 2 sentences."},
    ])


if __name__ == "__main__":
    result = ideate(topic="improving developer productivity with AI", runtime=runtime)
    print(f"\nBest idea:\n{result}")
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `ValueError: No Claude credential` | Run `claude auth login` (subscription), or add an Anthropic API key under Settings → Providers |
| Auth-related 4xx errors | Token expired or invalid — `claude auth login` again, or diagnose with `openprogram providers doctor` |
| Model id rejected by the API | Ids other than the aliases (`sonnet`/`opus`/`haiku`) are passed through as is; check the spelling and version number |
