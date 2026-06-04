# Install — optional extras

The README lists the available pip extras; this page covers what
each one pulls in and any post-install command it needs.

## Provider SDKs

Installed by default — there is no separate provider extra. `pip install
openprogram` already pulls the three wire SDKs (`anthropic`, `openai`,
`google-genai`) that every provider routes through, so any provider you
pick works out of the box. No post-install.

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

## GUI harness — vision / control deps

The GUI harness's heavy deps are NOT an OpenProgram extra — the harness
declares its own. Install the harness + its deps in one step:

```bash
openprogram programs install gui
```

That clones the [GUI Agent Harness](https://github.com/Fzkuji/GUI-Agent-Harness)
into `functions/agentics/` and `pip install`s the ~2 GB stack it
declares: vision encoders, OCR, screen-capture and input drivers.
Do this only when you actually plan to run `gui_agent`; the base
install is intentionally light.

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
