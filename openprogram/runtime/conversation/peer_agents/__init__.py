"""Turns agents run for each other: starting a sub-agent run, merging a
branch back, and the messages between agents.

A message to an existing agent is addressed (``addressing``), delivered as a
tracked Job (``dispatch``, ``delivery``), steered into a running helper
(``steer``) or queued in the target's inbox (``inbox``) until its turn ends,
all within the budgets of its collaboration chain (``jobs.chain_budgets``). The
``send_message`` and ``agent`` tools (``openprogram.programs.tools.agents``)
are the model's surface over these; the runtime owns them because the inbox
is drained, and the chain budgets bound, by the turns themselves.
"""
