"""Complex built-in workflows. Directory nodes are source classification only."""

from .authoring import *  # noqa: F403
from . import authoring as _authoring

for _name, _value in vars(_authoring).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value
del _authoring, _name, _value
