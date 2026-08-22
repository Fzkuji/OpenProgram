"""Turn-scoped context bindings — pipeline step 3 (dispatcher-split).

Attach a Runtime with the session's GraphStore so any @agentic_function
the agent_loop invokes records its placeholder / internal / exit nodes
into the same DAG. The Runtime is shared via the ``_current_runtime``
ContextVar that @agentic_function's _inject_runtime consults.

``TurnBindings.bind`` sets every per-turn ContextVar (session id, turn
id, worktree cwd, GraphStore, DAG runtime), installs the session-scoped
deferred-tool set and snapshots the project auto-commit baseline.
``release`` resets the tokens — the caller runs it in a ``finally`` so
success, exception and early-return paths all unwind identically.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from openprogram.agent.run_control import (
    get_current_session_id as _get_session_id,
    reset_current_session_id as _reset_session_id,
    set_current_session_id as _set_session_id,
)

if TYPE_CHECKING:
    from openprogram.agent.dispatcher.types import TurnRequest

_log = logging.getLogger(__name__)


class TurnBindings:
    """Holds the ContextVar tokens for one turn, plus the project baseline."""

    def __init__(self) -> None:
        self.project_baseline = None
        self._runtime_token = None
        self._store_token = None
        self._turn_id_token = None
        self._worktree_token = None
        self._session_id_token = None
        self._turn_request_token = None
        self._render_range_token = None
        self._surface_token = None
        self._web_use_owner_id = None
        self._sandbox_token = None
        self._req_session_id: Optional[str] = None

    @classmethod
    def bind(cls, *, req: "TurnRequest", assistant_msg_id: str, db) -> "TurnBindings":
        self = cls()
        self._req_session_id = req.session_id
        # Critical: we use ``create_runtime()`` (real provider) instead
        # of a stub. @agentic_function's _inject_runtime would otherwise
        # pick up our stub and any ``runtime.exec`` inside the function
        # body would return whatever the stub's ``call`` does (a fixed
        # string or empty) rather than actually calling an LLM. If
        # real-runtime construction fails (e.g. no provider configured),
        # fall back to NOT setting _current_runtime so @agentic_function
        # can create its own runtime as before — DAG persistence
        # gracefully degrades to off for this turn.
        from openprogram.store import (
            SessionNodeWriter as _GraphStore,
            _store as _store_var,
            _current_turn_id as _turn_id_var,
        )
        from openprogram.agentic_programming.function import (
            _current_runtime as _current_runtime_var,
            _render_range_override as _render_range_var,
        )
        # Tag this turn so file-mutating tools can attribute backups to
        # the right assistant message via checkpoint.helpers.
        self._turn_id_token = _turn_id_var.set(assistant_msg_id)
        # The execution context an inner AgentSession inherits. A program
        # spawned from this turn derives its own request from this one, so
        # its tools carry the same authority, source and permission mode
        # rather than running ungated (see turn_request_context).
        from openprogram.agent.turn_request_context import set_turn_request
        self._turn_request_token = set_turn_request(req)
        # The session half of the pair every collaboration tool reads
        # (agent, send_message, todo, worktree_*, read_conversation).
        # Their error text already promises "the dispatcher sets the
        # session + turn ContextVars on entry", and until now only the
        # turn half was true here: the session id was bound by whichever
        # entry point happened to run the turn. A new thread starts with
        # empty ContextVars, so the paths that call process_user_turn off
        # their own thread (the job runner's follow-up, merge, the CLI
        # /goal turn) left it unbound and every one of those tools failed
        # with "no active parent turn".
        #
        # Fill in only when nothing is bound. An entry point that binds
        # the id for a scope WIDER than the turn (webui exec thread, task
        # runner worker, channel adapter) stays in charge of it, so a
        # nested turn for another session cannot repoint the cancel hook
        # or runtime.ask at a session that registered no turn token.
        if _get_session_id() is None:
            self._session_id_token = _set_session_id(req.session_id)
        # Bind the session's active agent worktree (if any) to the
        # _current_worktree_path ContextVar for the duration of this turn.
        # bash / edit / write / read consult that var to default their cwd
        # to the worktree root. The binding is per-turn so a worktree
        # created mid-turn is picked up by the next turn entry — within
        # the same turn the tool that ran worktree_create also calls
        # set_worktree explicitly so the rest of that turn sees it.
        try:
            from openprogram.worktree.context import set_worktree as _set_wt
            from openprogram.worktree.manager import get_manager as _get_wt_mgr
            _wt_mgr = _get_wt_mgr()
            _active_wt = _wt_mgr.find_active_for_session(req.session_id)
            if _active_wt is not None:
                self._worktree_token = _set_wt(_active_wt.worktree_path)
            else:
                # No explicit worktree → default this turn's tool cwd to the
                # session's bound (non-default) project path. Resolved fresh
                # every turn, so a mid-chat set_session_project takes effect
                # on the next turn without moving the session repo.
                from openprogram.agent.internals._workdir import project_workdir_for
                _proj_wd = project_workdir_for(req.session_id)
                if _proj_wd is not None:
                    self._worktree_token = _set_wt(str(_proj_wd))
        except Exception:
            self._worktree_token = None
        # Layer 6 (Claude Code's shouldDefer / ToolSearch): install a
        # session-scoped "loaded deferred tools" set so tool_search can
        # mutate it and subsequent turns see the updated set.
        from openprogram.programs import install_loaded_deferred
        install_loaded_deferred()

        # Project auto-commit (entity layer): snapshot which paths are
        # already dirty in the session's bound project BEFORE the agent
        # touches anything, so the turn-end commit can tell the user's
        # uncommitted work apart from the agent's edits (Strategy A). None
        # when disabled / ad-hoc session. Best-effort — never blocks a turn.
        try:
            from openprogram.store.project import project_commit as _pc
            self.project_baseline = _pc.snapshot_baseline(req.session_id)
        except Exception:
            self.project_baseline = None
        # Expose the GraphStore via ContextVar so deep code (Runtime.exec,
        # ask_user, @agentic_function decorator, and the file-checkpoint
        # helper behind write/edit/apply_patch) writes land in the same DAG
        # without threading the store through every layer.
        #
        # Bound BEFORE — and independently of — the DAG runtime below. It
        # used to live inside that try, so a create_runtime() failure (no
        # provider configured, or an exhausted auth pool, which is routine
        # when the chat runtime is a different provider entirely) left
        # _store unbound for the whole turn. checkpoint_before_edit needs
        # _store AND _current_turn_id, so it silently no-op'd: no
        # file_backups/, hence no per-turn file list, no diff, no undo.
        # The store needs a session, not a provider — so it binds regardless.
        self._store_token = _store_var.set(_GraphStore(db, req.session_id))
        try:
            from openprogram.providers.registry import create_runtime as _create_rt
            _dag_runtime = _create_rt()
            self._runtime_token = _current_runtime_var.set(_dag_runtime)
        except Exception:
            # No provider configured / runtime construction blew up.
            # Skip only the runtime; @agentic_function will still work, just
            # without an auto-injected runtime.
            self._runtime_token = None
        self._render_range_token = _render_range_var.set(req.render_range)
        from openprogram.agent.surface_context import bind as _bind_surface
        self._surface_token = _bind_surface(req.surface_context)
        from openprogram.agent.surface_context import web_use_owner_id
        self._web_use_owner_id = web_use_owner_id(req.surface_context)
        # Freeze the effective SandboxPolicy for this turn. Nested
        # (subagent) binds inherit and cannot relax the parent snapshot.
        try:
            from openprogram.agent.session_config import (
                load_session_run_config,
                sandbox_override_from_config,
            )
            from openprogram.sandbox import bind_turn_policy
            _cfg = load_session_run_config(req.session_id)
            self._sandbox_token = bind_turn_policy(
                sandbox_override_from_config(_cfg, req.session_id),
            )
        except Exception:
            self._sandbox_token = None
        return self

    def release(self) -> None:
        """Reset every ContextVar bound by :meth:`bind`.

        Guarded because attach may have silently failed (no provider
        configured). ValueError is what ContextVar.reset raises when the
        token came from a different context — the only failure worth
        tolerating here.
        """
        from openprogram.store import (
            _store as _store_var,
            _current_turn_id as _turn_id_var,
        )
        from openprogram.agentic_programming.function import (
            _current_runtime as _current_runtime_var,
            _render_range_override as _render_range_var,
        )
        try:
            if self._web_use_owner_id is not None:
                from openprogram.programs.workflow.browser.web_use_runtime import (
                    release_owner_if_initialized as _release_web_use_owner,
                )
                _release_web_use_owner(self._web_use_owner_id)
            if self._surface_token is not None:
                from openprogram.agent.surface_context import (
                    current as _current_surface,
                    release_bindings as _release_surface_bindings,
                    reset as _reset_surface,
                )
                _release_surface_bindings(_current_surface())
                _reset_surface(self._surface_token)
            if self._runtime_token is not None:
                _current_runtime_var.reset(self._runtime_token)
            if self._store_token is not None:
                _store_var.reset(self._store_token)
            if self._turn_id_token is not None:
                _turn_id_var.reset(self._turn_id_token)
            if self._turn_request_token is not None:
                from openprogram.agent.turn_request_context import (
                    reset_turn_request,
                )
                reset_turn_request(self._turn_request_token)
            if self._render_range_token is not None:
                _render_range_var.reset(self._render_range_token)
            if self._session_id_token is not None:
                _reset_session_id(self._session_id_token)
            if self._worktree_token is not None:
                from openprogram.worktree.context import reset_worktree
                reset_worktree(self._worktree_token)
            if self._sandbox_token is not None:
                from openprogram.sandbox import reset_turn_policy
                reset_turn_policy(self._sandbox_token)
        except ValueError:
            _log.debug(
                "context var teardown ran in a foreign context for session %s",
                self._req_session_id, exc_info=True,
            )
