"""skill — load a SKILL.md body into the turn.

The ``<available_skills>`` listing in the system prompt is for discovery
only: a name, a capped description, a path. This is the verb that turns a
name from that listing into the full instructions.

``read`` on the listed location does the same thing, and stays supported.
This tool adds the short-name resolution the listing's hierarchical names
ask for (``docx`` for ``anthropic-skills/docx``) and records the load in
the invocation trace the Skills page reads.

Deferred by default (see ``DEFERRED_DEFAULT_TOOLS``): the model pays one
catalog line per turn and only pulls the schema when it decides to load
a skill.
"""
from .skill import skill

__all__ = ["skill"]
