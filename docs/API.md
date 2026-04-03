# Python API

## agentic

| API | Description |
|-----|-------------|
| [agentic_function](api/agentic_function.md) | Decorator. Records function execution into the Context tree. |
| [Context](api/context.md) | Execution record for one function call. |
| [runtime.exec](api/runtime.md) | Calls an LLM with automatic context injection and recording. |
| [get_context](api/functions.md#get_context) | Get the current Context node. |
| [get_root_context](api/functions.md#get_root_context) | Get the root of the Context tree. |
| [init_root](api/functions.md#init_root) | Manually create a root Context node. |
