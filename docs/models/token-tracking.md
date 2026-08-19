# Token tracking

Every vendor reports token usage with different field names and different conventions (some include cached tokens in the input count, some don't). The provider layer normalizes all of them into one usage record per assistant message, so the UI and session records never deal with vendor formats.

## Unified format

Each assistant message carries a `Usage` record (defined in [`openprogram/providers/types.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/types.py)):

| Field | Meaning |
|---|---|
| `input` | Non-cached input tokens (cached tokens are subtracted where the vendor reports an inclusive total) |
| `output` | Output tokens |
| `cache_read` | Input tokens served from the prompt cache |
| `cache_write` | Input tokens written to the prompt cache (providers with explicit caching only) |
| `total_tokens` | The vendor-reported total for the call |
| `cost` | USD cost, computed from the enabled model's per-million-token prices (input / output / cache read / cache write) |

The convention follows Anthropic: `input` excludes cached tokens. Vendors whose input count includes cached tokens (the OpenAI protocols) are converted by subtracting the cached figure.

## What each provider reports

The streaming implementations extract usage from the vendor's final event or last chunk:

| Protocol (providers) | Raw fields | Cache stats |
|---|---|---|
| Anthropic Messages (`anthropic`, `claude-code`, and Anthropic-protocol gateways such as `minimax`, `kimi_coding`, `vercel_ai_gateway`) | `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` | read + write |
| OpenAI Responses (`openai`, `openai_codex`, `azure_openai_responses`, `github_copilot`) | `input_tokens` (includes cached — subtracted), `output_tokens`, `input_tokens_details.cached_tokens`, `total_tokens` | read only |
| OpenAI Completions (`deepseek`, `groq`, `mistral`, `openrouter`, and other compatible endpoints) | `prompt_tokens`, `completion_tokens` (reasoning tokens from `completion_tokens_details` are split out of the output count), `total_tokens` | none |
| Google Generative AI (`google`) | `prompt_token_count`, `candidates_token_count`, `total_token_count` | none |
| Cloud Code Assist (`gemini_subscription`, `google_gemini_cli`) | `promptTokenCount`, `candidatesTokenCount` + `thoughtsTokenCount`, `cachedContentTokenCount`, `totalTokenCount` | read only |
| Bedrock Converse Stream (`amazon_bedrock`) | `inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheWriteInputTokens`, `totalTokens` | read + write |

Cost is computed right after extraction, from the model entry's pricing. On the OpenAI Responses protocol the response's actual `service_tier` additionally adjusts the price, so [fast-tier](fast-tier.md) requests are costed at the priority rate.

## Where usage shows up

- **Per message**: each assistant message stores its own usage and cost; they persist with the session.
- **Chat badge**: the badge by the input box shows the latest call's usage (`11.2k in · 450 out`). No accumulation across calls — the last call's input token count is what the current context occupies, which is the number that matters. The tooltip breaks the input down: base / cache write / cache hit for Claude-protocol providers, base / cached for Codex, a "% cached" figure for the rest. Counts format as `1.2k` above a thousand and `1.0m` above a million.
- **Context bar**: the server pushes a `context_stats` event over the chat WebSocket after each turn, carrying the chat usage plus the model's context window; the UI renders the fill percentage from it.
- **Function executions**: each function run uses its own runtime, and its usage is reported per execution on the function card — independent of the chat conversation's numbers.

Providers report usage per call, so there is no per-provider accumulation logic anywhere: the latest value always describes the latest request.
