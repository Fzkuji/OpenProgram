"""The prompts a compaction summary is written with.

* ``SUMMARY_RULES``   What every conversation summary must keep straight:
                      cancelled requests and who said what. Also used by
                      the request checkpoint.
* ``SYSTEM_PROMPT``   The summariser's job: a faithful, specific summary
                      the same agent continues from.
* ``SUMMARY_PROMPT``  The one summary prompt, first summary and update
                      alike (an update reads the summary in effect inside
                      ``<previous-summary>`` tags). Its sections follow
                      Claude Code's (every user message, the current work,
                      the next step with the latest request quoted), and it
                      leaves out what the next request sends anyway, as
                      OpenCode's does.
* ``FORK_SUMMARY_PROMPT``  The same prompt for a summary written as one
                      more turn of the conversation's own request
                      (``compaction/fork.py``). It arrives as a user
                      message, so it says first that it is the system's
                      request and must not be listed or quoted as the
                      user's (Codex's "CONTEXT CHECKPOINT COMPACTION"
                      opener, Claude Code's "the conversation so far").
* ``NUDGE``           Sent once when a reply leaves out a section.

``SUMMARY_SECTIONS`` are the headings a complete summary has
(``missing_sections``).
"""
from __future__ import annotations


SUMMARY_RULES = (
    "Mark every request the user cancelled, withdrew or replaced as "
    "cancelled or replaced, and never list it as outstanding work. "
    "Attribute what the assistant and tools said: write \"the assistant "
    "said X\" or \"the test run reported X\", and do not turn such a claim "
    "into a verified fact or into something the user agreed to."
)


SYSTEM_PROMPT = (
    "You are a summariser preserving conversation context across a "
    "model context-window boundary. Output a faithful summary that "
    "the same agent will continue work from. Be specific: include "
    "file paths, ids, command names, error messages, names of "
    "variables / functions / endpoints under discussion. Do NOT "
    "moralise, hedge, or add commentary about the task. Do NOT include "
    "preamble (\"Here is a summary…\"). Output the summary directly. "
    + SUMMARY_RULES
)


SUMMARY_SECTIONS = (
    "User intent",
    "All user messages",
    "Decisions and constraints",
    "Completed work",
    "Current work",
    "Next step",
    "Relevant files",
)


SUMMARY_PROMPT = (
    "Summarise the conversation in the source. The summary REPLACES that "
    "part of the transcript in your context window, and you continue the "
    "work from it. When a <previous-summary> is given, write one summary "
    "that supersedes it: keep what still holds, drop what is stale, and "
    "treat the newer messages as authoritative where they disagree.\n"
    "\n"
    "Use exactly these sections, each as a `## ` heading, in this order:\n"
    "\n"
    "## User intent\nThe user's overall goal in one or two sentences.\n"
    "## All user messages\nEvery message the user sent, in order, one per "
    "line, near-verbatim. Mark a request the user cancelled or replaced "
    "as such, and say when the user stopped a reply before it finished.\n"
    "## Decisions and constraints\nEvery instruction, preference and "
    "constraint the user set, and the decisions taken.\n"
    "## Completed work\nFiles created or changed, commands run, findings, "
    "with paths and ids.\n"
    "## Current work\nWhat was being done right before this summary.\n"
    "## Next step\nThe next step, if any, with the user's most recent "
    "request quoted word for word.\n"
    "## Relevant files\nPaths that matter for continuing, one per line.\n"
    "\n"
    "Leave out the system prompt, AGENTS.md, guides and memory: the next "
    "request sends their current versions separately. Write \"None\" under "
    "a section with nothing to say."
)


FORK_SUMMARY_PROMPT = (
    "[Context compaction] This message comes from the system, not from "
    "the user. Do not call tools and do not continue the task: write the "
    "summary below. \"The source\" is the whole conversation above this "
    "message. Leave this message out of every section: it is not one of "
    "the user's messages, and the user's most recent request is the last "
    "user message before it.\n\n"
    + SUMMARY_PROMPT
)


NUDGE = (
    "Your previous answer did not follow the required structure. Answer "
    "again with every one of these `## ` sections, in order: "
    + ", ".join(SUMMARY_SECTIONS) + "."
)


def missing_sections(text: str) -> list[str]:
    """The required section headings ``text`` does not have."""
    lowered = text.lower()
    return [name for name in SUMMARY_SECTIONS if f"## {name.lower()}" not in lowered]


__all__ = [
    "SUMMARY_RULES", "SYSTEM_PROMPT", "SUMMARY_SECTIONS", "SUMMARY_PROMPT",
    "FORK_SUMMARY_PROMPT",
    "NUDGE", "missing_sections",
]
