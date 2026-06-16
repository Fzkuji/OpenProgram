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
        "Runs on your Claude subscription — no API key, nothing to install.\n"
        "OpenProgram connects straight to Anthropic with your subscription's\n"
        "OAuth token (just like the OpenAI Codex card uses your ChatGPT login).\n"
        "\n"
        "**Set it up in the Claude accounts section below — pick ONE way:**\n"
        "\n"
        "**A) Sign in with Claude subscription (recommended).**\n"
        "1. Click **Sign in with Claude subscription** — it opens claude.ai in\n"
        "your browser.\n"
        "2. Log in (if needed) and click **Authorize**.\n"
        "3. The page shows a code that looks like `xxxxx#yyyyy` — copy the WHOLE\n"
        "string, including the `#` and everything after it.\n"
        "4. Paste it into the box that appeared in OpenProgram and confirm. Done\n"
        "— the account shows your email and a green **VALID**.\n"
        "\n"
        "**B) Paste a `claude setup-token` (no browser).**\n"
        "1. In a terminal run `claude setup-token` (needs the Claude Code CLI).\n"
        "2. Copy the `sk-ant-oat…` token it prints.\n"
        "3. Click **Paste a `claude setup-token`**, paste the token, confirm.\n"
        "(Note: setup-tokens don't auto-renew — re-mint roughly once a year.)\n"
        "\n"
        "**After adding:** click **Fetch models** to pull the current Claude\n"
        "model list. Add more accounts the same way to switch between them or\n"
        "rotate automatically. This is independent from any terminal `claude`\n"
        "login — changing it here never touches your CLI."
    ),
    "openai-codex": (
        "OpenAI Codex runs on your Codex CLI login — there's no API key to\n"
        "paste here. If you've already run `codex` and signed in (either a\n"
        "ChatGPT Plus / Pro / Team / Enterprise subscription OR an OpenAI API\n"
        "key), OpenProgram adopts that login read-only from\n"
        "`~/.codex/auth.json` automatically — this card shows \"Configured\" on\n"
        "its own, and changing your Codex CLI login here takes effect\n"
        "immediately (same file). Note an API-key login still has to have\n"
        "billing/quota on that OpenAI account or requests fail with a quota\n"
        "error even though sign-in is \"valid\".\n"
        "\n"
        "To run OpenProgram on a DIFFERENT account than your Codex CLI\n"
        "(independent of `~/.codex`), run the PKCE login from a terminal. A\n"
        "browser tab opens to `auth.openai.com`; sign in with the account that\n"
        "holds your subscription and approve the scope:\n"
        "\n"
        "$ openprogram providers login openai-codex --method pkce_oauth\n"
        "\n"
        "That callback lands on `localhost:1455` — don't have another `codex`\n"
        "or `pi-ai` process holding that port. Those tokens are saved\n"
        "separately to `~/.openprogram/openai-codex/default.json` and\n"
        "auto-refreshed.\n"
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
        "`openprogram web`. Either source works; the field takes\n"
        "precedence when both are set.\n"
        "\n"
        "This is the metered pay-per-token path. If you have an\n"
        "Anthropic Pro / Team subscription and want to route through\n"
        "your existing Claude session instead, use the **Claude Code**\n"
        "provider (no API key needed)."
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
        "$ openprogram providers login gemini-subscription\n"
        "\n"
        "If you only want a bare Google AI Studio API key instead\n"
        "(pay-per-token), use the **Google AI** provider — that one\n"
        "takes `GOOGLE_GENERATIVE_AI_API_KEY` and skips the OAuth dance."
    ),
    "github-copilot": (
        "GitHub Copilot uses your existing Copilot subscription's OAuth\n"
        "token — no API key, no separate billing.\n"
        "\n"
        "Sign in through the browser (device code) and OpenProgram saves\n"
        "and refreshes the token for you:\n"
        "\n"
        "$ openprogram providers login github-copilot\n"
        "\n"
        "A short code appears; open the GitHub URL it prints, enter the\n"
        "code, and approve. Subscription state is checked on every request —\n"
        "the moment your Copilot plan lapses the connectivity check goes red\n"
        "here; just run the login again to recover."
    ),
    "deepseek": (
        "DeepSeek's first-party API at `api.deepseek.com` is OpenAI-\n"
        "compatible, so this is the standard paste-an-API-key flow:\n"
        "\n"
        "$ open https://platform.deepseek.com/api_keys\n"
        "\n"
        "Create a key and paste it into the field below (or from a\n"
        "terminal: `openprogram providers login deepseek --api-key`).\n"
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
