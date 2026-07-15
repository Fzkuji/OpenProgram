# Providers

This page lists the provider implementations built into the repository (one subdirectory each under `openprogram/providers/`), how each one is accessed, and how to use providers directly from Python code. Beyond the built-in implementations, `openprogram providers available` also lists hundreds of community provider catalog entries that speak the OpenAI-compatible protocol; they are configured the same way.

## Built-in providers

Access methods: **API key** = a key stored in the credential store (`providers login <id>`, also importable from environment variables); **OAuth** = browser / device-code login with a subscription account; **CLI credentials** = reads the credential file of an external CLI that is already logged in; **cloud credential chain** = resolved at runtime through the cloud vendor's standard credential chain.

| Provider | Protocol | Access | Notes |
|---|---|---|---|
| `anthropic` | Anthropic Messages | API key (`ANTHROPIC_API_KEY`) or OAuth (Claude subscription, PKCE / pasting a `claude setup-token`) | Explicit prompt caching (`cache_control`, 1h TTL supported) |
| `openai` | OpenAI Responses | API key (`OPENAI_API_KEY`) | Automatic caching on the Responses protocol (`prompt_cache_key`) |
| `openai_responses` / `openai_completions` | OpenAI Responses / Chat Completions | — (shared protocol implementations, reused by many providers) | |
| `openai_codex` | ChatGPT backend | OAuth (ChatGPT subscription): browser PKCE sign-in; an existing `codex` CLI login can also be imported via `providers discover` | Model list pulled live from the official endpoint |
| `azure_openai_responses` | Azure OpenAI Responses | API key (`AZURE_OPENAI_API_KEY`) + a base URL you supply | |
| `google` | Google Generative AI | API key (`GEMINI_API_KEY` / `GOOGLE_API_KEY`) | Thinking controlled via a token budget |
| `google_gemini_cli` | Cloud Code Assist | CLI credentials: reads `~/.gemini/oauth_creds.json` directly; the Gemini CLI handles refreshing | |
| `gemini_subscription` | Cloud Code Assist | CLI credentials: imports `~/.gemini/oauth_creds.json` (log in with the Gemini CLI first) | Aliases `gemini`, `gemini-cli` |
| `amazon_bedrock` | Bedrock Converse Stream | Cloud credential chain (`AWS_PROFILE` / access keys / bearer token, etc., detected at runtime) | Explicit prompt caching (`cachePoint`) |
| `github_copilot` | OpenAI Responses and others | GitHub device-code OAuth in the browser, or import a token from `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN`; exchanged on demand for a short-lived Copilot token that is never written to disk | No thinking-effort support |
| `deepseek` | OpenAI Completions | API key (`DEEPSEEK_API_KEY`) | Reasoning is not adjustable on reasoner models |
| `openrouter` | OpenAI Completions | API key (`OPENROUTER_API_KEY`) | Aggregation gateway |
| `vercel_ai_gateway` | Anthropic Messages | API key (`AI_GATEWAY_API_KEY`) | Aggregation gateway |
| `groq` | OpenAI Completions | API key (`GROQ_API_KEY`) | |
| `cerebras` | OpenAI Completions | API key (`CEREBRAS_API_KEY`) | |
| `mistral` | OpenAI Completions | API key (`MISTRAL_API_KEY`) | |
| `xai` | OpenAI Completions | API key (`XAI_API_KEY`) | |
| `zai` | OpenAI Completions | API key (`ZAI_API_KEY`) | |
| `huggingface` | OpenAI Completions | API key (`HF_TOKEN`) | |
| `minimax` / `minimax_cn` | Anthropic Messages | API key (`MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY`) | International / China endpoints |
| `minimax_cn_coding_plan` | Anthropic Messages | API key (`MINIMAX_CN_API_KEY` / `MINIMAX_API_KEY` — same account and key as `minimax_cn`) | "MiniMax Token Plan (CN)" coding subscription |
| `kimi_coding` | Anthropic Messages | API key (`KIMI_API_KEY` / `MOONSHOT_API_KEY`) | |
| `alibaba_token_plan_cn` | OpenAI Completions | Plan API key | Alias `bailian` |
| `opencode` | OpenAI Completions and others | API key (`OPENCODE_API_KEY`) | |

Streaming output is supported by every provider (the whole layer is built on the streaming interface). Multimodal input is decided per model rather than per provider, based on each provider's model catalog data; trust what the UI shows for a given model. Prompt caching has been verified in code only where noted in the table above.

Empty directories such as `claude_code`, `chatgpt_subscription`, and `claude_max_proxy` are alias placeholders (`claude-max` → `claude-code`, `chatgpt-subscription` → `openai-codex`); their behavior is decided by the alias table, not by a separate implementation. `minimax_cn_coding_plan` is likewise config-driven with no code of its own.

## Custom providers

Any OpenAI-compatible endpoint the table does not cover can be added from the Web UI under Settings → Providers: a display name and a base URL are the only required fields (an id is derived from the name if you don't give one). The endpoint's `/models` list is then browsable with the same Fetch button as built-in providers, and enabled models work at runtime with no code changes. Custom providers are stored in config as `providers.<id>` with `source: "custom"`.

## Using providers as a library

To create a runtime in your own Python code, prefer auto-detection:

```python
from openprogram.providers.registry import create_runtime

runtime = create_runtime()                                        # picks the first available provider
runtime = create_runtime(provider="anthropic", model="claude-sonnet-4-6")
```

Six built-in runtime classes can be imported directly:

```python
from openprogram.providers import (
    AnthropicRuntime,     # Anthropic API
    OpenAIRuntime,        # OpenAI Responses API
    GeminiRuntime,        # Google Generative AI
    ClaudeCodeRuntime,    # Claude subscription direct connection, no API key needed
    OpenAICodexRuntime,   # ChatGPT subscription (Codex OAuth)
    GeminiCLIRuntime,     # reuses the Gemini CLI login state
)

runtime = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")
runtime = OpenAICodexRuntime(model="gpt-5.5")
```

The remaining providers in the table above have no dedicated runtime class: `create_runtime(provider=..., model=...)` routes automatically by the model's protocol, using the same path as the chat UI.
