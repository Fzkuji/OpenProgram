"""Question template handed to the answering agent."""

ANSWER_PROMPT = """User question:
{question}

Current date:
{question_date}

The user's memory is stored in a read-only workspace rooted at:
{memory_root}

The workspace contains topic, timeline, recent, core, and source memory. Use
the available read-only tools, then output exactly one <answer>...</answer>
block.

Available files:
{structure}
"""
