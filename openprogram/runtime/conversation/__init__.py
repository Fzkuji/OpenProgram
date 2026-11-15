"""Conversations: chat sessions and the turns that run in them.

A turn takes a user message, runs the Agent call core
(``openprogram.runtime.agent``) with the session's history and
settings, and writes the result back into the session. ``turn_runtime`` runs
one turn, ``chat`` is the ``ChatAgent`` entry, and ``production_driver``
drives a turn as a canonical execution.

Importing this package registers the conversation engine with the call core
(``engine_registration``), so ``Agent.run_turn`` and the core's lookups of
the running turn reach it, and claims the core's cancellation check
(``run_control``), so a stopped turn aborts at the next Agent method entry.
"""

# The call core, context and permission packages sit below this one;
# register what they read from conversations (see each module's docstring).
from openprogram.runtime.conversation import context_registration as _context_registration  # noqa: E402,F401
from openprogram.runtime.conversation import permission_registration as _permission_registration  # noqa: E402,F401

# Register the Agent hooks with the execution control plane.
from openprogram.runtime.conversation import execution_hooks as _execution_hooks  # noqa: E402,F401
from openprogram.runtime.conversation import engine_registration as _engine_registration  # noqa: E402,F401
# Claim the call core's cancellation check and session-id provider.
from openprogram.runtime.conversation import run_control as _run_control  # noqa: E402,F401
