"""Discovery from the project's own material: open-ended exploration of data files
(data_explore), the code component that matters most for the metric (ablation_target)
and the author's notes brought into the world model (notes_ingest)."""

from . import ablation, explore, notes
from .ablation import ablation_target
from .explore import data_explore
from .notes import notes_ingest

ROLES = {**explore.ROLES, **ablation.ROLES, **notes.ROLES}

__all__ = ["ROLES", "ablation_target", "data_explore", "notes_ingest"]
