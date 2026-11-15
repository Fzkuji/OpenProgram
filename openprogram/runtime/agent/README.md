# Agent execution

`Agent` configures model calls and automatically records ordinary subclass methods. `Context` controls named content, dynamic providers, session references, and DAG history selection.

```python
from openprogram import Agent, Context

class Researcher(Agent):
    def analyze(self, task):
        return self(task)

researcher = Researcher(context=Context({"topic": "agent memory"}))
researcher.analyze("Compare the methods.")
```

`llm` makes one model request. `agent` runs a tool loop. `decision.make` selects a callable or value. `Runtime` handles providers, tool authorization, request budgets, usage, and model records.

Execution uses the existing Session DAG. Explicit resumable Agent methods use `durable_steps.step`, `workflow`, and `parallel` with JSON state. Configure them through `method_options`; application methods require no decorator.

`agent_class.py` defines Agent configuration. `agent_method.py` implements method options and tool registration. `call_scope.py` manages call lifetime. `call_state.py` stores task-local call state. `tool_format.py` converts method tool specifications to external formats. `agent_function.py` holds `agent()`; `loop/` is the tool loop it runs (`loop.py`, `LoopDriver` in `driver.py`, `AgentSession` in `session.py`, tool and event types, retry); `follow_up_session.py` keeps the `Session` used for questions across CLI invocations. `provider_runtimes/` holds the Runtime subclasses of the subscription providers (Codex, Gemini CLI) and the `PROVIDER_RUNTIMES` table `create_runtime` (`runtime/factory.py`) reads. `runtime/callable_model.py` turns a user `call=fn` into the stream the loop consumes.

`turn_request.py` holds `TurnRequest`, the request a persistent turn runs under, and the request in force for the current context (`get_turn_request`, `inner_turn_request`). `engine_link.py` is the port to the engine above the core: the conversation engine registers itself there when `openprogram.runtime.conversation` is imported, and the core reaches persistent turns (`Agent.run_turn`, `resume_turn`) and their Agent profiles and tools, worker shutdown, background job activity, the pre-approved wait binding and the checkpointed runtime contract only through it. The bound session and execution, nested-call admission and terminal marking come from `openprogram.runtime.execution.run_control`; approval gating and the pre-approved wait id from `openprogram.runtime.permissions`. This package imports no feature package (`conversation`, `jobs`, `agent_registry`, `scheduler`, `proactive`, `commands`). With no engine registered the worker is not stopping, no background job runs, and `Agent.run_turn` raises `engine_link.EngineUnavailable`.
