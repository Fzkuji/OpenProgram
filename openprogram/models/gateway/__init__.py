"""Model gateway: outside agents run on OpenProgram's models.

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
"""
