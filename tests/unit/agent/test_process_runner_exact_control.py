"""Process controls must address one execution, never a whole session."""

import pytest

from openprogram.agent import process_runner


@pytest.mark.parametrize(
    "operation",
    (
        process_runner.is_subprocess_alive,
        process_runner.request_graceful_stop,
        process_runner.kill_active_subprocess,
    ),
)
def test_process_controls_require_exact_execution_id(operation):
    with pytest.raises(TypeError):
        operation("session-only")

    with pytest.raises(ValueError, match="execution_id is required"):
        operation("session-only", execution_id="")
