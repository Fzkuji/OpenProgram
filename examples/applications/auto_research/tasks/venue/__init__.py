"""Around the venue: the camera-ready check, the anonymous supplementary bundle, moving to
another venue's template and the submission-form fields."""

from .anonymous import anonymous_package
from .camera_ready import camera_ready_check
from .metadata import submission_metadata
from .move import venue_move

__all__ = ["anonymous_package", "camera_ready_check", "submission_metadata", "venue_move"]
