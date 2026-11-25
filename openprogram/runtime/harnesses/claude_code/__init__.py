"""Claude Code as a conversation's engine: the person's own installed Claude Code runs the turns.

OpenProgram starts the ``claude`` the person has (the Claude app's own,
else one they installed) in its stream-JSON mode and shows what it does.
Claude Code signs in and calls the model itself; OpenProgram never reads
its credentials and never calls Anthropic for it.
"""
