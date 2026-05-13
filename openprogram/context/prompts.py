"""Summarization prompts — pulled out so they're easy to A/B / tune.

We ship three prompts:

* ``SYSTEM_PROMPT``        — Frame the summariser's job; emphasise
                              specificity (file paths, ids, error msgs)
                              over hedging.
* ``FRESH_PROMPT``          — First-time summary: instruct the model to
                              cover goal / decisions / progress / open
                              tasks.
* ``UPDATE_PROMPT``         — Incremental summary: incorporate new
                              messages into an existing summary.

Each prompt is plain text; the summariser wraps the transcript in
``<conversation>`` tags and (when chaining) ``<previous-summary>``
tags before appending the appropriate prompt.

Why a separate module: lets you ship multiple prompt sets per language
or per agent persona without rebuilding the summariser.
"""
from __future__ import annotations


SYSTEM_PROMPT = (
    "You are a summariser preserving conversation context across a "
    "model context-window boundary. Output a faithful summary that "
    "the same agent will continue work from. Be specific: include "
    "file paths, ids, command names, error messages, names of "
    "variables / functions / endpoints under discussion. Do NOT "
    "moralise, hedge, or add commentary about the task. Do NOT include "
    "preamble (\"Here is a summary…\"). Output the summary directly."
)


FRESH_PROMPT = (
    "Summarise the conversation above. The user is about to send the "
    "next message and you will continue the work — the summary "
    "REPLACES the older transcript in your context window. Structure:\n"
    "\n"
    "1. **User intent**: the user's overall goal in 1-2 sentences.\n"
    "2. **Decisions**: every concrete instruction / preference / "
    "constraint the user has expressed, verbatim or near-verbatim.\n"
    "3. **Work completed**: files created / modified, commands run, "
    "data found, conclusions drawn — list with paths and ids.\n"
    "4. **Outstanding**: anything the user is still waiting for, or "
    "questions the assistant left dangling.\n"
    "5. **Active context**: any variable / artefact / state the agent "
    "needs to know about to keep working (e.g. \"connected to db X, "
    "open file Y, env var Z is set to W\")."
)


UPDATE_PROMPT = (
    "Below is an EXISTING summary inside <previous-summary> tags and "
    "NEW messages above. Produce a single revised summary that "
    "supersedes the previous one — incorporate the new messages, "
    "trim stale details, and update progress fields. Treat the new "
    "messages as authoritative when they contradict the prior "
    "summary. Use the same 5-section structure as the prior summary "
    "(User intent / Decisions / Work completed / Outstanding / "
    "Active context)."
)


__all__ = ["SYSTEM_PROMPT", "FRESH_PROMPT", "UPDATE_PROMPT"]
