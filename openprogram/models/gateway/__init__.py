"""Model gateway: third-party coding harnesses run on OpenProgram's models.

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
"""
