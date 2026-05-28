"""Provider-specific setup instructions surfaced in the web UI's
detail pane.

Plain text with two minor conventions the React renderer
(``web/components/settings/providers/setup-hint.tsx``) recognises:

  * Backticked spans render as inline ``<code>``.
  * ``**bold**`` renders as ``<strong>``.
  * Lines beginning with ``$`` render as a copy-able command row.
  * Consecutive non-blank, non-command lines collapse into one
    paragraph (so the source can stay hard-wrapped at ~72 chars
    without forcing visible mid-sentence line breaks in the UI).

Add a new provider here when its setup needs more than the generic
"paste your API key" flow — OAuth providers, daemon-backed providers,
or anything with platform-specific gotchas. The catalog serialiser
will surface the entry under ``provider.setup_hint`` and the React
side renders it above the API-key field.
"""
from __future__ import annotations


_SETUP_HINTS: dict[str, str] = {
    "claude-code": (
        "Claude Code auth lives in the Claude Code keychain — there's no API\n"
        "key to paste here. Install the Claude Code CLI, sign in once, install\n"
        "the Meridian proxy daemon, then keep `meridian` running while you use\n"
        "this provider:\n"
        "\n"
        "$ npm install -g @anthropic-ai/claude-code\n"
        "$ claude auth login\n"
        "$ npm install -g @rynfar/meridian --ignore-scripts\n"
        "$ meridian\n"
        "\n"
        "`meridian` is a plain foreground daemon — don't close that terminal\n"
        "while you're using Claude here. To stop it just `Ctrl+C` or close\n"
        "the window; run `meridian` again next time you want Claude back.\n"
        "Deliberately not wrapped in a service / scheduled task — the\n"
        "lifecycle stays yours.\n"
        "\n"
        "Windows: pass `--ignore-scripts` to the Meridian install. Its\n"
        "postinstall hook calls a POSIX one-liner (`2>/dev/null || true`)\n"
        "that crashes under `cmd.exe`; skipping it doesn't affect runtime.\n"
        "Older docs also say `claude login`; the current CLI moved it to\n"
        "`claude auth login`.\n"
        "\n"
        "Meridian listens on `http://127.0.0.1:3456` by default. Override with\n"
        "`CLAUDE_MAX_PROXY_URL=http://host:port` if 3456 is taken.\n"
        "\n"
        "Once the daemon is up, this section flips to \"Detected\" and you\n"
        "can enable the provider above. The proxy exposes an OpenAI-\n"
        "compatible `/v1/chat/completions` endpoint and routes traffic\n"
        "through your Claude Code OAuth session, so no extra API key is\n"
        "needed.\n"
        "\n"
        "Legacy: the older `claude-max-api-proxy` package also works on the\n"
        "same port for text-only traffic, but mangles multipart image\n"
        "content — prefer Meridian for any agent that sends screenshots\n"
        "(e.g. `gui_agent`)."
    ),
    "openai-codex": (
        "OpenAI Codex reuses your ChatGPT Plus / Pro / Team / Enterprise\n"
        "subscription via OAuth — there's no API key to paste here.\n"
        "\n"
        "Run the PKCE login from a terminal. A browser tab will open to\n"
        "`auth.openai.com`; sign in with the account that holds your\n"
        "subscription and approve the scope:\n"
        "\n"
        "$ openprogram providers login openai-codex --method pkce_oauth\n"
        "\n"
        "The callback lands on `localhost:1455` — don't have another `codex`\n"
        "or `pi-ai` process holding that port. Tokens are saved to\n"
        "`~/.openprogram/openai-codex/default.json` and auto-refreshed.\n"
        "\n"
        "Once login completes this section flips to \"Configured\" and the\n"
        "Connectivity probe below will go green. Requests stream against\n"
        "`chatgpt.com/backend-api/codex/responses`, so traffic to that host\n"
        "must be reachable from your network — corporate proxies that block\n"
        "consumer ChatGPT will block Codex too.\n"
        "\n"
        "If you're on a bare OpenAI API key (pay-per-token) instead of a\n"
        "ChatGPT subscription, use the regular **OpenAI** provider instead\n"
        "of this one — they're separate billing paths."
    ),
    "anthropic": (
        "Anthropic API uses a static key issued from the Console:\n"
        "\n"
        "$ open https://console.anthropic.com/settings/keys\n"
        "\n"
        "Create a key, then either paste it into the field below or set\n"
        "the env var `ANTHROPIC_API_KEY=sk-ant-...` and restart\n"
        "`openprogram --web`. Either source works; the field takes\n"
        "precedence when both are set.\n"
        "\n"
        "This is the metered pay-per-token path. If you have an\n"
        "Anthropic Pro / Team subscription and want to route through\n"
        "your existing Claude session instead, use the **Claude Code**\n"
        "provider (Meridian proxy, no API key needed)."
    ),
    "gemini-subscription": (
        "Gemini CLI reuses your Gemini Advanced / Workspace subscription\n"
        "via Google Cloud Code Assist — no API key to paste.\n"
        "\n"
        "Install Google's `gemini` CLI and log in there once. OpenProgram\n"
        "auto-detects ``~/.gemini/oauth_creds.json`` and refreshes via\n"
        "the bundled refresh flow:\n"
        "\n"
        "$ npm install -g @google/gemini-cli\n"
        "$ gemini\n"
        "$ openprogram providers discover\n"
        "$ openprogram providers adopt gemini_cli\n"
        "\n"
        "If you only want a bare Google AI Studio API key instead\n"
        "(pay-per-token), use the **Google AI** provider — that one\n"
        "takes `GOOGLE_GENERATIVE_AI_API_KEY` and skips the OAuth dance."
    ),
    "github-copilot": (
        "GitHub Copilot uses your existing Copilot subscription's OAuth\n"
        "token — no API key, no separate billing.\n"
        "\n"
        "Log in via the GitHub CLI once and OpenProgram will pick the\n"
        "token up automatically:\n"
        "\n"
        "$ winget install GitHub.cli         # or: brew install gh\n"
        "$ gh auth login --scopes copilot\n"
        "$ openprogram providers discover\n"
        "$ openprogram providers adopt github_copilot\n"
        "\n"
        "Subscription state is checked on every request — the moment\n"
        "your Copilot trial / plan lapses the connectivity check goes\n"
        "red here. Re-running `gh auth refresh` is enough to recover."
    ),
    "deepseek": (
        "DeepSeek's first-party API at `api.deepseek.com` is OpenAI-\n"
        "compatible, so this is the standard paste-an-API-key flow:\n"
        "\n"
        "$ open https://platform.deepseek.com/api_keys\n"
        "\n"
        "Create a key, paste it into the field below, or set the env var\n"
        "`DEEPSEEK_API_KEY=sk-...` and restart `openprogram --web`. Either\n"
        "source works; the field wins when both are set.\n"
        "\n"
        "Two models register out of the box, both V3.2-Exp on a 128K\n"
        "context: `deepseek-chat` and `deepseek-reasoner` (same base, the\n"
        "reasoner has thinking-trace output enabled). Click **Fetch\n"
        "models** if you want to refresh from `api.deepseek.com/v1/models`\n"
        "— useful if DeepSeek ships a new model and we haven't.\n"
        "\n"
        "Hosted in mainland China; the API gateway is reachable without\n"
        "a VPN from CN ISPs, unlike ChatGPT / Anthropic. Pricing is also\n"
        "an order of magnitude lower than OpenAI's o-class models, with\n"
        "cache-hit discounts on repeated prompts."
    ),
}


def _setup_hint(provider_id: str) -> str | None:
    return _SETUP_HINTS.get(provider_id)
