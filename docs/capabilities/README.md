# Overview

This page answers one question: what OpenProgram can do, and which page covers each capability. Capabilities come in three layers: the programming paradigm, ready-made workflows, and extension mechanisms.

## The Agentic Programming paradigm

OpenProgram is built on Agentic Programming: **Python controls the flow, the LLM provides the reasoning**. You decompose a task into a function call graph — nodes that need no reasoning are plain Python, nodes that need understanding / generation / judgment are decorated with `@agentic_function` and call the model via `runtime.exec(...)` inside the function body. Execution order, state, and retries are ordinary code you can unit-test.

- [Agentic Programming guide](agentic-programming/README.md) — the learning path for writing functions and the three "choose the next step" mechanisms
- [Design philosophy](agentic-programming/philosophy.md) — what problem the paradigm solves and why it inverts control

## Agentic workflows: ready-made agents

Complete workflows written on top of the paradigm (called harnesses / agentic programs in the code), usable right after install: GUI automation, autonomous research, a personal knowledge base. See what is installable with `openprogram programs available`, install with `openprogram programs install <name>`, list the registered functions with `openprogram programs list`; once registered, the functions can be triggered as tools in chat or run directly with `openprogram programs run`.

- [Agentic workflows overview](workflows/README.md)
- [GUI Agent](workflows/gui-agent.md) — give it one task sentence, it operates the desktop autonomously
- [Research Agent](workflows/research-agent.md) — from topic selection to a submittable paper
- [Wiki Agent](workflows/wiki-agent.md) — distills sessions into an HTML knowledge base
- [Installing and writing harnesses](installing-harnesses.md) — the install mechanism and directory contract for third-party harnesses

## Extension mechanisms

Ways to extend the agent's capabilities without writing a harness:

- [Skills](skills.md) — the `SKILL.md` registry: domain knowledge and playbooks the model loads on demand
- [Plugins](plugins.md) — install plugins from pip / npm / git / local paths that contribute commands, skills, MCP servers, and more to the host
- [MCP](mcp.md) — connect any MCP server; its tools appear directly in chat
- [Built-in tools](tools.md) — the tools that ship with the framework (shell, files, web search, images, PDF, etc.) and the keys each one needs
