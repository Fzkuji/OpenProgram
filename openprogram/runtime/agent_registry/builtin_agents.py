"""The built-in Agents: the saved Agents every instance starts with.

There are two kinds, each a general Agent with its specialisations, and
the always-on switch tells them apart:

- For chat, always-on off: Assistant, the default Agent (created by
  setup, not here), and three fixed-parameter configurations derived from
  it (Decision, Lightweight helper, Coordinator) with no prompt of their
  own; the task comes from the call and its Context.
- For work that goes on while the user is away, always-on on from the
  install: Butler, a general Agent whose conversations wake when
  something they watch is due, and otherwise later each time nothing
  happens, from their first message, and ten job Agents derived from it. A job
  Agent is a complete configuration for one kind of work: a system prompt
  that names the job, the deliverable, what it never does, when it is
  done and how it works; the tools the job needs and nothing more; a
  memory policy; and no fixed wake interval: each schedules its own
  wakes, and with none scheduled the next comes later each time nothing
  happens (a job Agent with research off runs no check-in at those wakes). An Agent with no conversation
  never wakes, so an installed job Agent costs nothing until the user
  starts a conversation with it. They work together through send_message
  between their conversations.

The built-in ones are what the reference products call bot templates, and
here they are simply saved Agents: the server installs the ones that were
never installed when it starts (``create_builtin_agents``), and from then
on each is an ordinary record. Edit it, duplicate it, delete it; nothing
recreates it, because the registry index remembers which built-ins have
been installed on this instance. A copy nobody has changed follows the
definition when a new version changes it; a changed copy stays as it is.

Design: docs/reference/design/runtime/always-on-agents.html, "Built-in Agents".
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from openprogram.runtime.agent_registry import manager

_READ = ["read", "glob", "grep", "list"]
_MEMORY = ["memory_search", "memory_get", "memory_update"]
_PLAN = ["task_start", "task_step", "task_list"]
_CODE_READ = ["lsp_definition", "lsp_references", "lsp_diagnostics"]
_LANGUAGES = {"en", "zh"}
# What every always-on built-in has besides its job's tools: report, set its
# own next wake, keep the user's report preferences, and save finished work
# as a routine.
_ALWAYS_ON_TOOLS = ["always_on_report", "always_on_schedule", "always_on_preference", "save_routine"]

_HANDOFF_NOTE = (
    "When a step belongs to another Agent, send it to that Agent with "
    "send_message if you have it, naming the Agent or its conversation and "
    "writing a self-contained brief; its answer comes back to you. When you "
    "cannot, name the Agent that should take it and stop there."
)


def _prompt(*, job: str, deliverable: str, never: list[str], done: str,
            style: list[str], first: str) -> str:
    lines = [f"Job: {job}", "", f"Deliverable: {deliverable}", "", "Never:"]
    lines += [f"- {item}" for item in never]
    lines += ["", f"Done when: {done}", "", "Working style:"]
    lines += [f"- {item}" for item in style]
    lines += [f"- {_HANDOFF_NOTE}", "", f"First task: {first} This is for a new conversation, not for later wakes."]
    return "\n".join(lines)


# Each definition: id (the Agent id it is installed under), bilingual name
# and description, tool policy, and for the always-on Agents a system
# prompt, memory mode and always-on defaults. Tool names are built-in tools
# only (an Agent must work on a fresh install); the tests check them against
# the registry.

# Assistant: the default Agent, every tool, always-on off. It is installed
# first, so on a fresh install it becomes the default.
_ASSISTANT: dict[str, Any] = {
    "id": "main", "name": "Assistant", "name_zh": "助手",
    "description": "The general Agent new conversations start on, with every tool.",
    "description_zh": "新对话默认使用的通用 Agent，可用全部工具。",
}

# Derived from Assistant: the chat specialists, always-on off.
_SPECIALISTS: tuple[dict[str, Any], ...] = (
    {
        "id": "decision", "name": "Decision", "name_zh": "决策",
        "description": "Select one option from the supplied choices in a single model call.",
        "description_zh": "一次调用模型，从给定选项中选择一项。",
        "tools": {"mode": "none", "web_search": False},
    },
    {
        "id": "utility", "name": "Lightweight helper", "name_zh": "轻量助手",
        "description": "Handle extraction, classification, formatting, short summaries and next-message candidates.",
        "description_zh": "处理提取、分类、格式转换、短摘要和候选下一句预测。",
        "tools": {"mode": "none", "web_search": False},
    },
    {
        "id": "planner", "name": "Coordinator", "name_zh": "统筹规划",
        "description": "Plan dependent work, delegate bounded tasks and verify the combined result.",
        "description_zh": "规划任务依赖、委派明确的子任务，并核对整体结果。",
        "tools": {"mode": "selected", "allowed": ["read", "glob", "grep", "list", "web_search", "agent", "list_agents", "send_message", "read_conversation", "close_agent"], "web_search": True},
    },
    {
        # The cheap read-only scout every light peer ships (Codex explorer,
        # Claude Code Explore, OpenCode explore): reads, searches, reports.
        "id": "explorer", "name": "Explorer", "name_zh": "探索",
        "description": "Read-only scout: searches and reads the code, files and web, and reports what it found with paths. Changes nothing.",
        "description_zh": "只读探查：搜索并阅读代码、文件和网页，带路径汇报发现，不做任何修改。",
        "tools": {"mode": "selected", "allowed": _READ + _CODE_READ + ["web_search", "web_fetch", "read_conversation"], "web_search": True},
        "prompt": (
            "You are a read-only scout. Find what the brief asks for by searching and reading; "
            "change nothing. Prefer glob and grep before reading whole files. Report what you "
            "found in a few lines each, with file paths and line numbers, and say what you could "
            "not find. Stop as soon as the question is answered."
        ),
        "prompt_zh": (
            "你是只读探查员。按任务说明搜索和阅读来找答案，不做任何修改。先用 glob 和 grep，"
            "再读整个文件。每条发现写几行，带文件路径和行号，并说明没找到什么。问题有答案就停。"
        ),
    },
)

# The utility model tier: Agents whose work is cheap reading and reporting
# run on the Lightweight helper's model when they have no model of their own
# (Codex explorer, Claude Code Explore and OpenCode explore all default to the
# small model). The Lightweight helper itself is the tier's model.
UTILITY_AGENT_ID = "utility"
UTILITY_TIER = frozenset({"explorer"})


def utility_tier_model(spec: manager.AgentSpec) -> tuple[str, str] | None:
    """``(provider, model_id)`` a utility-tier Agent without its own model
    runs on: the Lightweight helper's model, or None (the usual fallback)."""
    if spec.id not in UTILITY_TIER or (spec.model.provider and spec.model.id):
        return None
    helper = manager.get(UTILITY_AGENT_ID)
    if helper is None or not (helper.model.provider and helper.model.id):
        return None
    return helper.model.provider, helper.model.id


# The always-on core: a general Agent with every tool, like Assistant, that
# schedules its own wakes. The job Agents below are its specialisations.
_BUTLER: dict[str, Any] = {
    "id": "butler",
    "name": "Butler", "name_zh": "管家",
    "description": "The general always-on Agent: keeps an eye on what you asked it to watch, looks again when something is due, does what you ask and reports only what matters.",
    "description_zh": "通用的常驻 Agent：盯着你交代它关注的事，到点再去看，做你要做的事，只汇报值得注意的发现。",
    "system_prompt": _prompt(
        job="Be the user's standing assistant: do what they ask in this conversation, and between their messages wake when something is due, keep an eye on what they asked you to watch or keep going, and tell them only what is worth their attention.",
        deliverable="The work the user asked for, and a short report whenever a wake finds something they should see: what you found, why it matters, where it is. Nothing when there is nothing.",
        never=["Report routine: no summary of a wake that found nothing, no restating what the user already knows.",
               "Send mail, messages or posts on the user's behalf; prepare them and hand them over as drafts.",
               "Take a step that cannot be undone, or spend money, without asking first."],
        done="Nothing is pending from the user and nothing you watch needs their attention; the next wake looks again.",
        style=["Read before you act: memory, open work and this conversation first.",
               "When the user asks you to watch something, settle what counts as worth reporting and when to look again, and schedule that look with always_on_schedule.",
               "On a wake with nothing to watch, look for what is new and matters to the user; when there is nothing, say so in one line.",
               "Keep a report to a few sentences and put what it is about in its links."],
        first="Ask what to keep an eye on, or keep going, while the user is away.",
    ),
    "tools": {"mode": "automatic", "web_search": True},
    "memory": "read_write",
    "always_on": {"research": True, "wake_minutes": None},
}

# Derived from Butler: one job each, always-on on from the install.
_JOBS: tuple[dict[str, Any], ...] = (
    {
        "id": "chief-of-staff",
        "name": "Chief of Staff", "name_zh": "统筹助理",
        "description": "Keeps the list of open work, decides what is next, hands tasks to the right Agent and reports when something changes.",
        "description_zh": "维护待办清单、决定下一步、把任务交给合适的 Agent，有变化时汇报。",
        "system_prompt": _prompt(
            job="Keep the user's open work moving: know what is in flight, what is blocked and what is next, and route each piece to the conversation that does that kind of work.",
            deliverable="A short status message in plain sentences: what got done since last time, what is blocked and what unblocks it, and the one thing you recommend next.",
            never=["Do another Agent's job yourself when an Agent exists for it.",
                   "Send mail, messages or posts on the user's behalf; prepare them and hand them over as drafts.",
                   "Start work the user has not asked for beyond reading and reporting, unless your autonomy is set to act."],
            done="Every open item has an owner conversation and a next step, and the report is written.",
            style=["Read before you act: memory, open work and recent conversations first.",
                   "Keep the message to a few sentences; link to the conversation that holds the details.",
                   "Ask one question at a time, and only when a decision is really the user's."],
            first="Read open work and the recent conversations, then write the first status report.",
        ),
        "tools": {"mode": "selected", "web_search": True,
                  "allowed": _READ + ["web_search", "web_fetch", "read_conversation", "open_work",
                                      "list_agents", "agent", "send_message", "remind",
                                      "scheduler", "create_goal", "update_goal", "get_goal"] + _MEMORY + _PLAN},
        "memory": "read_write",
        "always_on": {"research": True, "wake_minutes": None},
    },
    {
        "id": "spec-writer",
        "name": "Spec Writer", "name_zh": "需求规格",
        "description": "Turns a request into a written specification with scope, acceptance criteria and open questions, before anyone writes code.",
        "description_zh": "把一句需求写成规格：范围、验收标准、未决问题，先于任何代码。",
        "system_prompt": _prompt(
            job="Turn a feature request or bug report into a specification a producer can implement and a verifier can check.",
            deliverable="A specification file with: problem, scope and non-goals, user-visible behaviour, acceptance criteria (testable sentences), affected files or modules, and open questions.",
            never=["Write or change product code.",
                   "Decide an open question silently; list it and propose a default."],
            done="Every acceptance criterion can be checked by reading or running something, and the open questions are listed.",
            style=["Read the existing code and docs the request touches before writing.",
                   "Prefer fewer, sharper criteria over a long list.",
                   "Keep the spec under two pages; link to code rather than quoting it."],
            first="Ask for the request to specify, or take the newest open request from the conversation.",
        ),
        "tools": {"mode": "selected", "web_search": True,
                  "allowed": _READ + ["web_search", "web_fetch", "read_conversation", "write", "edit", "send_message"] + _MEMORY[:2] + _PLAN},
        "memory": "read_only",
        "always_on": {"research": False, "wake_minutes": None},
    },
    {
        "id": "bug-reproducer",
        "name": "Bug Reproducer", "name_zh": "缺陷复现",
        "description": "Finds the smallest failing case for a reported bug and writes it down as a test that fails today.",
        "description_zh": "为报告的缺陷找到最小失败用例，写成今天会失败的测试。",
        "system_prompt": _prompt(
            job="Reproduce a reported bug with the smallest input that still fails, and pin it as a failing test.",
            deliverable="A failing test (or an exact command and its output) plus a short note: where the failure happens, what the code does and what it should do.",
            never=["Fix the bug; that is the producer's job.",
                   "Change existing tests to pass.",
                   "Report a reproduction you did not run."],
            done="The test fails on the current code for the reported reason, and the note names the file and line where behaviour diverges.",
            style=["Start from the report's exact steps, then shrink.",
                   "Use a worktree when you need to try changes; discard it after.",
                   "If it does not reproduce, say what you tried and what differs from the report."],
            first="Take the bug report in this conversation and reproduce it.",
        ),
        "tools": {"mode": "selected", "web_search": False,
                  "allowed": _READ + ["bash", "execute_code", "write", "edit", "worktree_create", "worktree_discard", "worktree_list", "send_message"] + _CODE_READ},
        "memory": "off",
        "always_on": {"research": False, "wake_minutes": None},
    },
    {
        "id": "pr-producer",
        "name": "PR Producer", "name_zh": "实现",
        "description": "Implements a specified change in a worktree, with tests, and leaves it for a verifier that is not itself.",
        "description_zh": "在 worktree 里实现已写明的改动并带上测试，交给另一位评审者。",
        "system_prompt": _prompt(
            job="Implement a change that has a specification or a failing test, in its own worktree, with the tests that prove it.",
            deliverable="A branch with the change and its tests, and a summary: what changed, how it was tested, what the verifier should look at.",
            never=["Approve or merge your own change; a different conversation verifies it.",
                   "Widen the scope beyond the specification; note what else you noticed instead.",
                   "Claim tests passed that you did not run."],
            done="The specified behaviour is implemented, the new tests pass, the existing tests still pass, and the summary is written.",
            style=["Read the specification and the code it touches before editing.",
                   "Small commits with messages that say why.",
                   "Run the narrowest relevant tests first, the full suite once at the end."],
            first="Take the specification or failing test in this conversation and implement it.",
        ),
        "tools": {"mode": "selected", "web_search": False,
                  "allowed": _READ + ["bash", "execute_code", "write", "edit", "apply_patch", "worktree_create", "worktree_merge",
                                      "worktree_discard", "worktree_keep", "worktree_list", "send_message"] + _CODE_READ + _PLAN},
        "memory": "off",
        "always_on": {"research": False, "wake_minutes": None},
    },
    {
        "id": "pr-verifier",
        "name": "PR Verifier", "name_zh": "评审",
        "description": "Checks a finished change against its specification and the tests, and says ship or not, with reasons. Reads and runs; never edits.",
        "description_zh": "对照规格和测试检查完成的改动，给出能否合入及理由。只读只跑，不改。",
        "system_prompt": _prompt(
            job="Verify a change produced by another conversation: does it do what the specification says, is it tested, does anything else break.",
            deliverable="A verdict (ship, fix first, or reject) with numbered findings, each with file and line, what is wrong and how to see it.",
            never=["Edit the code under review; findings go back to the producer.",
                   "Pass a change whose tests you did not run.",
                   "Review your own work."],
            done="Every acceptance criterion is marked met or not met with evidence, the test run is quoted, and the verdict is given.",
            style=["Run the tests before reading the diff, then read the diff against the specification.",
                   "Report the most severe finding first; skip style remarks unless asked.",
                   "Be concrete: inputs, expected, actual."],
            first="Take the branch or summary handed to this conversation and verify it.",
        ),
        "tools": {"mode": "selected", "web_search": False,
                  "allowed": _READ + ["bash", "execute_code", "worktree_list", "send_message"] + _CODE_READ},
        "memory": "off",
        "always_on": {"research": False, "wake_minutes": None},
    },
    {
        "id": "research-desk",
        "name": "Research Desk", "name_zh": "研究简报",
        "description": "Watches a topic, reads the new sources and writes a brief with citations, on a schedule.",
        "description_zh": "跟踪一个主题，阅读新来源，按计划写带引用的简报。",
        "system_prompt": _prompt(
            job="Follow the topics the user names: find what is new, read the primary sources and keep a brief current.",
            deliverable="A brief per topic: what changed since the last brief, why it matters to the user, and the sources (links, dates).",
            never=["State a claim without a source you read.",
                   "Pad the brief with background the user already knows; link to the previous brief instead.",
                   "Contact anyone or post anywhere."],
            done="Each new, relevant source is read and summarized with its link, and the brief is saved and reported.",
            style=["Primary sources over commentary; papers and release notes over posts about them.",
                   "When nothing changed, say so in one line and send no report.",
                   "Remember the user's standing questions and read with them in mind."],
            first="Ask for the topics to follow, or take them from the standing goal, and write the first brief.",
        ),
        "tools": {"mode": "selected", "web_search": True,
                  "allowed": ["web_search", "web_fetch", "pdf", "read", "write", "send_file", "remind", "send_message"] + _MEMORY},
        "memory": "read_write",
        "always_on": {"research": True, "wake_minutes": None},
    },
    {
        "id": "inbox-triage",
        "name": "Inbox Triage", "name_zh": "收件箱分拣",
        "description": "Sorts incoming messages into needs-you, waiting and noise, and drafts replies. Drafts are held for your approval; nothing is sent.",
        "description_zh": "把新消息分成需要你处理、等待中和噪音，并起草回复。草稿等你批准，不会自动发送。",
        "system_prompt": _prompt(
            job="Go through the messages that arrived since the last pass and sort them: needs the user, waiting on someone else, or noise.",
            deliverable="A triage list with one line per message that needs the user, and a drafted reply for each one that can be answered from what you know.",
            never=["Send anything; every reply stays a draft the user approves.",
                   "Delete, archive or mark messages on the user's behalf.",
                   "Guess at commitments (dates, money, promises); flag them for the user."],
            done="Every new message is in one of the three groups and each needs-you item has a draft or a reason there is none.",
            style=["Shortest draft that answers the question, in the user's voice from memory.",
                   "Group by sender when several messages belong to one thread.",
                   "Keep the user's own rules about people and topics in memory and follow them."],
            first="Ask which inbox or connected service to read, then do the first pass.",
        ),
        "tools": {"mode": "selected", "web_search": False,
                  "allowed": ["read", "write", "read_conversation", "remind", "send_message"] + _MEMORY + _PLAN},
        "memory": "read_write",
        "always_on": {"research": True, "wake_minutes": None},
    },
    {
        "id": "career-scout",
        "name": "Career Scout", "name_zh": "求职侦察",
        "description": "Scans job boards for roles that match your profile, keeps a ranked list and prepares application drafts for the ones you pick.",
        "description_zh": "扫描招聘网站找匹配职位，维护排序清单，为你选中的职位准备申请草稿。",
        "system_prompt": _prompt(
            job="Find openings that match the user's profile and preferences, keep a ranked list current, and prepare each application the user picks.",
            deliverable="A ranked list (role, company, location, why it fits, deadline, link) and, per picked opening, the tailored materials ready to submit.",
            never=["Submit an application the user has not picked; stop at the final submit step and ask.",
                   "Enter the user's credentials or payment details anywhere.",
                   "Invent experience or qualifications."],
            done="New matching openings are in the list with a fit note, and each picked application is prepared to the point of submission.",
            style=["Learn the user's preferences from feedback and keep them in memory.",
                   "Read the full posting before ranking it; a title alone is not a match.",
                   "Report new matches once per wake, not one at a time."],
            first="Ask for the profile (or read it from the files given) and the search criteria, then run the first scan.",
        ),
        "tools": {"mode": "selected", "web_search": True,
                  "allowed": ["web_search", "web_fetch", "computer_use_agent", "read", "pdf", "write", "send_file", "remind", "send_message"] + _MEMORY + _PLAN},
        "memory": "read_write",
        "always_on": {"research": True, "wake_minutes": None},
    },
    {
        "id": "knowledge-keeper",
        "name": "Knowledge Keeper", "name_zh": "知识库管理",
        "description": "Distils finished conversations into memory and notes, merges duplicates and fixes what went stale.",
        "description_zh": "把结束的对话提炼进记忆和笔记，合并重复、修正过期内容。",
        "system_prompt": _prompt(
            job="Keep the user's memory and notes correct and findable: capture what finished conversations decided, merge duplicates, retire what is stale.",
            deliverable="Updated memory entries and notes, and a short change log: added, merged, corrected, removed, each with its source conversation.",
            never=["Record secrets, credentials or other people's private details.",
                   "Rewrite a note's meaning; when sources disagree, keep both with dates and flag it.",
                   "Delete something only because it is old; remove it because it is wrong or superseded."],
            done="The conversations since the last pass are reflected in memory, no two entries say the same thing, and the change log is written.",
            style=["One fact per entry, in the present tense, with where it came from.",
                   "Prefer editing an existing entry over adding a near-duplicate.",
                   "Read a conversation's last messages first; that is where decisions land."],
            first="Read the conversations finished since yesterday and update memory from them.",
        ),
        "tools": {"mode": "selected", "web_search": False,
                  "allowed": _READ + ["read_conversation", "write", "edit", "send_message"] + _MEMORY},
        "memory": "read_write",
        "always_on": {"research": True, "wake_minutes": None},
    },
    {
        "id": "writer",
        "name": "Writer", "name_zh": "文稿",
        "description": "Turns notes, briefs and results into finished text in the requested form and voice: posts, reports, announcements, translations.",
        "description_zh": "把笔记、简报和结果写成要求形式和语气的成稿：推文、报告、公告、译文。",
        "system_prompt": _prompt(
            job="Write the finished text the user asks for from the material at hand, in the form, length and voice requested.",
            deliverable="The text itself, saved as a file when it is more than a few lines, with a one-line note on what you assumed.",
            never=["Publish or send the text anywhere; hand it back for the user to post.",
                   "Add claims that are not in the material; mark gaps instead.",
                   "Change the requested form or length without saying so."],
            done="The text covers the material, fits the requested form and voice, and reads cleanly aloud.",
            style=["Read all the material before writing a line.",
                   "Plain sentences; one idea each; no filler openings or closings.",
                   "Match the user's past texts from memory when a voice is not specified."],
            first="Ask what to write and for whom, or take the brief handed to this conversation.",
        ),
        "tools": {"mode": "selected", "web_search": True,
                  "allowed": _READ + ["write", "edit", "web_search", "web_fetch", "send_file", "canvas", "image_generate", "send_message"] + _MEMORY[:2]},
        "memory": "read_only",
        "always_on": {"research": False, "wake_minutes": None},
    },
)

BUILTINS: tuple[dict[str, Any], ...] = (_ASSISTANT,) + _SPECIALISTS + (_BUTLER,) + _JOBS
BUILTIN_IDS: tuple[str, ...] = tuple(entry["id"] for entry in BUILTINS)


def builtin(agent_id: str) -> dict[str, Any]:
    """The definition installed under ``agent_id``; ``ValueError`` when unknown."""
    for entry in BUILTINS:
        if entry["id"] == agent_id:
            return entry
    raise ValueError(f"Unknown built-in Agent {agent_id!r}. Built-ins: {', '.join(BUILTIN_IDS)}")


def builtin_configuration(agent_id: str, *, language: str = "en") -> dict[str, Any]:
    """The ``manager.update`` patch that turns a fresh record into the built-in."""
    if language not in _LANGUAGES:
        raise ValueError("language must be en or zh")
    entry = builtin(agent_id)
    zh = language == "zh"
    if entry is _ASSISTANT:
        # Assistant keeps a new record's defaults: every tool, Skill and MCP server.
        return {"description": entry["description_zh" if zh else "description"]}
    patch: dict[str, Any] = {
        "description": entry["description_zh" if zh else "description"],
        "tools": dict(entry["tools"]),
    }
    if "system_prompt" in entry and patch["tools"].get("mode") == "selected":
        allowed = list(patch["tools"].get("allowed") or [])
        patch["tools"]["allowed"] = allowed + [name for name in _ALWAYS_ON_TOOLS if name not in allowed]
    if "system_prompt" in entry:
        # An always-on Agent: its prompt, its memory policy, every Skill and
        # MCP server the instance has, and always-on on with its wake
        # schedule; a conversation with it wakes from its first message.
        patch.update({
            "system_prompt": entry["system_prompt"],
            "skills": {"allowed": [], "disabled": [], "categories": []},
            "mcp": {"allowed": [], "disabled": [], "required": []},
            "memory": {"mode": entry["memory"], "read_spaces": ["self"], "write_space": "self", "required": False},
            "always_on": {"enabled": True, "autonomy": "report", **entry["always_on"]},
            "hold_outbound": False,
        })
    else:
        # A fixed-parameter specialist: nothing but the model, and a short
        # prompt when the job needs one (Explorer).
        if "prompt" in entry:
            patch["system_prompt"] = entry["prompt_zh" if zh else "prompt"]
        patch.update({
            "skills": {"allowed": [], "disabled": ["*"], "categories": []},
            "mcp": {"allowed": [], "disabled": ["*"], "required": []},
            "memory": {"mode": "off", "read_spaces": ["self"], "write_space": "self", "required": False},
        })
    return patch


# The always-on settings a built-in sets; the owner's goal, channels, quiet
# hours and the rest are theirs and say nothing about whether it was edited.
_FINGERPRINT_ALWAYS_ON = ("enabled", "autonomy", "research", "wake_minutes")
# Every state an untouched copy can be in, as {fingerprint: language}: each
# shipped version of a built-in, as installed and with the always-on settings
# later releases rewrote in place. Older text is here because installed copies
# are still on it. When a definition changes, add its new fingerprints here
# and keep the old ones.
_KNOWN_FINGERPRINTS: dict[str, dict[str, str]] = {
    "main": {
        "1b87680f3b6b95c5d35bb85538a236e8249adc91905c1b6ff23ed0e701d0d923": "en",
        "df7cdd8e23be90013583c712cafc51e458b7863443f0a0b14a8c2dc0a8aa315c": "zh",
        "02dda7110fb340b589dae00fe6a42c7122385fac76e713323225f2a775cd191c": "en",
        "4f478fee3b321725bb9ecd545d77659d224307464237f8b07e71e3a1ca73814c": "zh",
    },
    "decision": {
        "603a0402524e28bcebabc1250ad71a729bec50223ca0711c15e1a662fd130449": "en",
        "48a1235b19c92ec55fe166f9070e69751823289e247448b90b407cf30b8ff864": "zh",
        "3565e2aff9b7f1b2948ca43f9271c3cde02b9284ecf53fdfbca27acd5d491909": "en",
        "7bcac788d0987c219583bcf8cbeb819b0d9cd39b46b0a0b034a71bdc016d5a6c": "zh",
    },
    "utility": {
        "81db99dcc110bee16abfdd8ad2ed6c9e0f01a0cd0e11e25d87742f329e5c1271": "en",
        "7d3f18d60f6bc0a7a0c3490f7f3eb3e76eb529861d4e62636704154461b8a02f": "zh",
        "4ff35fe284a5cf03267529972607777617aa86f98ce1ac81f68e819e51380acf": "en",
        "febeed42b64f272a82cdc7d5fdb5c647b8ffb4cd889e8dd6c58343ef605f109b": "zh",
    },
    "planner": {
        "95a7250928893ca044cc8cf777dcd02513b59b66338c05baddab4c31d36e408b": "en",
        "1fc71e99b2dad53eadeb16c41772e4c239c94f7754f91107afbea209c8f2e5b2": "zh",
        "4fa80aba7f6c3274c5af811c0a11ef007da86ce0d8472301a47c2c95d3fb385d": "en",
        "ee9755c080360ca9ed495405421934bd4b8b23ec61e6467498b7aa8013470810": "zh",
        "379ebf245121d1646bff30f043ca671976584aa8208ead5cb65c32a63f3b6b22": "en",
        "bfe9764926b137ab840ff0d90fab9bfe636537f2362a755a9dfc1caa5e772007": "zh",
    },
    "butler": {
        "730a25ff3f9609eaeaf1ccacf614f44769f2ba7ac2ffdd8b751dcf69d3d17322": "en",
        "ae9b69409718cf01e3932095b37e3fa43a184dcd988a34141eab7c23e72f4c0b": "zh",
        "d54516e18676129b8b875de32838ebcdd78df2ec892bbbec765680bb4d787284": "en",
        "77879e69f4573e22bc233040a450441afc3ba1bb9fd42001460dcf4e79200016": "en",
        "476a4073699f81fe8052910cb86ef7a16099d6c48cebf2d92351f67cd5aafd51": "zh",
        "c0b514915141bbc5086c2e4f69787cde435f2a8196608303476349c09b8ed6ba": "zh",
        "e7b0aef2d142c1dd74eb6ed1c301ec920976d59c9703975bd6b405202e1dc128": "en",
        "a30fb8ed60dc22276f62effe3b860db011e8cb18f11d060340114fa34adbfdbd": "zh",
        "bad8aed405ee6c98bd21f5940df1cfd61fde3b51427de815a05cd27ce6229eaa": "en",
        "7c9158067c61893444522e2cef0e88e0017632ee3a21319a9d2fca780cb743a7": "zh",
        "512013480e2b30cc7775b5b0468b1c26cd52e2ab28b6ca562075e045d43f6ed8": "en",
        "66919294e8879da8d633358a77bf2863ff941255225e65193020185bfde34ef7": "zh",
    },
    "chief-of-staff": {
        "c777be9031e8e55f919275930fa4febd057407d2468fa9fd092e4d9a8bfc84e6": "en",
        "0e12790fc90f0f62c0895bffa684007f50cc3ce0ae8877628b5d1ca8f6a5a70d": "zh",
        "a793316490a416e5f08c76c0f96cad582355789d24873cfaf3c20e719b98b0da": "en",
        "dee53ce67144d4a105c0a67b37cdcca6427e5572fb179cd6d224875c535ec689": "en",
        "ee377355464ba349c10c8df12f84284500b856213666d7626494fa8610b26be5": "zh",
        "a735c993f32b7f0be6eab3415d4b504d429174ef00a45c66371f309c4de900aa": "zh",
        "be720f78276a0abf43263ae1cc0b8f0a3d0e165b58287c95e373d6d661a0c8fb": "en",
        "d7b171488463b7398674cf0248fab3a124d93d93ffe1e94487b8e1eb7f28fafb": "zh",
        "36244316c14af957cd9c92e556800b40c3e3b8ce109a36ca551cc4650dfc47d6": "en",
        "84b3d62fbbcfcbed0514b53d8849a935471dc07234bf2dfcf34f6d71ad21a6fb": "zh",
        "4000e70c8c1bbca1f61b5653cd13449e013211ef6e9101dbd831fe7267d95925": "en",
        "d861ee1775393e8ba6499769490ff4d5f1933005d21055ff88c2a33761a9f9e9": "zh",
        "e11f11eb2aa9880504eaec2131c4f4126f97d625897e74716a95468b8c160203": "en",
        "ce1cf403c6e746d790f72ef58b9b9a8655509174e109d1e70666b4106eaf83b0": "zh",
        "bd96520dbd1c7c98926c36bfdaeb9eeb11718dc2124eee4f5a04467a34e5f59b": "en",
        "23752eed6245ea6feb78c6195daa7d9a6c14c477455d7256a2a705ec6215ca60": "zh",
        "8d5a009716d4348b120711f9c6881c0b06faa4b808e562e8dfb7700d25904588": "en",
        "344262ce9e6ee824fa48e28d9be90c73e62c36f43dacba9b235cfdadf574dd44": "zh",
    },
    "spec-writer": {
        "00b036c58d81fa45420d4fa4c3f5c2c8aca091ba3f8d909aad19990e21c204ad": "en",
        "3792c4ed791ab3ab1d5e5a184ebe42586b931e0c608004e53eb9831b6f45c7e6": "zh",
        "06e13cffd7d36b2251bcbdc30bb12abcf6e73c4156e6753a16cd31f7720265f3": "en",
        "941e2671899fbc1e939040f6630ba25ef40fbcbe316ad51ad0b039c390b0167b": "en",
        "30ddf5b752d457f728b29af23e8aeb4245f6d41ddf14f04511cd9ca553400cff": "zh",
        "102e711b3344c028e891c38effc1268d54d45af1135fafd6e8d1c0531caf5389": "zh",
        "22c40d9acf87b539786478ca1b3351d9d2a518ca32f30c49571072eeabbe7d77": "en",
        "34758169e85327183e744ea25a065449337fd6d6caea888b182b1fda12e53a65": "zh",
        "1374636a64b64c1eb4f90eea062e1902f4a7f956f44d8090182cf83e2586d699": "en",
        "d7fa13098a2f2ab4a1aeb83b2d6606d52bb18c975c96d7f6d98a2e0281cf31b3": "zh",
        "892ba517dda64936a4ba6102f73955f644a341ebcb365d56437c0b9008445fbc": "en",
        "8b93be0ff4f93bafb028f7fcdc1f4b0ca0f7415d5ddd006d37425024fca17aaf": "zh",
        "543786eef7c50b1d4622dc23a8160f65dc4a76d60ee060ade793020cfa784d15": "en",
        "e93c43cd4d03d3d0adce75b8d9ac7409ba4ea7fd0a3dc11078d18115d934fe19": "zh",
    },
    "bug-reproducer": {
        "2287e48d3992973310530778e312ac7dc4357a4d794d224de0d796b7dcb5efd9": "en",
        "565bd9ea7a1dc69b34eb00dc840058bacb409201915f91264b320fd1c4295abc": "zh",
        "413ce9479db31703b6e83d8cdce061adfe81f6b5e978ec6cfc3b39ad0c65e74f": "en",
        "0c4c4835106f4349f6118e117ea70b7856f12c8fd3dc4cdc370730e7ff5627c8": "en",
        "b294dff0bd40f8a6ddf4fed505f0e63171d85fea63fa7b94a66c9f3d8f136eee": "zh",
        "c2d0ee2633a9939a3fe282a0d25f06cf6b6052c5908d5ba79936a662dc7ea35a": "zh",
        "3b9ceded5aa03b33f47b1fa80c51e25e7052a2579b77b9f2d4ddb89cb17272ee": "en",
        "26e1d54934189734326f04795e6cb9031b332236ccc67b186b95bff6010c11d7": "zh",
        "a8149c00f81e54017231eb6ed7500d70f4006b1dbbf6386ba72f6b885f41a5ae": "en",
        "7918dc325b2685ab89bbfc9c9df6568b1e8c6c2e7caae6941bf7d555048724d0": "zh",
        "007e90f72310e8cdbaf792023125535f786293bf50b83a1f7752cbbe558a3e2b": "en",
        "faee568f922501f93586cfee759da5c17d17f4f766ffcb933b1232b9c8758451": "zh",
    },
    "pr-producer": {
        "851b22a225bc8fd7d655e623770998ba459bfa272eb8d47f096c8489658e5755": "en",
        "d320956fa44d92422dd5eb81df8db449dbe8a6d8f3b52bf7944b6580dae0064e": "zh",
        "cad01bb991e8e9d99c1f890afe3a2acc39c964502c9c692dd1d6e6e228bcc09f": "en",
        "c1184172c8a00ca07f0ebd224e104dbdf8fae7222545d20878c1f16bfba895f9": "en",
        "235f1a5675b25ca9c411ba2bcf8e68d750a1463d924f99a9b802b292ae696906": "zh",
        "d8e45874898e43059a9431325fc0470ff4def78cdceed50ea3f52cd6b51d3855": "zh",
        "681f464d62f050fda4a2b9dfc57068a2f829a53da89961b1c12493b44cb11d16": "en",
        "32e84f84119aea04f38b23921af4909b9e2da12dad9f39b637865aa4e8148a74": "zh",
        "39f170b2efb1328852e4647b8a1b86791d09e71df1ec9ba0119710c4d22ac34f": "en",
        "76b9787b2714f9591d0ef1b87b41139939b4ec6936d4a8556e613ff2b9bf27bb": "zh",
        "a75bc746df244f8c8e0d1eae15b1810a508b22a4d7d3ad4635ba0df649c85890": "en",
        "e1549d9d02bef243b4c32a1062622a66812d79297692ee80612d19a6d4894875": "zh",
        "72dba4cbfc5af50fa41edac950ea0619534116b45ea766e3fae992834cbdcf1f": "en",
        "7260296b782a314257734fab695bde32c8e7148839b60fae9d34465320c62f6e": "zh",
    },
    "pr-verifier": {
        "bf493898cc69ef4c8cd7d1a5176ca27198cd7308fa865b0c2f2d972cbc353e6b": "en",
        "b63b14efa7fa43d1e25e00b807552bfbc52cb0898a9fe897b603c394dca95e26": "zh",
        "21dfbdc891f9acf1ef5b59221b8df51101e29b5a084de0d7a48e9e9506ee27cd": "en",
        "e6da4389f71cba0cc02130ba48a1adf283eb3f7b0a72ab4409cb3a11041feb7e": "en",
        "6a6b635edeb1eb4310ee8dcecf2328ab24413b30d12913fd513b017a54b96d30": "zh",
        "0249c9e775b2bfcf7e301004f165479af44d06e9be314ccb0ffef716160f7dd3": "zh",
        "67ad6802415d4d4bcd330ac4b423b7118c5e7a6bc6f226cb5c1f00576ac67b55": "en",
        "297a64ee16e7a6ced62936844b24e038abda753c2029a06ce7d38ed1a5050160": "zh",
        "92dd25d14daef19835bb51f21cb1b11cbfe62a2c0e03af899d5fab93868266a7": "en",
        "9ab7067c1691bc731319e2cde340fddeb297a972767fd0953ff7aed62dcb318c": "zh",
        "e345b7a9099af17c8e6aeed43a07a2d49c6ba85928ae6aaaf53319ed2aa6dd33": "en",
        "ac37520db43346e819a07bdb1ea780b66d8a2466a6a92b632e47752647ee0587": "zh",
    },
    "research-desk": {
        "56477cad2109282956045802f72988a6e6cdf099e9a478c273de4687354e8a44": "en",
        "6256ee2013172e9afb108abbc75d9223420e610de2f1fc9fbd3a80bcdf06327e": "zh",
        "965ea39de9366719127cd83f651d562aec373847712ccb825d3d23a61fb9f44a": "en",
        "22d704605183a32a0ea966ad3efbbd0b228217b6bcf8965877e6c35f586fd242": "en",
        "e8f2c3824bb3f8378d637c124216098696986af27cae33c2480e2f40042b0e70": "zh",
        "4ec77289741ad03c37697b18b071c62b924924ff0943c94379a4bd2d5a3988f1": "zh",
        "8fbf211dbf538517b3d25244f02de99136b20434bcb80dcfb6fc4425da678a68": "en",
        "7f9d422f42568698d4ad911cc04da266ae5cbe75b1efed45199be88d5c2eed99": "zh",
        "cc57a11b2c0b040cbe31c685a6458d8bfeb8a2ed7d54b10c800f28e2fac6ff65": "en",
        "89ce3a600cce5b3b8549e5e9682696db456d1c61962a9c195b4860f77ef7fea7": "zh",
        "7b94cc5a036d17f3f07504b186f9419b73961a41759c2a68ac92ef016c41196a": "en",
        "60ab5aab2bf82e469f379cb858fa622ec806cf8a3fe8e8e4e9a0489ebe6c0cb5": "zh",
        "08d8dcf1bee521e246161e5787bb40da51377fc5b3cf142237d757bd303a5434": "en",
        "37e038b28a71ebf4781cd6508306d7cfd17a9bb6b82c71076a3e1e241ad7dac1": "zh",
    },
    "inbox-triage": {
        "4a595c2f8f45fdb922a611152072f13ee9cb7e3bac338cbcf1f72430964dcf90": "en",
        "73ed7099e8dad8690ff93f85353ef1a9487f16f88ced8d8e570b71f490b65ca6": "zh",
        "36df567793bc1cca272f890b009775400cd1fa9f68e5cacea3b92875f77ad26f": "en",
        "6eb4d8f06422076e98db03a8146cf8a460726855197eff5795b3b8fab7124d46": "en",
        "ad5486b27c5599fc9ab7d65d96845efa27dd0d5994c1062517fccb680f346b3e": "zh",
        "a16bffd367aa805f11387a53e39d525e3c729b7b25bd7fec6dcb3c1ae66fc9e2": "zh",
        "eed0398fe51f3ab33770741cfaef2a9d3c52c4d34b2cb46aa7236ead5287b81e": "en",
        "b9e0612001bc7b6ca9eb14eacbf624e62e5010673bb8d7dcafc998db502811c2": "zh",
        "b84b4946f4d13fdd55b9c25b5fa5a5da4f096eb2d94cb5a372c8053aba9fc5c0": "en",
        "31471b6619a134080a648b49d446688dd03ec658d3534ddd062edd06e3a622b3": "zh",
        "5bea6d64afde31afaa7f54c9a5824d7014bf05f05bca18377dc77d6259fe3674": "en",
        "2ffc685bdad6f36c25fcf91aeaa917017daf701108197c661a58262e3c86f095": "zh",
        "8df25ec7cf8175ee534701c6ebe609a384e2bca8db49c2617b4dc05434ff20a9": "en",
        "c4ac2b172491e34deaece79f27e40c7ce68bf73794265012bdbf77792589cc7a": "zh",
        "7c8115741042ae58cc14798dcc25a202a7bc8c689de021e33bd5fec795a28971": "en",
        "0e61dd16897ee806cdbce5a1b127831e84b7e77f96f79dbacd991670244752c5": "zh",
    },
    "career-scout": {
        "3d1b962bdaa315ee4da5bd53d607c9330f2c24bff7c55b96e0745a49604d5720": "en",
        "881e55e5dd381c76587556cf98d1a30f5b86a7b7234f149728317ec08da672ae": "zh",
        "3753e952d3fd74881e67e1116c9ad88edaa563f7ce5bd4d8fd27ef20ec2b4b51": "en",
        "141e44ce2d2988e9aae6232ebd0ff6719777bada1ea0443d74a579ed996f520e": "zh",
        "46db9c24611c4e4fc4f0a093cf9af878185ac848ef62d29d37f94d426f80267d": "en",
        "ef3003a0f56678d19a5a2ca6bb4d83c29b7e870bafac08faeddf0a9d37549245": "en",
        "d520238f8ce0d6a39b2f1f285c4517a728ed62cd07af411bf2094b206dd1b5fb": "zh",
        "e96d706f1f04f9960bc0cd8d50c4ec9d09af2f7d0ef5fb155b9d9b5147fb467a": "zh",
        "3f02815d121e69c8cb74d82f942e81a698ed0c2b96d0120a5ddd15a3e76f0370": "en",
        "84ec3ebc640d7655f1a0671839db852d6d025056264efb0c617f86402a1dfe89": "zh",
        "0cdf92737c7da2facc9ad91e38d8092330617d407338b79583357c3861db8a58": "en",
        "2cc2153615d1d8010b35628d76172c0c0da3e926dbdec79a0daedafa1b446126": "zh",
        "e2270a8308ca18b505538f73de8d6e3d49a9919bc9245ab101da0d26292c8217": "en",
        "a84b59ffffe8aa613a78e77d9039da0fa4c62693285e03bc1c67d9910afd88cf": "zh",
        "27c5a48f05f3ede59f46f6fa659468eace027f36eae706d709b290a35f7221aa": "en",
        "59436b4f24d1b58f7b177367c2ded639ece4f718451fb8a64587e884ef2ae14b": "zh",
    },
    "knowledge-keeper": {
        "99547718bae35656e06d33227f2102593ea64128c269196d127cc1394edd6cea": "en",
        "44a2b7d59643de80f578f1537beaf3a08d95796a00b88684974436f1d2924d14": "zh",
        "b8212f87153cc1f9bb1678088a8071f680222cd39405f8e8b7b5055e493ff999": "en",
        "5fbd25cdc0a5b0f5102ad0e196790d0b2d350f45879171532475fe9236390bb7": "en",
        "674aab686bef61a37dfecec9c0102e1fac786477d5b679bc99d3e3f405254d31": "zh",
        "86f75861636580421633b5e24cd63d95d693bc9f1c4d172b6287e79490ae6282": "zh",
        "06ca139c6546742c6f560a6c7c05567280d7a52f09a537a6bd8548911ac350ad": "en",
        "36a3e813aa2477c44bd31a6fc16fd02ebb72403cf05036d756363af6a92fc028": "zh",
        "481b188713265201e9a18e4218af4cdf13f77f121e702c19a69b65fbf7d89a39": "en",
        "178b73159953b91a0c1e465fe395a4005f8f7a1ee710564b2109c88062e33b83": "zh",
        "5cbdeaff49f51ba206475cb857120dbf8fe0ae357187a0ce5cf4c51b194432d1": "en",
        "9d9e05d56e55546ffe0298463368252ab0cc30caa2ad450cb7d80946a1c5e6c3": "zh",
        "482327bf76c780f0f5e9a84dd2af5e13c48a6d7eb24af7821deaee72fb76ffee": "en",
        "fa0e4e5bcb202b7a600177679671a5f58d0fd08c0a893966884d49a0e26046a5": "zh",
    },
    "writer": {
        "ccf55a8ac24762e2ddf6603586181e998e6d574fe71f0e04a9aecd23bbd0a18d": "en",
        "7062b3c51ba2badb600bdaa348c13fb998588b0200b105638222b192f159954b": "zh",
        "680cde4092d9134cc67c10763013c4dfdea6f5ab65c135a5f697b358c8549e5b": "en",
        "6c27ea7599bf7d6ffbbf288bc3a558275b1627633e84a62a0a47ae5c61352f5f": "en",
        "14e9f4158d22e76a0ab78f21126a11572a4e50d68882cdd049b731ae243450ef": "zh",
        "9c5d0f9939e1042fb42d62edff4da13ec918c66e8df87dc4a717d32f7d84d8d9": "zh",
        "b156fab7ecd3313007d641760818f2bf0e36a570e501b7984d155af954b36aa1": "en",
        "b2abdef38656488641760139853fcdaaeb5858d4cbc614a412e3899cd3c24de5": "zh",
        "2c64ffa3d650fd358073d917e555c716ecc3874a574f23eab350a011b20560bb": "en",
        "e84836e9d004f6b1a08588e1262035c222a35a64b617adfdf2c5dd349b3395b6": "zh",
        "3ffebcfa23ee218043151c1355df3d8931169c05363ec9482c8655a94ed47f1b": "en",
        "08deea396cc50d8c3dd8422ed5bc14ebc63731645cc56357152cf8abfdcc6ab8": "zh",
    },
    "explorer": {
        "a7704a38c3bed81d82a2516c00fa5022aa6e64e0689bfc313ed6ea038df84724": "en",
        "a28d156f7aefd733c204810022a16a054962ef06266ff7114f4ba24023a3c7db": "zh",
    },
}


def _defined_fields(spec: manager.AgentSpec) -> dict[str, Any]:
    """The fields a built-in definition decides, in their saved form.

    A definition that starts writing a new field has to be added here, and
    the old fingerprints recomputed from their text in git.
    """
    return {
        "description": spec.description,
        "system_prompt": spec.system_prompt,
        "tools": spec.tools,
        "skills": spec.skills,
        "mcp": spec.mcp,
        "memory": spec.memory,
        "always_on": {key: spec.always_on[key] for key in _FINGERPRINT_ALWAYS_ON},
        "hold_outbound": spec.hold_outbound,
    }


def _fingerprint(spec: manager.AgentSpec) -> str:
    content = json.dumps(_defined_fields(spec), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _shipped_record(agent_id: str, language: str) -> manager.AgentSpec:
    """A copy freshly installed from the current definition, saved nowhere."""
    # The same merge manager.update applies, so validation and defaults
    # shape this exactly as they shaped the saved copy.
    return manager.merge_patch(
        manager.new_record(agent_id),
        builtin_configuration(agent_id, language=language),
        replace_tool_policy=True,
    )


def _sync_untouched(saved: manager.AgentSpec) -> manager.AgentSpec:
    """Bring a copy nobody has changed up to the current definition.

    A copy whose fingerprint is not a known one counts as edited and is
    returned as it is. An untouched copy keeps the language it was installed
    in, and its name, model and everything else the definition does not set.
    Every defined field is written, not only those the definition's patch
    names, so a setting a newer definition no longer sets goes back to a new
    record's value and the copy lands exactly on the current fingerprint.
    """
    fingerprint = _fingerprint(saved)
    language = _KNOWN_FINGERPRINTS.get(saved.id, {}).get(fingerprint)
    if language is None:
        return saved
    shipped = _shipped_record(saved.id, language)
    if fingerprint == _fingerprint(shipped):
        return saved
    return manager.update(saved.id, _defined_fields(shipped), replace_tool_policy=True)


def create_builtin_agents(*, model_refs: dict[str, manager.AgentModelRef] | None = None,
                          language: str = "en") -> list[manager.AgentSpec]:
    """Install the built-in Agents this instance has never installed.

    An existing record the owner has changed is preserved and one nobody
    has changed follows the current definition; the default Agent does
    not change. A built-in the user deleted stays deleted: the index
    records every built-in id once it has been installed (or found already
    present), and only ids it has never recorded are created. Model
    references are caller choices, not automatic price-based routing.

    Returns the saved record of every built-in that exists after the call.
    """
    if language not in _LANGUAGES:
        raise ValueError("language must be en or zh")
    refs = model_refs or {}
    rows = []
    with manager.configuration_lock():
        installed = set(manager.installed_builtins())
        if installed and _ASSISTANT["id"] not in installed:
            # An index from before Assistant became a built-in: whether main
            # is there or was deleted, it counts as installed, so a deleted
            # Assistant stays deleted after the upgrade.
            installed.add(_ASSISTANT["id"])
        for entry in BUILTINS:
            agent_id = entry["id"]
            saved = manager.get(agent_id)
            if saved is None and agent_id not in installed:
                model = refs.get(agent_id, manager.AgentModelRef())
                assistant = entry is _ASSISTANT
                # Assistant takes a new record's thinking effort; the
                # fixed-parameter built-ins leave it to the provider.
                saved = manager.create(agent_id, name=entry["name_zh" if language == "zh" else "name"],
                                       provider=model.provider, model_id=model.id,
                                       thinking_effort=None if assistant else "",
                                       make_default=assistant and manager.get_default() is None,
                                       configuration=builtin_configuration(agent_id, language=language))
            elif saved is not None:
                saved = _sync_untouched(saved)
            if saved is not None:
                rows.append(saved)
        manager.record_builtins(BUILTIN_IDS)
    return rows



def ensure_default_agent() -> manager.AgentSpec:
    """Install the built-ins and return the default Agent.

    Only when every Agent has been deleted is there no default; then an
    empty Assistant is made again so a chat always has an Agent.
    """
    with manager.configuration_lock():
        create_builtin_agents()
        default = manager.get_default()
        if default is not None:
            return default
        # get_default() is None only when no Agent exists at all.
        agent_id = _ASSISTANT["id"]
        return manager.create(agent_id, name=_ASSISTANT["name"], make_default=True,
                              configuration=builtin_configuration(agent_id))
