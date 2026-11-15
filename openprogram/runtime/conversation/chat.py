"""Conversation policy on the public Agent execution contract."""
from openprogram.runtime.agent.agent_class import Agent


class ChatAgent(Agent):
    """Run conversations using saved Agent profiles and persistent Context.

    The base class owns execution, events, cancellation and continuation.
    Chat uses the saved system prompt, rather than this Python docstring.
    """
    instructions = None
    turn_kind = 'chat'
