# Claude Code Integration

## What Is This?

`ClaudeCodeRuntime` lets you use Agentic Programming **without any API key**. It routes LLM calls through the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code), which uses your Claude Code subscription.

If you have `claude` installed and logged in, you're ready to go.

## Prerequisites

1. **Install Claude Code CLI:**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

2. **Log in:**
   ```bash
   claude login
   ```

3. **Verify it works:**
   ```bash
   claude -p "Hello, world!"
   ```

That's all the setup needed. No API keys, no environment variables.

## Basic Usage

```python
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

# No API key needed — uses Claude Code subscription
runtime = ClaudeCodeRuntime(model="haiku")

@agentic_function
def explain(concept):
    """Explain a concept clearly and concisely."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Explain '{concept}' in 2-3 sentences. Be clear and concise."},
    ])

result = explain(concept="gradient descent")
print(result)
```

## Configuration Options

```python
runtime = ClaudeCodeRuntime(
    model="haiku",       # Model name (passed to --model flag)
    timeout=120,          # Max seconds per CLI call (default: 120)
    cli_path=None,        # Path to claude binary (auto-detected)
)
```

### Model Names

The `model` parameter is passed directly to `claude -p --model <model>`. Common values:

| Model | Description |
|-------|-------------|
| `"sonnet"` | Claude Sonnet (default, fast & capable) |
| `"opus"` | Claude Opus (most capable) |
| `"haiku"` | Claude Haiku (fastest, cheapest) |

## How It Works

Under the hood, `ClaudeCodeRuntime`:

1. Combines all content blocks into a text prompt
2. Calls `claude -p <prompt>` as a subprocess
3. Returns the CLI's stdout as the result

```
Your Python code
    → @agentic_function decorator (records a DAG node)
        → runtime.exec() (builds the prompt from the DAG)
            → claude -p "..." (CLI call)
                → Claude API (via subscription)
            ← response text
        ← reply written back as a DAG node
    ← return value
```

## Limitations

- **Text only.** Images, audio, and file blocks are converted to text placeholders (`[Image: path]`). For multimodal input, use `AnthropicRuntime` with an API key.
- **Subprocess overhead.** Each call spawns a new process (~0.5-1s overhead). For latency-sensitive applications, use direct API providers.
- **No streaming.** Results are returned after the full response is generated.
- **Timeout.** Long responses may hit the default 120s timeout. Increase with `timeout=300`.

## Complete Example

```python
"""
Claude Code integration demo — no API key needed.
Demonstrates a multi-step agentic workflow.
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def brainstorm(topic):
    """Generate 3 creative ideas about a topic."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Generate exactly 3 creative ideas about: {topic}\nNumber them 1-3, one per line."},
    ])


@agentic_function
def evaluate(idea):
    """Rate an idea's feasibility on a scale of 1-10 with brief reasoning."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Rate this idea's feasibility (1-10) and explain in one sentence:\n{idea}"},
    ])


@agentic_function
def ideate(topic):
    """Brainstorm ideas and evaluate each one."""
    ideas_text = brainstorm(topic=topic)
    print(f"💡 Ideas:\n{ideas_text}\n")

    lines = [l.strip() for l in ideas_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        rating = evaluate(idea=line)
        print(f"  📊 {rating}\n")

    return runtime.exec(content=[
        {"type": "text", "text": "Pick the best idea from the evaluation above and explain why in 2 sentences."},
    ])


if __name__ == "__main__":
    result = ideate(topic="improving developer productivity with AI")
    print(f"\n🏆 Best idea:\n{result}")
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `FileNotFoundError: Claude Code CLI not found` | Install: `npm install -g @anthropic-ai/claude-code` |
| `ConnectionError: Claude Code CLI not logged in` | Run: `claude login` |
| `TimeoutError: Claude Code CLI timed out` | Increase timeout: `ClaudeCodeRuntime(timeout=300)` |
| `RuntimeError: Claude Code CLI error` | Check `claude -p "test"` works manually |
