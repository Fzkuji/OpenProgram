# OpenProgram vs LangGraph, AutoGen, and CrewAI

This page compares the public programming models documented by each project. It
is intended to help developers choose an abstraction, not to rank unrelated
features. The comparison was last verified on **2026-08-13** against the linked
official documentation.

| Framework | Documented primary abstraction | Documented orchestration model | Documented emphasis |
|---|---|---|---|
| **OpenProgram** | `@agentic_function` plus an execution runtime | Ordinary control flow, model-selected tools, and a shared execution DAG | Agents that can author reviewable agentic functions; runtime-managed context, tools, memory, interfaces, and multi-agent work |
| **LangGraph** | Stateful graph with nodes and edges | Compile and invoke a graph over shared state | Durable execution, persistence, streaming, and human-in-the-loop control |
| **AutoGen** | AgentChat agents and teams, or Core agents and runtimes | Agent messages, team patterns, and an event-driven Core API | Conversational single/multi-agent applications and scalable multi-agent runtimes |
| **CrewAI** | Agents, tasks, crews, and flows | Role-based agent crews combined with event-driven flows | Collaborative agent teams plus structured workflow automation |

Sources: [OpenProgram Agentic Programming](../capabilities/agentic-programming/README.md),
[LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview),
[AutoGen overview](https://microsoft.github.io/autogen/), and
[CrewAI documentation](https://docs.crewai.com/).

## Choose OpenProgram when

- the workflow should remain normal source code with explicit branches, loops,
  validation, and return values;
- an agent should be able to propose or author a new workflow as a reviewable
  function rather than only select from a fixed graph or team configuration;
- one runtime should provide terminal, Web, model-provider, tool, memory,
  context, and multi-agent surfaces;
- execution context should be represented as a DAG of user, model, function,
  and tool calls.

Start with [Self-Programming AI Agents](../capabilities/agentic-programming/self-programming-ai-agents.md) and
the [OpenProgram installation guide](../start/GETTING_STARTED.md).

## Choose LangGraph when

Your system is most naturally represented as an explicit state graph and the
primary requirements are durable execution, persistence, streaming, and
human-in-the-loop state control. LangGraph describes itself as a low-level
orchestration runtime and recommends higher-level LangChain agents for prebuilt
agent architectures.

Official source: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview).

## Choose AutoGen when

Your design is centered on agents exchanging typed messages or participating in
team patterns. AutoGen exposes a higher-level AgentChat API and a lower-level,
event-driven Core runtime; its documentation covers teams, state management,
human feedback, custom agents, and distributed runtimes.

Official sources: [AutoGen](https://microsoft.github.io/autogen/) and
[Agent and Agent Runtime](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/agent-and-agent-runtime.html).

## Choose CrewAI when

Your application maps clearly to agents with roles, goals, and tasks organized
as a crew, or to structured event-driven flows that invoke crews for autonomous
work. CrewAI documents crews for collaboration and flows for controlled workflow
execution.

Official source: [CrewAI documentation](https://docs.crewai.com/).

## Verification boundary

The table summarizes documented public abstractions; it does not claim that an
unlisted feature is impossible through extensions or custom code. Project APIs
change, so verify the linked documentation before making a long-term migration
decision.
