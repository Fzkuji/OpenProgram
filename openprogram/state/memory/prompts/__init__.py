"""What the model is told when it writes or reorganises memory."""

from .organize import ORGANIZE_MEMORY
from .system import FEW_SHOT_INSTRUCTIONS, SYSTEM_PROMPT
from .verify import VERIFY_JUDGE, VERIFY_PROBE, VERIFY_REPAIR, VERIFY_RETRIEVE
from .write import WRITE_MEMORY

__all__ = [
    "FEW_SHOT_INSTRUCTIONS",
    "ORGANIZE_MEMORY",
    "SYSTEM_PROMPT",
    "VERIFY_JUDGE",
    "VERIFY_PROBE",
    "VERIFY_REPAIR",
    "VERIFY_RETRIEVE",
    "WRITE_MEMORY",
]
