"""Leaf LLM-callable tools.

Each subdirectory here holds one ``@function``-decorated tool —
deterministic Python whose body runs once per tool_call and returns
a result. No inner LLM rounds (that's what ``@agentic_function`` in
``../agentics/`` is for).

This package's only job is to side-effect-import every leaf tool on
load so the ``@function`` decorator's registration into the shared
``_registry`` fires before any consumer queries it. List order
doesn't matter; alphabetical for diff stability.
"""

from . import agent_browser as _agent_browser_self_register  # noqa: F401
from . import apply_patch as _apply_patch_self_register  # noqa: F401
from . import bash as _bash_self_register  # noqa: F401
from . import browser as _browser_self_register  # noqa: F401
from . import canvas as _canvas_self_register  # noqa: F401
from . import clarify as _clarify_self_register  # noqa: F401
from . import cron as _cron_self_register  # noqa: F401
from . import edit as _edit_self_register  # noqa: F401
from . import execute_code as _execute_code_self_register  # noqa: F401
from . import glob as _glob_self_register  # noqa: F401
from . import grep as _grep_self_register  # noqa: F401
from . import image_analyze as _image_analyze_self_register  # noqa: F401
from . import image_generate as _image_generate_self_register  # noqa: F401
from . import list as _list_self_register  # noqa: F401
from . import memory as _memory_self_register  # noqa: F401
from . import mixture_of_agents as _mixture_of_agents_self_register  # noqa: F401
from . import pdf as _pdf_self_register  # noqa: F401
from . import process as _process_self_register  # noqa: F401
from . import read as _read_self_register  # noqa: F401
from . import semble as _semble_self_register  # noqa: F401
from . import spawn_program as _spawn_program_self_register  # noqa: F401
from . import task as _task_self_register  # noqa: F401
from . import todo as _todo_self_register  # noqa: F401
from . import web_fetch as _web_fetch_self_register  # noqa: F401
from . import web_search as _web_search_self_register  # noqa: F401
from . import write as _write_self_register  # noqa: F401
