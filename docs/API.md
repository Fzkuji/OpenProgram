# Python API

## agentic

The `agentic` package provides three core components for building LLM-powered functions with automatic context tracking.

### Decorator

| API | Description |
|-----|-------------|
| [agentic_function](api/agentic_function.md) | Records function execution into the Context tree. |

### Context

| API | Description |
|-----|-------------|
| [Context](api/context.md) | Execution record for one function call. |
| [Context.summarize](api/context.md#summarize) | Query the tree for LLM input. |
| [Context.tree](api/context.md#tree) | Full tree view for debugging. |
| [Context.traceback](api/context.md#traceback) | Error traceback. |
| [Context.save](api/context.md#save) | Save tree to file. |

### Runtime

| API | Description |
|-----|-------------|
| [runtime.exec](api/runtime.md) | Calls an LLM with automatic context injection and recording. |

### Utilities

| API | Description |
|-----|-------------|
| get_context | Get the current Context node. Returns `None` if outside any `@agentic_function`. |
| get_root_context | Get the root of the Context tree. Walks up from the current node. |
| init_root | Manually create a root Context node. Usually not needed. |
