# Thinking effort

Each vendor's API controls reasoning depth with a different parameter (an `effort` string, `reasoning_effort`, a token budget). OpenProgram unifies them into a single effort slider.

## Levels

The framework defines `off` plus six levels:

```
minimal · low · medium · high · xhigh · max
```

Each model supports only a subset, and the UI shows the levels the model actually supports. For example, Opus 4.8 has five levels from low to max, DeepSeek reasoner is not adjustable (it always reasons at full effort), and the GitHub Copilot path has no thinking support. Level data comes from each provider's declaration (the `thinking` block in `provider.json`, with per-model overrides); providers whose endpoint reports capabilities — Anthropic's `/v1/models`, the Codex subscription models endpoint — refresh the per-model levels on Fetch. Providers with no declaration at all fall back to the three levels low / medium / high.

## Defaults

The default level is declared by each provider (`default_effort` in `provider.json`):

| Provider family | Default |
|---|---|
| OpenAI (Responses and Completions) / Codex / Azure OpenAI | `xhigh` |
| Anthropic / claude-code, Amazon Bedrock | `high` |
| Google / Gemini CLI (converted via token budget), DeepSeek | `medium` |
| Providers with no declaration (three-level fallback) | `medium` |

## How to change it

Three layers from outermost to innermost, with inner layers overriding outer ones:

1. **Agent configuration** `thinking_effort`: the agent's default level (agent settings in the Web UI).
2. **Project configuration**: a project-level default that overrides the agent default.
3. **Per turn**: the level picker in the chat UI (next to the Web UI input box), or the `/effort` command in the TUI; applies turn by turn to the current session.

The selected level persists with the session, and the provider translates the framework level into the vendor API's actual parameter before sending each request.

For the full record of mapping rules and detection strategy, see the [design notes](../reference/design/providers/models/thinking-effort.md).
