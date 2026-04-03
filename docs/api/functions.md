# Module Functions

## get_context

```python
agentic.get_context() -> Context | None
```

Get the currently active [Context](context.md) node.

Returns `None` if called outside any [@agentic_function](agentic_function.md).

```python
from agentic import agentic_function, get_context

@agentic_function
def my_function():
    ctx = get_context()
    print(ctx.name)      # "my_function"
    print(ctx.params)    # {}
```

---

## get_root_context

```python
agentic.get_root_context() -> Context | None
```

Get the root of the current [Context](context.md) tree. Walks up from the active node via `parent` links.

Returns `None` if called outside any [@agentic_function](agentic_function.md).

```python
from agentic import agentic_function, get_root_context

@agentic_function
def my_function():
    root = get_root_context()
    print(root.tree())   # full tree from root
```

---

## init_root

```python
agentic.init_root(name="root") -> Context
```

Manually create a root [Context](context.md) node and set it as the current context.

Usually not needed — [@agentic_function](agentic_function.md) creates the root automatically when the first decorated function is called.

```python
from agentic import init_root

root = init_root("my_session")
# Now any @agentic_function call will attach under this root.
```
