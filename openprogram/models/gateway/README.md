# `openprogram/models/gateway/`

> Model gateway: outside agents run on OpenProgram's models.

## Overview

Coding agents (Claude Code, Codex, and any client of the same APIs) send
their model requests to the Worker under ``/gateway/``; each request is read
into OpenProgram's own message types (``models/providers/types.py``), sent
through the provider layer to the model the request names as an explicit
``provider/model`` reference, and the provider's events are written back in
the client's protocol. The design follows yetone/magpie (MIT License).

  messages.py           Anthropic Messages in and out (Claude Code)
  web_search.py         Anthropic's web search tool on Messages
  responses.py          OpenAI Responses in and out (Codex)
  responses_ws.py       Responses over a WebSocket
  compact.py            /responses/compact, on the provider's own route
  chat_completions.py   OpenAI Chat Completions in and out
  gemini.py             Google's Gemini API in and out (Gemini CLI)
  ollama.py             the Ollama API (VS Code's Copilot and others)
  models_info.py        the model list with what clients read of each model
  conversion.py         shared pieces: model lookup, signatures, tool choice
  stream.py             one upstream call; errors in the client's shape
  attribution.py        which agent a request came from: path, key, User-Agent
  keys.py               the gateway key, and each agent's own (token_for)
  address.py            the Worker address an agent is pointed at
  launch.py             overrides for a harness OpenProgram starts itself
  agents/               the agent registry: one module per agent, the engine
                        that writes and restores their configs, lifecycle
  edit/                 surgical TOML, JSON(C), YAML and dotenv edits
  stash.py              original values kept until they are put back
  codex_app.py          the Codex app's model catalog and thread history

Upstream calls use only the providers configured in OpenProgram and their
own credentials. A credential the client sends authenticates it to the
gateway and is never forwarded.

See docs/reference/design/integrations/model-gateway.html.

## Files in this directory

- **`address.py`** — Where a harness reaches the gateway: this profile's Worker, on loopback
- **`agent_effort.py`** — The reasoning effort picked in OpenProgram for an agent whose own config can't carry one
- **`attribution.py`** — Which agent a gateway request came from, for the usage ledger
- **`chat_completions.py`** — OpenAI Chat Completions (``POST /v1/chat/completions``) in and out
- **`claude_subagent.py`** — Claude Code's Explore and Plan subagents moved onto the model set for its subagents
- **`codex_app.py`** — The Codex app on OpenProgram's models, after magpie
- **`compact.py`** — ``POST /v1/responses/compact``: Codex's remote compaction
- **`conversion.py`** — What the three client protocols share on the way in and out
- **`desktop.py`** — Claude Desktop on the gateway: the model ids it keeps, and its requests
- **`dsh_compact.py`** — DeepSeek Harness's compactions, put on the model picked for them
- **`gemini.py`** — Google's Gemini API (``/v1beta``) in and out, for Gemini CLI and Google's SDKs
- **`kept_picks.py`** — An agent's choices that outlive disconnecting (magpie ``Field.Own``)
- **`keys.py`** — The gateway key: what a harness config authenticates to ``/gateway/`` with
- **`launch.py`** — Launch overrides for a harness OpenProgram starts for a chat
- **`messages.py`** — Anthropic Messages (``POST /v1/messages``) in and out, for Claude Code
- **`models_info.py`** — The gateway's model list, with what clients read about each model
- **`ollama.py`** — The Ollama API, for clients that take a custom endpoint only as an Ollama server
- **`responses.py`** — OpenAI Responses (``POST /v1/responses``) in and out, for Codex
- **`responses_ws.py`** — OpenAI Responses over a WebSocket (``GET /v1/responses`` upgraded)
- **`science.py`** — Claude Science at the gateway: how it is known, listed models and served
- **`stash.py`** — What an agent's config said before OpenProgram pointed it at the gateway
- **`stream.py`** — One upstream call for a gateway request
- **`web_search.py`** — Anthropic's ``web_search`` server tool on ``/v1/messages``

## Sub-packages

- **`agents/`** — The outside agents OpenProgram's gateway can be written into: one registry
- **`edit/`** — Surgical edits of an outside agent's own config files, after magpie's ``internal/edit``

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
