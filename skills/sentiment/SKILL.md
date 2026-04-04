---
name: sentiment
description: "Analyze text sentiment using LLM. Returns positive, negative, or neutral. Use when: user asks about sentiment, mood, or tone of text. Triggers: 'sentiment', 'is this positive', 'analyze mood', 'what tone'."
---

# Sentiment Analysis

Analyze text sentiment using LLM reasoning.


## Usage

```python
from agentic.functions.sentiment import sentiment
from agentic.providers import ClaudeCodeRuntime

# Bind runtime before first use
sentiment._fn.__globals__['runtime'] = ClaudeCodeRuntime()

result = sentiment(text="I love this product!")
print(result)  # "positive"
```

## Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `text` | `str` | Text to analyze |

## Output

One word: `positive`, `negative`, or `neutral`.
