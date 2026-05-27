# pi-ai reference sources

TypeScript source from [badlogic/pi-mono](https://github.com/badlogic/pi-mono)
(npm package `@mariozechner/pi-ai`, MIT license, © 2025 Mario Zechner) kept
here verbatim as **read-only protocol reference**.

Used to translate into Python for `openprogram/providers/openai_codex.py`:
- OAuth flow endpoints (`auth.openai.com/oauth/token`, `CLIENT_ID`)
- ChatGPT backend endpoint (`chatgpt.com/backend-api/codex/responses`)
- Request body shape (OpenAI Responses API + Codex-specific fields)
- SSE event format
- JWT `chatgpt_account_id` extraction

These files are NOT compiled or imported at runtime. Our Python implementation
rewrites the protocol logic from scratch, using these files only as the
authoritative description of the non-public ChatGPT backend protocol.

## Upstream
- Monorepo: https://github.com/badlogic/pi-mono
- Package path: `packages/ai/`
- License: MIT (see `LICENSE` here)
- Pulled: main branch, 2026-04-19
