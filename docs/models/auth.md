# Authentication

This page covers where provider credentials come from, where they are stored, and how to import them from other CLIs you are already logged in to.

## Storage location

All credentials live in one credential store: `~/.openprogram/auth/<provider>/<profile>.json` (permissions 0600; with `--profile <name>` the root directory becomes `~/.openprogram-<name>/`).

At runtime, keys are **read from the credential store only — environment variables are not read directly**. A key in an environment variable (such as `OPENAI_API_KEY`) must be imported first (see discover below); changing the environment variable afterwards does not affect the imported credential. The two exceptions are cloud credential chains: Amazon Bedrock (`AWS_PROFILE` / access keys / bearer token, etc.) and Google Vertex (ADC), both detected automatically at runtime.

## Credential sources

### API key login

```bash
openprogram providers login deepseek                       # interactive input
printf %s "$KEY" | openprogram providers login deepseek --api-key-stdin   # scripts
```

`--api-key` also accepts the value directly, but it ends up in shell history; prefer `--api-key-stdin` in scripts.

### OAuth login

Subscription providers log in via browser or device code; `login` picks the method automatically (`--method` forces one):

- `anthropic` / `claude-code`: Claude subscription PKCE login in the browser, or paste the output of `claude setup-token` (both write to the same `anthropic` credential pool)
- `openai-codex`: ChatGPT subscription PKCE login in the browser; an existing `codex` CLI login can instead be imported via `discover` / `adopt` below
- `gemini-subscription`: imports `~/.gemini/oauth_creds.json` — log in with the Gemini CLI first
- `github-copilot`: GitHub device-code login in the browser (or import a `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` env var); the short-lived Copilot token is exchanged on demand and never written to disk

### Importing credentials already on this machine

```bash
openprogram providers discover        # scan and list only, writes nothing
openprogram providers adopt codex_cli # import one entry; --all imports everything
```

Scanned sources:

| Source | Location | Imported into |
|---|---|---|
| Codex CLI | `~/.codex/auth.json` | `openai-codex` |
| Qwen CLI | `~/.qwen/oauth_creds.json` | `qwen` |
| gh CLI | `~/.config/gh/hosts.yml` | `github` |
| Environment variables | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. in the process environment | The matching provider |

Imports take one of two forms: while the external CLI is still on the machine, a pointer is stored (the external file is re-read on every call, so tokens the external CLI refreshes itself take effect automatically); otherwise the token is copied into the credential store and OpenProgram handles refreshing.

The Gemini CLI login state does not go through discover: the `google-gemini-cli` provider reads `~/.gemini/oauth_creds.json` directly — install the Gemini CLI, log in, and it works. Claude subscriptions are likewise not on the scan list; use the OAuth login above.

## Management and troubleshooting

```bash
openprogram providers status <provider>    # are the current credentials usable
openprogram providers doctor               # expiry, refresh failures, cooldown, conflicts
openprogram providers logout <provider>    # delete credentials
openprogram providers use <provider> [profile]   # switch between multiple accounts
openprogram providers list                 # list credential pools by profile
```

Every provider supports multiple accounts (named profiles), and one account's credential pool can hold multiple API keys. A key that returns 401 / 402 / 429 / 503 is put on a cooldown and the pool hands the next healthy key to the following request — automatically, with selectable strategies (`fill_first` is the default "backup key" behavior; `round_robin`, `random`, and `least_used` are also available). Rotating across whole accounts (instead of the single active one) is a separate per-provider switch, off by default.
