"""Deep phase — promote candidates to wiki, regenerate pages, refresh core.

Reads the staged candidate list from light, asks an LLM to:

  1. Classify each candidate into kind (entity / concept / procedure / user)
  2. Decide create-new-page vs merge-into-existing vs disputed
  3. Produce structured ``Claim`` records to add to the page
  4. Re-render the page body from the merged claim set

Then writes wiki pages, updates the index, and regenerates ``core.md``.

The LLM call is the most expensive part of the sweep. We pass it the
top-N candidates and the existing page set, and we expect a structured
JSON response. If the model is unavailable or returns malformed JSON,
deep degrades gracefully: candidates stay in short-term, no wiki write,
core stays stale until next run.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from .. import core, store, wiki
from ..schema import Claim, WikiPage, now_iso, slugify
from .light import read_stage
from .scoring import THRESHOLDS

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are the deep-sleep memory consolidator for an AI agent. You receive:

  1. A list of candidate facts (from recent conversations)
  2. A list of existing wiki pages with their current claims

Your job: decide for each candidate whether it should be promoted into
the wiki, and if so, into which page (create new or merge into existing).

Output JSON with this exact shape:

{
  "decisions": [
    {
      "candidate_key": "<the input key>",
      "action": "create" | "merge" | "skip",
      "page_kind": "entities" | "concepts" | "procedures" | "user",
      "page_slug": "<filesystem-safe-id>",
      "page_title": "<human title>",
      "page_aliases": ["short-name", "..."],
      "claim_text": "<one atomic factual sentence>",
      "claim_status": "candidate" | "confirmed" | "disputed",
      "claim_confidence": 0.0,
      "reason": "<short why-this-decision>"
    }
  ],
  "page_bodies": {
    "<kind>/<slug>": "<refreshed body markdown after the merge>"
  }
}

Guidelines:
- Skip candidates that are too vague or one-off ("user asked about X").
- Prefer merging into existing pages rather than fragmenting.
- If two claims contradict, mark the new one disputed and explain in reason.
- Keep claim text atomic; one sentence, < 200 chars.
- ``page_bodies`` should be lean prose (3–8 lines per page) summarising
  the page's confirmed claims in flowing English.
- Slugs are ``[a-z0-9-]+`` only, no spaces.
- Return only the JSON object — no preamble, no markdown fences.
"""


def run(*, llm: Callable[[str, str], str] | None) -> dict[str, Any]:
    """Promote candidates into wiki. Caller supplies the LLM callable.

    ``llm(system_prompt, user_text) -> str``. If None, we skip the LLM
    pass and just exit cleanly so the cron is still safe to run.
    """
    stage = read_stage()
    candidates = [
        c for c in stage.get("candidates", [])
        if c["score"] >= THRESHOLDS["min_score"]
    ]
    if not candidates:
        return {"phase": "deep", "promoted": 0, "skipped": "no candidates above threshold"}
    if llm is None:
        return {"phase": "deep", "promoted": 0, "skipped": "no LLM configured"}

    user_text = _build_user_text(candidates)
    try:
        raw = llm(SYSTEM_PROMPT, user_text)
        decisions = _parse_decisions(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("deep phase LLM call failed: %s", e)
        return {"phase": "deep", "promoted": 0, "error": str(e)}

    promoted = 0
    skipped = 0
    by_page: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for d in decisions.get("decisions", []):
        action = d.get("action", "")
        if action == "skip":
            skipped += 1
            continue
        kind = d.get("page_kind", "entities")
        slug = slugify(str(d.get("page_slug") or "untitled"))
        by_page.setdefault((kind, slug), []).append(d)

    bodies = decisions.get("page_bodies", {})

    for (kind, slug), entries in by_page.items():
        existing = wiki.get(kind, slug)
        title = entries[0].get("page_title") or slug.replace("-", " ").title()
        aliases: list[str] = []
        if existing:
            aliases.extend(existing.aliases)
        for d in entries:
            for a in (d.get("page_aliases") or []):
                if a and a not in aliases and a != slug:
                    aliases.append(a)

        existing_claims = list(existing.claims) if existing else []
        existing_sources = list(existing.sources) if existing else []

        for d in entries:
            text = (d.get("claim_text") or "").strip()
            if not text:
                continue
            status = d.get("claim_status", "candidate")
            try:
                conf = max(0.0, min(1.0, float(d.get("claim_confidence", 0.6))))
            except (TypeError, ValueError):
                conf = 0.6
            sources = next(
                (c["sources"] for c in candidates if c["key"] == d.get("candidate_key")),
                [],
            )
            if any(_same_claim(c, text) for c in existing_claims):
                # Refresh confidence + sources in place.
                for c in existing_claims:
                    if _same_claim(c, text):
                        c.confidence = max(c.confidence, conf)
                        for s in sources:
                            if s not in c.sources:
                                c.sources.append(s)
                        if status in ("confirmed", "disputed"):
                            c.status = status
            else:
                existing_claims.append(Claim(
                    text=text, confidence=conf, status=status, sources=sources,
                ))
            for s in sources:
                if s not in existing_sources:
                    existing_sources.append(s)

        body = bodies.get(f"{kind}/{slug}") or (existing.body if existing else "")
        page = WikiPage(
            type=kind,
            id=slug,
            title=title,
            body=body,
            aliases=aliases,
            confidence=_avg_confidence(existing_claims),
            last_updated=now_iso(),
            sources=existing_sources,
            claims=existing_claims,
        )
        wiki.write(page, source="sleep-deep", reason=f"promoted {len(entries)} claim(s)")
        promoted += len(entries)

    if promoted > 0:
        wiki.regenerate_index()
        _refresh_core()

    _write_last_sleep({"phase": "deep", "promoted": promoted, "skipped": skipped})
    return {"phase": "deep", "promoted": promoted, "skipped": skipped}


def _build_user_text(candidates: list[dict[str, Any]]) -> str:
    cand_lines = ["# Candidate facts", ""]
    for c in candidates[:30]:
        cand_lines.append(
            f"- key: {c['key']!r}\n"
            f"  text: {c['text']}\n"
            f"  score: {c['score']}, n_days: {c['n_distinct_days']}, "
            f"confidence: {c['max_confidence']}, type: {c['type']}, tags: {c['tags']}"
        )
    page_lines = ["", "# Existing wiki pages", ""]
    for page in wiki.all_pages():
        page_lines.append(f"## {page.type}/{page.id} — {page.title}")
        for cl in page.claims:
            page_lines.append(f"- ({cl.status}) {cl.text}")
        page_lines.append("")
    return "\n".join(cand_lines + page_lines)


def _parse_decisions(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {"decisions": [], "page_bodies": {}}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {"decisions": [], "page_bodies": {}}


def _same_claim(c: Claim, text: str) -> bool:
    return c.text.strip().lower() == text.strip().lower()


def _avg_confidence(claims: list[Claim]) -> float:
    if not claims:
        return 0.5
    return round(sum(c.confidence for c in claims) / len(claims), 3)


def _refresh_core() -> None:
    """Pick top-confidence claims and rewrite ``core.md``."""
    pages = list(wiki.all_pages())
    entries = core.select_entries(pages)
    core.write(entries, last_consolidated=datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def _write_last_sleep(report: dict[str, Any]) -> None:
    p = store.last_sleep_path()
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **report,
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
