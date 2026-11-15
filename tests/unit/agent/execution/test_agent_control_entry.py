"""The standalone LoopDriver exposes no process-local steering control."""

from openprogram.runtime.agent.loop.driver import LoopDriver


def test_agent_has_no_local_steering_entry():
    agent = LoopDriver()

    assert not hasattr(agent, "steer")
    assert not hasattr(agent, "_steering_queue")
