"""Deterministic LLM-callable functions, grouped by source purpose.

Each category package imports its shipped function packages so their
``@function`` decorators register with the shared runtime registry. Public
callable names do not depend on this source hierarchy.
"""

from . import agents as _agents_self_register  # noqa: F401
from . import code as _code_self_register  # noqa: F401
from . import files as _files_self_register  # noqa: F401
from . import interaction as _interaction_self_register  # noqa: F401
from . import jobs as _jobs_self_register  # noqa: F401
from . import knowledge as _knowledge_self_register  # noqa: F401
from . import planning as _planning_self_register  # noqa: F401
from . import runtime as _runtime_self_register  # noqa: F401
from . import system as _system_self_register  # noqa: F401
from . import web as _web_self_register  # noqa: F401
