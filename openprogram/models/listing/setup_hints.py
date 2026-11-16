"""Provider-specific setup instructions surfaced in the web UI's
detail pane.

Plain text with two minor conventions the React renderer
(``apps/web/components/settings/providers/auth/setup-hint.tsx``) recognises:

  * Backticked spans render as inline ``<code>``.
  * ``**bold**`` renders as ``<strong>``.
  * Lines beginning with ``$`` render as a copy-able command row.
  * Consecutive non-blank, non-command lines collapse into one
    paragraph (so the source can stay hard-wrapped at ~72 chars
    without forcing visible mid-sentence line breaks in the UI).

Add a new provider here when its setup needs more than the generic
"paste your API key" flow — OAuth providers, daemon-backed providers,
or anything with platform-specific gotchas. The catalog serialiser
will surface the entry under ``provider.setup_hint``; the provider page
shows it until the provider has a credential (a local server's always),
so it never repeats what the Credentials list already shows.
"""
from __future__ import annotations


_SETUP_HINTS: dict[str, str] = {
    **{pid: "Start your inference server and load a model first. Save its API Base URL, fetch models, and enable the model you want. API keys are optional; add one only when the server requires authentication. localhost refers to the worker machine. Set model token limits to match the server configuration." for pid in ("ollama", "lmstudio", "vllm", "llamacpp", "local")},
    "openai-codex": (
        "OpenAI Codex runs on a ChatGPT sign-in, not an API key. Click\n"
        "**Sign in with ChatGPT** under Credentials and approve in the browser\n"
        "tab that opens, or from a terminal:\n"
        "\n"
        "$ openprogram providers keys openai-codex add\n"
        "\n"
        "A Codex CLI login (`~/.codex/auth.json`) is picked up on its own and\n"
        "listed under Credentials. Requests go to `chatgpt.com`, so that host\n"
        "must be reachable. For a pay-per-token OpenAI API key, use the\n"
        "**OpenAI** provider instead."
    ),
    "anthropic": (
        "Anthropic is reached with an API key from the Console:\n"
        "\n"
        "$ open https://console.anthropic.com/settings/keys\n"
        "\n"
        "Add it under Credentials, or set `ANTHROPIC_API_KEY` where\n"
        "OpenProgram starts; that key is listed under Credentials too.\n"
        "OpenProgram uses Anthropic only through this official API, billed\n"
        "per token to the API account."
    ),
    "xai-subscription": (
        "This is the SuperGrok / X Premium+ provider, not the xAI API key.\n"
        "Click **Sign in with Grok** under Credentials and approve with the\n"
        "account that holds the subscription. For pay-per-token, use the\n"
        "**xAI** provider instead."
    ),
    "gemini-subscription": (
        "Gemini CLI reuses your Gemini subscription through Google's `gemini`\n"
        "CLI. Install it and sign in there once; OpenProgram imports\n"
        "`~/.gemini/oauth_creds.json` from Credentials:\n"
        "\n"
        "$ npm install -g @google/gemini-cli\n"
        "$ gemini\n"
        "\n"
        "For a pay-per-token Google AI Studio key, use the **Google AI**\n"
        "provider instead."
    ),
    "github-copilot": (
        "GitHub Copilot uses your Copilot subscription. Click **Sign in with\n"
        "GitHub** under Credentials, enter the code it shows on the GitHub\n"
        "page that opens, and approve. If the plan lapses, sign in again."
    ),
    "deepseek": (
        "DeepSeek takes an API key from its platform:\n"
        "\n"
        "$ open https://platform.deepseek.com/api_keys\n"
        "\n"
        "Add it under Credentials, or from a terminal with\n"
        "`openprogram providers keys deepseek add`."
    ),
}


def _setup_hint(provider_id: str) -> str | None:
    return _SETUP_HINTS.get(provider_id)
