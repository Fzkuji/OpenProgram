# Fast tier

Some vendors offer a paid high-speed tier for some models. OpenProgram surfaces it as a "fast" toggle in the chat UI: when enabled, requests carry the vendor's fast-tier parameter.

## Which models have it

Only two families have a fast tier:

| Family | Request shape |
|---|---|
| GPT 5.4 / 5.5 / 5.6 series (OpenAI priority processing) | `service_tier: "priority"` in the request body |
| Claude Opus 4.6 / 4.7 / 4.8 | `speed: "fast"` in the request body + the fast-mode beta header |

Other models (Gemini, DeepSeek, Qwen, etc.) have no fast concept, and the toggle does not appear for them.

Detection runs per provider along three paths: `openai-codex` reads the `Model.fast` field persisted from the official model endpoint (updated by Fetch after a subscription login — no hand-written list); `claude-code` uses a built-in declaration table (the Opus models above); every other provider checks the models.dev catalog for a priority tier.

## How to enable it

- The "fast" menu item / chip in the chat input box: applies per request to the current model. Switching to an unsupported model hides the toggle and the parameter is never sent.
- `service_tier` in the agent configuration: stores a default tier for an agent, overridable on any turn.

## Which providers it applies to

On the request-building side, only these paths pass the fast-tier parameter through: `openai_responses`, `openai_completions`, and `openai_codex` (`service_tier` in the request body), plus `anthropic` (switching to `speed: "fast"` + the beta header only when the model declares fast support). Other providers do not pass it through, and the parameter never leaves the machine.

Billing note: the fast tier is pay-as-you-go. On a Claude subscription account with no usage credits topped up, Anthropic returns 429 "Usage credits are required for fast mode" — an account issue, not a lack of model support; the UI shows the error as is.

For the full record of implementation details and detection rules, see the [design notes](../reference/design/providers/models/fast-tier.md).
