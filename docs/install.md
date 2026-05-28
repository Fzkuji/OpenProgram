# Install — optional extras

The README lists the available pip extras; this page covers what
each one pulls in and any post-install command it needs.

## Provider SDKs

```bash
pip install "openprogram[anthropic]"    # Anthropic Claude SDK
pip install "openprogram[openai]"       # OpenAI / Codex SDK
pip install "openprogram[gemini]"       # Google GenAI SDK
```

No post-install. Required only if you call the API directly —
the CLI-based providers (Claude Code / Codex / Gemini CLI) work
without these.

## `[browser]` — Playwright

```bash
pip install "openprogram[browser]"
playwright install chromium
```

Adds ~150 MB of Playwright runtime. Needed by the
`browser` / `agent_browser` tools and by any
`@agentic_function` that opens a real browser.

## `[browser-stealth]` — Cloudflare-bypassing browsers

```bash
pip install "openprogram[browser-stealth]"
patchright install chromium
camoufox fetch
```

Adds Patchright and Camoufox alongside Playwright. Picks one of
them automatically when the site under `browser` sets
Cloudflare turnstile.

## `[gui]` — Vision / control deps for the GUI harness

```bash
pip install "openprogram[gui]"
```

Adds the ~2 GB stack the [GUI Agent Harness](https://github.com/Fzkuji/GUI-Agent-Harness)
expects: vision encoders, OCR, screen-capture and input
drivers. Install this only when you actually plan to run
`gui_agent`; the base install is intentionally light.

## `[channels]` — Discord / Slack / WeChat bots

```bash
pip install "openprogram[channels]"
```

Adds the SDKs for the messenger bots that bridge
chat / fn-form into Discord (today), Slack, WeChat (in
progress). The bots themselves are wired in via
`openprogram channels` settings.

## `[all]` — Everything except `[browser-stealth]`

```bash
pip install "openprogram[all]"
playwright install chromium
```

Convenient single-line install. Skips `[browser-stealth]`
because the extra installs are slow and aren't always wanted.

## Local-dev install across multiple repos

For working on
[GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)
/
[Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness)
side-by-side with OpenProgram, see
[troubleshooting.md → Local-development install](troubleshooting.md#local-development-install-multi-repo).
