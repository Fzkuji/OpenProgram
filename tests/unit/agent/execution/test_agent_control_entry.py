"""The standalone Agent exposes no process-local steering control."""

from openprogram.agent.agent import Agent


def test_agent_has_no_local_steering_entry():
    agent = Agent()

    assert not hasattr(agent, "steer")
    assert not hasattr(agent, "_steering_queue")
