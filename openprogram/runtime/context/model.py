"""Task-local context content and DAG history selection."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, MutableMapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import inspect
from typing import Any

UNSET = object()
DELETE = object()
_current: ContextVar[Context | None] = ContextVar("openprogram_context", default=None)


HISTORY_MODES = (None, "thread", "chat")


class Context(MutableMapping[str, Any]):
    """Named content with a parent scope and optional history selection.

    Providers must be synchronous callables that accept one Context.
    Local writes persist on this object. Derived scopes copy effective content.
    Content does not change system instructions or tool permissions.

    ``history`` selects the history a model call sees: ``None`` for none,
    ``"thread"`` for the earlier calls made with this same context object,
    ``"chat"`` for the conversation, or a callable ``(node, context) -> bool``
    that selects conversation nodes. Unset, a plain call sees the
    conversation and a workflow role call sees none.
    """

    def __init__(self, blocks: Mapping[str, Any] | None = None, *,
                 parent: Context | None = None,
                 providers: Mapping[str, Callable] | None = None,
                 history: Any = UNSET, call_id: Any = UNSET,
                 store: Any = UNSET, head_id: Any = UNSET,
                 excluded_call_ids: Any = UNSET, thread: Any = UNSET):
        self.parent = parent
        self.excluded_call_ids = frozenset(parent.excluded_call_ids if parent is not None else ()) if excluded_call_ids is UNSET else frozenset(excluded_call_ids)
        self.store = (parent.store if parent is not None else None) if store is UNSET else store
        self.head_id = (parent.head_id if parent is not None else None) if head_id is UNSET else head_id
        self._store_explicit = store is not UNSET or (parent is not None and parent._store_explicit)
        self._head_explicit = head_id is not UNSET or (parent is not None and parent._head_explicit)
        if self.store is not None and not all(callable(getattr(self.store, name, None)) for name in ("load", "append", "update")):
            raise TypeError("Context store must be a SessionNodeWriter.")
        self._blocks = deepcopy(parent._blocks) if parent is not None else {}
        self._providers = dict(parent._providers) if parent is not None else {}
        self._deleted = set(parent._deleted) if parent is not None else set()
        if history is not UNSET and history not in HISTORY_MODES and not callable(history):
            raise TypeError("Context history must be None, 'thread', 'chat', or a callable.")
        self._history_explicit = history is not UNSET or (parent is not None and parent._history_explicit)
        self.history = (parent.history if parent is not None else "chat") if history is UNSET else history
        if thread is not UNSET:
            self._thread = thread
        elif history is not UNSET or parent is None:
            self._thread = []
        else:
            self._thread = parent._thread
        self.call_id = (parent.call_id if parent is not None else None) if call_id is UNSET else call_id
        for name, value in (blocks or {}).items():
            if value is DELETE:
                self._blocks.pop(name, None)
                self._providers.pop(name, None)
                self._deleted.add(name)
            else:
                self[name] = deepcopy(value)
        for name, provider in (providers or {}).items():
            if not callable(provider) or inspect.iscoroutinefunction(provider):
                raise TypeError(f"Context provider {name!r} must be a synchronous callable.")
            self._deleted.discard(name)
            self._providers[name] = provider
            self._blocks.pop(name, None)

    @classmethod
    def for_session(cls, store, session_id: str, *, head_id=UNSET, **options):
        """Select a persistent session without creating or loading a provider."""
        from openprogram.state.store import SessionNodeWriter
        return cls(store=SessionNodeWriter(store, session_id), head_id=head_id, **options)

    @property
    def session_id(self):
        """The selected execution graph, independent of named content."""
        return getattr(self.store, 'session_id', None)

    @classmethod
    def turn_store(cls, session_id):
        """Resolve a turn's store from its explicit Context or the default."""
        current = cls.current()
        if current is not None and current.store is not None:
            if current.session_id != session_id:
                raise ValueError('Context belongs to a different session.')
            return current.store.store
        from openprogram.runtime.context.session_db import default_db
        return default_db()

    def render_blocks(self):
        """Resolve named content once for a provider request."""
        import json
        from openprogram.models.providers.types import TextContent
        blocks = []
        for name, value in self.resolve_blocks().items():
            if value is None or value == '':
                continue
            value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            blocks.append(TextContent(text=f'{name}:\n{value}'))
        return blocks

    def add_to_messages(self, messages, *, blocks=None):
        """Add named content to a user message, never system authority."""
        from openprogram.models.providers.types import TextContent
        blocks = self.render_blocks() if blocks is None else blocks
        result = list(messages)
        if blocks:
            for index in range(len(result) - 1, -1, -1):
                message = result[index]
                if getattr(message, 'role', None) == 'user':
                    content = list(message.content) if isinstance(message.content, list) else [TextContent(text=message.content)]
                    result[index] = message.model_copy(update={'content': blocks + content})
                    break
        return result

    def filter_history(self, history):
        """Narrow an already authorized branch before model preparation."""
        if self.history in (None, "thread"):
            return []
        if self.store is None or (self.history == "chat" and not self.excluded_call_ids):
            return list(history)
        graph = self.store.load()
        ids = [row['id'] for row in history if row.get('id') in graph.nodes]
        selected = set(self.select_history(graph, ids))
        return [row for row in history if row.get('id') in selected]

    @classmethod
    def current(cls) -> Context | None:
        """Return the context bound to this task."""
        return _current.get()

    def derive(self, blocks: Mapping[str, Any] | None = None, *,
               providers: Mapping[str, Callable] | None = None,
               history: Any = UNSET, call_id: Any = UNSET,
               store: Any = UNSET, head_id: Any = UNSET,
               excluded_call_ids: Any = UNSET, thread: Any = UNSET) -> Context:
        """Create a child with an independent content snapshot.

        The child shares this context's thread unless ``history`` is given,
        which starts a new one."""
        return type(self)(blocks, parent=self, providers=providers,
                          history=history, call_id=call_id, store=store, head_id=head_id,
                          excluded_call_ids=excluded_call_ids, thread=thread)

    @property
    def thread(self) -> list[dict]:
        """The ``{"role", "text"}`` turns recorded by calls made with this context."""
        return self._thread

    def record_turn(self, prompt: Any, reply: Any) -> None:
        """Add one call's prompt and reply to the thread."""
        if self.history != "thread":
            return
        self._thread.append({"role": "user", "text": _turn_text(prompt)})
        self._thread.append({"role": "assistant", "text": _turn_text(reply)})

    def merge(self, other: Context) -> Context:
        """Create a child with content and selection from another context."""
        blocks = {name: DELETE for name in other._deleted}
        blocks.update(other._blocks)
        return self.derive(blocks, providers=other._providers,
                           history=other.history if other._history_explicit else UNSET,
                           thread=other._thread if other._history_explicit else UNSET,
                           store=other.store if other._store_explicit else UNSET,
                           head_id=other.head_id if other._head_explicit else UNSET)

    @contextmanager
    def bind(self):
        """Bind this context and restore the previous task binding."""
        from openprogram.state.store import _store, SessionNodeWriter
        from openprogram.runtime.context.scope import _call_id, _forced_predecessor

        with ExitStack() as stack:
            writer = _store.get()
            if self.store is not None:
                if not isinstance(self.store, SessionNodeWriter):
                    raise TypeError("Context store must be a SessionNodeWriter.")
                if writer is not None and writer is not self.store:
                    raise ValueError("Context store differs from the active session writer.")
                if writer is None:
                    writer = self.store
                    token = _store.set(writer)
                    stack.callback(_store.reset, token)
            if self.head_id is not None:
                if writer is None or self.head_id not in writer.load().nodes:
                    raise ValueError(f"Context head {self.head_id!r} does not exist.")
                if not _call_id.get() and _forced_predecessor.get() != self.head_id:
                    token = _forced_predecessor.set(self.head_id)
                    stack.callback(_forced_predecessor.reset, token)
            token = _current.set(self)
            stack.callback(_current.reset, token)
            try:
                yield self
            except GeneratorExit:
                # An abandoned producer's generator may be collected in a
                # different Context. Those tokens cannot reset that Context;
                # close all callbacks without an unraisable GC exception.
                try:
                    stack.close()
                except ValueError:
                    pass
                raise

    def __getitem__(self, name: str) -> Any:
        if name in self._providers:
            return self._providers[name]
        return self._blocks[name]

    def __setitem__(self, name: str, value: Any) -> None:
        if not isinstance(name, str):
            raise TypeError("Context block names must be strings.")
        self._deleted.discard(name)
        self._providers.pop(name, None)
        self._blocks[name] = value

    def __delitem__(self, name: str) -> None:
        if name in self._providers:
            del self._providers[name]
        else:
            del self._blocks[name]
        self._deleted.add(name)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.fromkeys((*self._blocks, *self._providers)))

    def __len__(self) -> int:
        return len(set(self._blocks) | set(self._providers))

    def resolve_blocks(self) -> dict[str, Any]:
        """Resolve and freeze named content for one request."""
        result = deepcopy(self._blocks)
        with self.bind():
            for name, provider in self._providers.items():
                try:
                    value = provider(self)
                    if inspect.isawaitable(value):
                        if inspect.iscoroutine(value):
                            value.close()
                        raise TypeError("The provider returned an awaitable.")
                    result[name] = deepcopy(value)
                except Exception as exc:
                    error = RuntimeError(f"Context provider {name!r} failed: {exc}")
                    error.retryable = False
                    raise error from exc
        return result

    def select_history(self, graph, read_ids: list[str], *, call_id: str | None = None) -> list[str]:
        """Filter IDs already selected by the DAG visibility policy.

        It cannot restore IDs excluded by DAG authority, branch, or
        render-range rules.
        """
        scope_id = self.call_id or call_id
        excluded = set(self.excluded_call_ids)
        scope_node = graph.nodes.get(scope_id)
        task_id = (scope_node.metadata or {}).get("task_id") if scope_node is not None else None
        if task_id:
            branch = scope_node
            visited = set()
            while branch.caller and branch.caller not in visited:
                visited.add(branch.id)
                parent = graph.nodes.get(branch.caller)
                if parent is None or (parent.metadata or {}).get("task_id") != task_id:
                    break
                branch = parent
            ancestors = set()
            current = branch.caller
            while current and current not in ancestors:
                ancestors.add(current)
                node = graph.nodes.get(current)
                current = node.caller if node is not None else None
            # A sibling task can start after this branch entered. Include
            # those new sibling roots in the exclusion snapshot at read time.
            for node in graph.nodes.values():
                other_task = (node.metadata or {}).get("task_id")
                if other_task and other_task != task_id and node.caller in ancestors and node.seq >= branch.seq:
                    excluded.add(node.id)
        def selected(node_id: str) -> bool:
            current = node_id
            seen = set()
            while current and current not in seen:
                if current in excluded:
                    return False
                seen.add(current)
                node = graph.nodes.get(current)
                current = node.caller if node is not None else None
            if callable(self.history):
                value = self.history(graph.nodes[node_id], self)
                if inspect.isawaitable(value):
                    if inspect.iscoroutine(value):
                        value.close()
                    raise TypeError("History selector must return a synchronous value.")
                return bool(value)
            return self.history == "chat"
        return list(dict.fromkeys(node_id for node_id in read_ids if selected(node_id)))


def _turn_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(block.get("text", "")) for block in value
                         if isinstance(block, dict) and block.get("type") == "text")
    import json
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)
