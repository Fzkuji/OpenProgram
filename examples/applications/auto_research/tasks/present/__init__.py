"""Present a paper: its method as a tested MCP server (paper_agent), and the questions a
talk's audience will ask with answers the paper backs (talk_prep)."""

from .agent import paper_agent
from .talk import talk_prep

__all__ = ["paper_agent", "talk_prep"]
