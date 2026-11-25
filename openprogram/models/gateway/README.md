# `openprogram/models/gateway/`

> Model gateway: third-party coding harnesses run on OpenProgram's models.

## Overview

Claude Code and Codex (and any client of the same APIs) send their model
requests to the Worker under ``/gateway/``; each request is read into
OpenProgram's own message types (``models/providers/types.py``), sent
through the provider layer to the model the request names as an explicit
``provider/model`` reference, and the provider's events are written back
in the client's protocol.

  messages.py           Anthropic Messages in and out (Claude Code)
  responses.py          OpenAI Responses in and out (Codex)
  chat_completions.py   OpenAI Chat Completions in and out
  conversion.py         shared pieces: model lookup, signatures, tool choice
  stream.py             one upstream call; errors in the client's shape
  keys.py               the gateway key harness configs authenticate with
  address.py            the Worker address a harness is pointed at
  launch.py             overrides for a harness OpenProgram starts itself
  config_files.py       surgical TOML and JSON edits of a harness's config
  stash.py              original values kept until they are put back
  harness_config.py     "Also in the terminal": write and restore configs

Upstream calls use only the providers configured in OpenProgram and their
own credentials. A credential the client sends authenticates it to the
gateway and is never forwarded.

See docs/reference/design/integrations/model-gateway.html.

## Files in this directory

- **`address.py`** — Where a harness reaches the gateway: this profile's Worker, on loopback
- **`chat_completions.py`** — OpenAI Chat Completions (``POST /v1/chat/completions``) in and out
- **`codex_app.py`** — The Codex app on OpenProgram's models, after magpie
- **`config_files.py`** — Surgical edits of a harness's own config file: TOML (Codex) and JSON (Claude Code)
- **`conversion.py`** — What the three client protocols share on the way in and out
- **`harness_config.py`** — "Also in the terminal": the gateway written into a harness's own config
- **`keys.py`** — The gateway key: what a harness config authenticates to ``/gateway/`` with
- **`launch.py`** — Launch overrides for a harness OpenProgram starts for a chat
- **`messages.py`** — Anthropic Messages (``POST /v1/messages``) in and out, for Claude Code
- **`responses.py`** — OpenAI Responses (``POST /v1/responses``) in and out, for Codex
- **`stash.py`** — Original config values, kept until they are put back
- **`stream.py`** — One upstream call for a gateway request

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
