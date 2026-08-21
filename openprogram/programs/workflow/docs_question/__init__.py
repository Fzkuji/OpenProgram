"""docs_question — answer a question about OpenProgram from its own docs.

"Can OpenProgram do X?" / "How do I configure Y?" are questions the
product documentation already answers, and answering them from the
model's memory of some other agent product is how a confident wrong
answer gets shipped. So this module spawns one agent whose whole world
is the repository's ``docs/`` tree: read-only tools, a prompt that pins
its working scope to that directory, and a pre-computed page listing so
it locates candidates by title before it opens anything.

The answer carries the pages it came from, and "the documentation does
not cover this" is a first-class answer — the prompt separates it from
"the documentation says this is not supported", because those are
different facts for the reader.

Generated reference pages (``docs/reference/cli/`` and friends) are
gitignored build output, so a fresh clone has none of them. The listing
simply reflects what is on disk; nothing here errors when they are
absent.

The single LLM round goes through a module-level ``_run_docs_turn``
seam, so tests stub one function instead of the network. Same shape as
``goal`` and the Workflow management functions.

Registration: AGENTIC_MODULES.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Optional

from openprogram.agentic_programming.function import (
    agentic_function,
    current_session_id,
)
from openprogram.programs.workflow.json_parsing import parse_json

_log = logging.getLogger(__name__)

# Reading only. No bash: a shell is a way out of the docs tree, and
# grep/glob/list/read already cover locating and reading a page. No
# write/edit either — answering a question never changes the docs.
DOCS_TOOLS = ("read", "grep", "glob", "list")

# Directories under docs/ that are build output, not pages.
_NOT_PAGES = {"_site", "images", "_static_root"}

# The listing is titles, not content — past this many pages it stops
# being a cheap index. The tree is well under it today; the cap is what
# keeps the prompt bounded if it grows.
MAX_LISTED_PAGES = 400


def docs_root() -> Path:
    """The repository's ``docs/`` directory.

    ``openprogram/programs/workflow/docs_question/__init__.py`` →
    repo root, the same walk-up ``webui/routes/docs.py`` does."""
    import openprogram

    return Path(openprogram.__file__).resolve().parent.parent / "docs"


def _title_of(path: Path) -> str:
    """The page's first markdown heading, or "" when it has none."""
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        return ""
    return ""


def list_pages(root: Optional[Path] = None) -> list[tuple[str, str]]:
    """``(relative path, title)`` for every English page under ``docs/``.

    English pages only: they are the authority, and each ``.zh.md``
    sits beside its ``.md`` so the agent can find the translation from
    the path it already has. A missing ``docs/`` (or a missing
    generated-reference subtree) yields fewer rows, never an error."""
    root = root or docs_root()
    if not root.is_dir():
        return []
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if rel.parts[0] in _NOT_PAGES or rel.name.endswith(".zh.md"):
            continue
        rows.append((rel.as_posix(), _title_of(path)))
    return rows[:MAX_LISTED_PAGES]


def normalize_source(raw: str, root: Optional[Path] = None) -> str:
    """One cited page as a clean path relative to ``docs/``.

    The agent reads absolute paths and may cite them back that way, or
    with a ``docs/`` prefix, or rooted at the docs tree (``/goal.md``).
    All of those mean the same page, so they are folded to the one form
    the caller gets. Returns "" for anything that does not land inside
    ``docs/`` — a citation that is not a docs page is not a source."""
    text = (raw or "").strip().strip("`").split("#", 1)[0].strip()
    if not text:
        return ""
    root = root or docs_root()
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            # Not a real filesystem path under docs/: the agent wrote a
            # site-style path rooted at the docs tree. Fall through and
            # read it as docs-relative.
            pass
    posix = candidate.as_posix().lstrip("/")
    if posix.startswith("docs/"):
        posix = posix[len("docs/"):]
    if not posix or ".." in Path(posix).parts:
        return ""
    # A citation only counts when it names a page that exists.
    return posix if (root / posix).is_file() else ""


def _prompt(question: str, root: Path) -> str:
    listing = "\n".join(
        f"{rel} — {title}" if title else rel for rel, title in list_pages(root)
    ) or "(no pages found)"
    return (
        f"{inspect.getdoc(run_docs_question)}\n\n"
        f"<docs_root>\n{root}\n</docs_root>\n\n"
        f"<pages>\n{listing}\n</pages>\n\n"
        f"<question>\n{question}\n</question>"
    )


def _run_docs_turn(session_id: str, prompt: str, *, agent_id: str,
                   spawn_caller: Optional[str]) -> str:
    """One read-only documentation-reading turn. Module-level so tests
    stub it."""
    from openprogram.agent.sub_agent_run import run_agent_turn
    res = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=None,
        label="文档查询",
        spawn_caller=spawn_caller,
        advance_head=False,
        tools_override=list(DOCS_TOOLS),
        # The question is the whole brief: the answer must come from the
        # docs, not from whatever the session was talking about.
        render_range={"callers": 0, "subcalls": 0},
    )
    if res.failed:
        raise RuntimeError(res.error or "docs question turn failed")
    return res.final_text or ""


def _parse_answer(raw: str, root: Optional[Path] = None) -> dict:
    """``{"answer", "sources", "covered"}`` from a reply.

    Raises ``ValueError`` when the reply carries no JSON object, when
    ``covered`` is not a bool, or when ``answer`` is empty — an answer
    with no text is not an answer. Sources are normalized and
    de-duplicated in order; a citation that is not an existing page
    under ``docs/`` is dropped, so a hallucinated page path never
    reaches the caller as a source."""
    data = parse_json(raw or "")
    if not isinstance(data, dict) or not isinstance(data.get("covered"), bool):
        raise ValueError("docs question reply was not valid JSON")
    answer = str(data.get("answer") or "").strip()
    if not answer:
        raise ValueError("docs question reply carried no answer text")
    sources: list[str] = []
    for item in (data.get("sources") or []):
        if not isinstance(item, str):
            continue
        rel = normalize_source(item, root)
        if rel and rel not in sources:
            sources.append(rel)
    return {"answer": answer, "sources": sources, "covered": bool(data["covered"])}


@agentic_function(input={
    "question": {"description": "A question about OpenProgram itself",
                 "multiline": True},
    "session_id": {"hidden": True},
    "spawn_caller": {"hidden": True},
    "agent_id": {"hidden": True},
})
def run_docs_question(question: str, session_id: str = "",
                      spawn_caller: Optional[str] = None,
                      agent_id: str = "main") -> dict:
    """You answer a question about OpenProgram itself, using ONLY its
    product documentation. The documentation tree is given below as
    <docs_root>, and every page in it is listed under <pages> with its
    path and title.

    Work in this order:

    1. Pick candidate pages from the <pages> listing by path and title.
       The question names a topic; the listing tells you which pages
       could hold it. Do not open pages at random.
    2. Read those pages. Use your read, grep, glob and list tools, and
       stay inside <docs_root> — nothing outside that directory is
       part of your answer, and you have no tools that change files.
    3. Answer from what the pages actually say.

    The English page (``xxx.md``) is authoritative. ``xxx.zh.md`` beside
    it is a translation; consult it when the question is in Chinese or
    when the English page is ambiguous, but when the two disagree the
    English page is what you report.

    Every claim in your answer must come from a page you read, and you
    must name those pages. An answer with no page behind it is a
    fabrication — never fill a gap with what you know about other agent
    products, and never infer a feature from a page that does not
    mention it.

    Distinguish three outcomes, and say which one you are giving:

    * The documentation answers the question — answer it, cite the
      pages, covered=true.
    * The documentation says the thing is NOT supported, or documents a
      different behaviour than the question assumes — report that as the
      answer with its page, covered=true. This is a documented fact.
    * The documentation does not mention it at all — say plainly that
      the documentation does not cover it, covered=false, and point at
      the closest related pages you did find so the reader knows where
      the topic would live. Do not guess whether the feature exists.

    Some listed pages may be missing on disk (generated reference pages
    are build output). A page you cannot open is simply not a source;
    carry on with the others.

    Keep the answer concrete: the command, the setting key, the file
    path, as the page writes it. Answer in the SAME LANGUAGE as the
    question.

    End your reply with STRICT JSON only, no markdown fence, no prose
    after it:
    {"answer": "<the answer, in the question's language>",
     "sources": ["capabilities/goal.md", …],
     "covered": true|false}
    Source paths are relative to <docs_root>.
    """
    text = (question or "").strip()
    if not text:
        raise ValueError("question must not be empty")
    sid = session_id or current_session_id()
    root = docs_root()
    raw = _run_docs_turn(sid, _prompt(text, root), agent_id=agent_id,
                         spawn_caller=spawn_caller)
    return _parse_answer(raw, root)


__all__ = ["run_docs_question", "list_pages", "normalize_source", "docs_root",
           "DOCS_TOOLS", "MAX_LISTED_PAGES"]
