# Providers

> Source: [`openprogram/providers/`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/)

`create_runtime` plus the built-in `Runtime` subclasses. All providers speak the raw HTTP APIs through OpenProgram's provider layer — **no vendor SDK needs to be installed**. The CLI/subscription providers reuse the OAuth credentials of the corresponding CLI tool, so those CLIs must be installed and logged in once.

```bash
# Codex CLI (for openai-codex / OpenAICodexRuntime)
npm install -g @openai/codex && codex login

# Gemini CLI (for gemini-cli / GeminiCLIRuntime)
npm install -g @google/gemini-cli && gemini

# Claude Code CLI (for claude-code / ClaudeCodeRuntime — its OAuth token is adopted)
npm install -g @anthropic-ai/claude-code && claude login
```

API keys for the API providers are stored in the credential store: **Settings → Providers** in the Web UI, or `openprogram providers login <provider> --api-key`.

---

## create_runtime / detect_provider

```python
from openprogram.providers.registry import create_runtime, detect_provider, check_providers

rt = create_runtime()                                   # auto-detect the best available provider
rt = create_runtime(provider="anthropic")               # explicit provider, its default model
rt = create_runtime(provider="openai-codex", model="gpt-5.5")
```

### `create_runtime(provider=None, model=None, **kwargs)`

Returns a ready-to-use `Runtime`. `provider=None` (or `"auto"`) runs `detect_provider()`. The six providers below get their dedicated `Runtime` subclass; **any other provider name** (deepseek, groq, openrouter, minimax, kimi, and the rest of the catalogue) is routed through the base `Runtime("provider:model", ...)` via the model registry — the same path the chat dispatcher uses. `**kwargs` are forwarded to the runtime constructor.

### `detect_provider() -> (provider_name, default_model)`

Detection priority:

1. Environment variables `AGENTIC_PROVIDER` / `AGENTIC_MODEL`
2. Config file (`~/.openprogram/config.json` → `default_provider` / `default_model`)
3. Caller environment (running inside Codex CLI → use it)
4. Available CLI binaries (`codex` → `openai-codex`, `gemini` → `gemini-cli`)
5. Stored API keys (anthropic → openai → google)

Raises `RuntimeError` with setup guidance when nothing is found.

### `check_providers() -> dict`

Availability report for the six dedicated providers: `{name: {"available": bool, "method": "CLI"|"API", "model": default}}`, with `"default": True` on the one `detect_provider()` would pick.

### The `PROVIDERS` table

| Provider name | Runtime class | Default model | Credential |
|------|------|------|------|
| `claude-code` | `ClaudeCodeRuntime` | `claude-sonnet-4` (alias, expanded to the current Sonnet) | Claude subscription OAuth (adopted from Claude Code CLI) |
| `openai-codex` | `OpenAICodexRuntime` | `gpt-5.5` | ChatGPT subscription OAuth (`~/.codex/auth.json`) |
| `gemini-cli` | `GeminiCLIRuntime` | `gemini-2.5-flash` | Google account OAuth (`~/.gemini/oauth_creds.json`) |
| `anthropic` | `AnthropicRuntime` | `claude-sonnet-4-6` | Anthropic API key |
| `openai` | `OpenAIRuntime` | `gpt-4.1` (table) / `gpt-4o` (class constructor) | OpenAI API key |
| `gemini` | `GeminiRuntime` | `gemini-2.5-flash` | Google API key |

All six classes are importable from `openprogram.providers` (lazy) or `openprogram.providers.registry`.

---

## AnthropicRuntime

Anthropic Messages API, via the provider layer (streaming, tool loop, DAG recording all included).

```python
from openprogram.providers import AnthropicRuntime

rt = AnthropicRuntime(api_key="sk-ant-...", model="claude-sonnet-4-6")
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. `None` = resolved from the credential store — a stored API key or an adopted Claude-subscription OAuth token (`sk-ant-oat...`, for which the wire switches to Bearer auth automatically) |
| `model` | `str` | `"claude-sonnet-4-6"` | Model id under the `anthropic` provider namespace |
| `max_retries` | `int` | `2` | Retry budget forwarded to the base `Runtime` |

Raises `ValueError` when no credential can be resolved. `list_models()` returns the enabled Anthropic model ids.

---

## OpenAIRuntime

OpenAI Responses API, via the provider layer.

```python
from openprogram.providers import OpenAIRuntime

rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. `None` = resolved from the credential store (`openprogram providers login openai --api-key`) |
| `model` | `str` | `"gpt-4o"` | Model id under the `openai` provider namespace |
| `max_retries` | `int` | `2` | Retry budget forwarded to the base `Runtime` |

For Azure or a local OpenAI-compatible server, add a custom provider (Settings → Providers → Add custom provider, name + base URL) and use `Runtime(model="<provider>:<model>")` or `create_runtime(provider="<provider>")`.

---

## GeminiRuntime

Google Gemini Generative Language API, via the provider layer.

```python
from openprogram.providers import GeminiRuntime

rt = GeminiRuntime(api_key="...", model="gemini-2.5-flash")
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. `None` = resolved from the credential store (accepted env-var names when adding one: `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`) |
| `model` | `str` | `"gemini-2.5-flash"` | Model id under the `google` provider namespace |
| `max_retries` | `int` | `2` | Retry budget forwarded to the base `Runtime` |

---

## ClaudeCodeRuntime

Claude via a **Claude subscription** — connects directly to `api.anthropic.com` with the subscription's OAuth token (Bearer auth + Claude Code identity headers). No API key billing; the token is resolved fresh on every call so CLI-side rotations propagate.

```python
from openprogram.providers import ClaudeCodeRuntime

rt = ClaudeCodeRuntime(model="claude-sonnet-4")
```

Setup: log in once with the Claude Code CLI (`claude login`) so the OAuth token can be adopted, or add a Claude account with `openprogram providers claude-code accounts add`.

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | Normally omitted — the token resolves from the credential store on every call. Passing a value pins it (not recommended: subscription tokens expire) |
| `model` | `str` | `"claude-sonnet-4"` | A bare family alias (`claude-opus-4` / `claude-sonnet-4` / `claude-haiku-4`) expands to the current default of that family; any more-specific id (`claude-opus-4-8`, dated ids) is passed through verbatim |
| `max_retries` | `int` | `2` | Retry budget forwarded to the base `Runtime` |

Extra keyword arguments are accepted and ignored for backward compatibility. Raises `ValueError` when no Claude credential exists.

---

## OpenAICodexRuntime

ChatGPT / Codex **subscription** runtime. Reads OAuth credentials adopted from the Codex CLI's `~/.codex/auth.json` and talks to the ChatGPT Responses backend. Refreshed tokens are mirrored back so the Codex CLI stays in sync.

```python
from openprogram.providers import OpenAICodexRuntime

rt = OpenAICodexRuntime(model="gpt-5.5")
```

Setup:

```bash
npm install -g @openai/codex
codex login          # OAuth login — do not pick the API-key option
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|------|------|
| `model` | `str` | `"gpt-5.5"` | Codex model id (a `openai-codex:` prefix, if present, is stripped) |
| `system` | `str \| None` | `None` | Optional system prompt |
| `profile` | `str \| None` | active profile | OpenProgram auth profile to use (keyword-only) |

Extra keyword arguments are accepted and ignored. Requires an OAuth credential — a bare OpenAI API key raises `AuthConfigError` (use `OpenAIRuntime` instead).

---

## GeminiCLIRuntime

Gemini via a **Google account** (Gemini CLI OAuth). Reuses `~/.gemini/oauth_creds.json` and talks to the Cloud Code Assist backend over HTTP — no subprocess.

```python
from openprogram.providers import GeminiCLIRuntime

rt = GeminiCLIRuntime(model="gemini-2.5-flash")
```

Setup:

```bash
npm install -g @google/gemini-cli
gemini               # first run performs the OAuth login
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|------|------|
| `model` | `str` | `"gemini-2.5-flash"` | Model id; must match a `gemini-subscription/<id>` registry entry |
| `system` | `str \| None` | `None` | Optional system prompt |
| `profile` | `str \| None` | active profile | OpenProgram auth profile to use (keyword-only) |

Extra keyword arguments are accepted and ignored. If you only have a Google API key, use `GeminiRuntime`.

---

## Every other provider

Providers without a dedicated class — deepseek, groq, openrouter, minimax, kimi, and the rest of the catalogue — work through the model registry:

```python
from openprogram.agentic_programming.runtime import Runtime
rt = Runtime(model="deepseek:deepseek-chat")

# or, equivalently:
from openprogram.providers.registry import create_runtime
rt = create_runtime(provider="deepseek", model="deepseek-chat")
```

`create_runtime(provider=...)` without a model picks the provider's first enabled model, and raises `ValueError` if the provider has no registered models yet (enable some via Settings → Providers or `openprogram providers available <provider>`).

---

## Custom Providers

All built-in providers are subclasses of `Runtime`. You can create your own in the same way:

```python
from openprogram.agentic_programming.runtime import Runtime

class MyRuntime(Runtime):
    def __init__(self, api_key, model="my-model"):
        super().__init__(model=model)
        self.api_key = api_key

    def _call(self, content, model="default", response_format=None):
        # 1. convert the content blocks into your API's format
        # 2. call the API
        # 3. return a str
        texts = [b["text"] for b in content if b["type"] == "text"]
        return my_api_call("\n".join(texts), model=model)
```

The key point: `_call()` receives `content: list[dict]` and returns a `str`. It's that simple. (Passing `call=fn` to the base `Runtime` achieves the same without a subclass.)
