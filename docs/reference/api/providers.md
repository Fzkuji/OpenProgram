# Providers

> Source: [`openprogram/providers/`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/)

`create_runtime` is the one construction path for every provider. All providers speak the raw HTTP APIs through OpenProgram's provider layer — **no vendor SDK needs to be installed**. The CLI/subscription providers reuse the OAuth credentials of the corresponding CLI tool, so those CLIs must be installed and logged in once.

```bash
# Codex CLI (for the openai-codex provider)
npm install -g @openai/codex && codex login

# Gemini CLI (for the gemini-cli provider)
npm install -g @google/gemini-cli && gemini

# Claude Code CLI (for the claude-code provider — its OAuth token is adopted)
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

Returns a ready-to-use `Runtime`. `provider=None` (or `"auto"`) runs `detect_provider()`. Of the six providers below, the three subscription/CLI-credential ones are backed by a dedicated `Runtime` subclass (an implementation detail of `create_runtime`); the three API-key ones are the base `Runtime` itself — `create_runtime` resolves the key from the credential store and builds `Runtime("<namespace>:<model>", api_key=...)`. **Any other provider name** (deepseek, groq, openrouter, minimax, kimi, and the rest of the community list) is routed through the base `Runtime("provider:model", ...)` via the model registry — the same path the chat dispatcher uses. Every runtime carries an authoritative `provider_id` attribute (derived from its model namespace, or set by the subscription classes). `**kwargs` are forwarded to the runtime constructor.

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

| Provider name | Construction | Default model | Credential |
|------|------|------|------|
| `claude-code` | `ClaudeCodeRuntime` | `claude-sonnet-4` (alias, expanded to the current Sonnet) | Claude subscription OAuth (adopted from Claude Code CLI) |
| `openai-codex` | `OpenAICodexRuntime` | `gpt-5.5` | ChatGPT subscription OAuth (`~/.codex/auth.json`) |
| `gemini-cli` | `GeminiCLIRuntime` | `gemini-2.5-flash` | Google account OAuth (`~/.gemini/oauth_creds.json`) |
| `anthropic` | base `Runtime("anthropic:<id>")` | `claude-sonnet-4-6` | Anthropic API key or adopted subscription OAuth token |
| `openai` | base `Runtime("openai:<id>")` | `gpt-4.1` | OpenAI API key |
| `gemini` | base `Runtime("google:<id>")` | `gemini-2.5-flash` | Google API key |

The three subscription classes live in their provider packages; the API-key providers have no class at all. Construct every provider through `create_runtime(provider=...)`.

---

## anthropic

Anthropic Messages API, via the provider layer (streaming, tool loop, DAG recording all included). `create_runtime` resolves the credential and returns the base `Runtime("anthropic:<model>")` with `provider_id="anthropic"` — there is no dedicated class.

```python
from openprogram.providers.registry import create_runtime

rt = create_runtime(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-4-6")
```

### Options

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. `None` = resolved from the credential store — a stored API key or an adopted Claude-subscription OAuth token (`sk-ant-oat...`, for which the wire switches to Bearer auth automatically) |
| `model` | `str` | `"claude-sonnet-4-6"` | Model id under the `anthropic` provider namespace |
| `max_retries` | `int` | `OPENPROGRAM_MAX_RETRIES` env, else 6 | Retry budget forwarded to the base `Runtime` |

Raises `ValueError` when no credential can be resolved. `list_models()` returns the enabled Anthropic model ids.

---

## openai

OpenAI Responses API, via the provider layer. `create_runtime` resolves the API key and returns the base `Runtime("openai:<model>")` with `provider_id="openai"` — there is no dedicated class.

```python
rt = create_runtime(provider="openai", api_key="sk-...", model="gpt-4.1")
```

### Options

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. `None` = resolved from the credential store (`openprogram providers login openai --api-key`) |
| `model` | `str` | `"gpt-4.1"` | Model id under the `openai` provider namespace |
| `max_retries` | `int` | `OPENPROGRAM_MAX_RETRIES` env, else 6 | Retry budget forwarded to the base `Runtime` |

For Azure or a local OpenAI-compatible server, add a custom provider (Settings → Providers → Add custom provider, name + base URL) and use `Runtime(model="<provider>:<model>")` or `create_runtime(provider="<provider>")`.

---

## gemini

Google Gemini Generative Language API, via the provider layer. `create_runtime` resolves the API key and returns the base `Runtime("google:<model>")` with `provider_id="google"` — there is no dedicated class; the `gemini` provider streams models under the `google` registry namespace.

```python
rt = create_runtime(provider="gemini", api_key="...", model="gemini-2.5-flash")
```

### Options

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key. `None` = resolved from the credential store (accepted env-var names when adding one: `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`) |
| `model` | `str` | `"gemini-2.5-flash"` | Model id under the `google` provider namespace |
| `max_retries` | `int` | `OPENPROGRAM_MAX_RETRIES` env, else 6 | Retry budget forwarded to the base `Runtime` |

---

## claude-code

Claude via a **Claude subscription** — connects directly to `api.anthropic.com` with the subscription's OAuth token (Bearer auth + Claude Code identity headers). No API key billing; the token is resolved fresh on every call so CLI-side rotations propagate. Backed by `ClaudeCodeRuntime`.

```python
rt = create_runtime(provider="claude-code", model="claude-sonnet-4")
```

Setup: log in once with the Claude Code CLI (`claude login`) so the OAuth token can be adopted, or add a Claude account with `openprogram providers claude-code accounts add`.

### Options (forwarded to the runtime)

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | Normally omitted — the token resolves from the credential store on every call. Passing a value pins it (not recommended: subscription tokens expire) |
| `model` | `str` | `"claude-sonnet-4"` | A bare family alias (`claude-opus-4` / `claude-sonnet-4` / `claude-haiku-4`) expands to the current default of that family; any more-specific id (`claude-opus-4-8`, dated ids) is passed through verbatim |
| `max_retries` | `int` | `2` | Retry budget forwarded to the base `Runtime` |

Extra keyword arguments are accepted and ignored for backward compatibility. Raises `ValueError` when no Claude credential exists.

---

## openai-codex

ChatGPT / Codex **subscription** runtime. Reads OAuth credentials adopted from the Codex CLI's `~/.codex/auth.json` and talks to the ChatGPT Responses backend. Refreshed tokens are mirrored back so the Codex CLI stays in sync. Backed by `OpenAICodexRuntime`.

```python
rt = create_runtime(provider="openai-codex", model="gpt-5.5")
```

Setup:

```bash
npm install -g @openai/codex
codex login          # OAuth login — do not pick the API-key option
```

### Options (forwarded to the runtime)

| Parameter | Type | Default | Description |
|------|------|------|------|
| `model` | `str` | `"gpt-5.5"` | Codex model id (a `openai-codex:` prefix, if present, is stripped) |
| `system` | `str \| None` | `None` | Optional system prompt |
| `profile` | `str \| None` | active profile | OpenProgram auth profile to use (keyword-only) |

Extra keyword arguments are accepted and ignored. Requires an OAuth credential — a bare OpenAI API key raises `AuthConfigError` (use the `openai` provider instead).

---

## gemini-cli

Gemini via a **Google account** (Gemini CLI OAuth). Reuses `~/.gemini/oauth_creds.json` and talks to the Cloud Code Assist backend over HTTP — no subprocess. Backed by `GeminiCLIRuntime`.

```python
rt = create_runtime(provider="gemini-cli", model="gemini-2.5-flash")
```

Setup:

```bash
npm install -g @google/gemini-cli
gemini               # first run performs the OAuth login
```

### Options (forwarded to the runtime)

| Parameter | Type | Default | Description |
|------|------|------|------|
| `model` | `str` | `"gemini-2.5-flash"` | Model id; must match a `gemini-subscription/<id>` registry entry |
| `system` | `str \| None` | `None` | Optional system prompt |
| `profile` | `str \| None` | active profile | OpenProgram auth profile to use (keyword-only) |

Extra keyword arguments are accepted and ignored. If you only have a Google API key, use the `gemini` provider instead.

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
