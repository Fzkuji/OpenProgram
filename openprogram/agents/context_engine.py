"""Context engine — how we assemble model context per turn.

Modelled on OpenClaw's context-engine concept (ingest / assemble /
compact / after_turn), reshaped for our smaller storage layout.

Our ContextGit already tracks message history as a DAG: every commit
(user / assistant turn) has a parent; siblings are retries. The
context engine sits ON TOP of that — it decides which messages on
the active branch to actually send to the model, how to summarize
overflow, and what extra system-prompt text to prepend.

Public API:

    engine = ContextEngine()
    engine.ingest(messages, new_msg)           # stores into messages
    assembled = engine.assemble(agent, session)
      -> AssembleResult(messages, estimated_chars, system_prompt_addition)
    engine.compact(agent, session, budget)     # reduces tail history
    engine.after_turn(agent, session)          # post-turn hook

Budget here is char-based rather than token-based — token counting
requires a per-provider tokenizer, and for our MVP a conservative
char count is close enough (≈ 4 chars/token for English).

The assemble output doesn't include the user's incoming text — the
caller appends that as the final block. Assemble just produces the
header (system prompt addition) + any rendered history.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from openprogram.agents.manager import AgentSpec
from openprogram.agents import workspace as _workspace


# Same char budget as _conversation.py used before. Conservative for
# Claude-sonnet-class windows; engines can override per-call.
DEFAULT_HISTORY_CHAR_BUDGET = 60_000


@dataclass
class AssembleResult:
    """What ``assemble()`` returns for a single turn."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    estimated_chars: int = 0
    system_prompt_addition: str = ""


class ContextEngine:
    """The default ("legacy") engine — rendering is string-based, no
    summarization yet (hook exists for a future plugin).

    Single source of truth for how our channel inbound path turns a
    DAG-stored session into a blob the LLM sees. Other front-ends
    (Web UI chat, CLI chat) will route through this too in a
    follow-up.
    """

    name = "legacy"

    # ------------------------------------------------------------------
    # Lifecycle hooks (mostly no-ops in the legacy engine)
    # ------------------------------------------------------------------

    def ingest(self, messages: list[dict[str, Any]],
               new_msg: dict[str, Any]) -> None:
        """Mutates ``messages`` in place: append the new message.

        The legacy engine trusts the caller to persist the file; the
        DAG writer (``channels/_conversation.py::_save_session``)
        does the actual disk I/O.
        """
        messages.append(new_msg)

    def after_turn(self, agent: AgentSpec,
                   session_meta: dict[str, Any],
                   messages: list[dict[str, Any]]) -> None:
        """Post-turn hook — stamp `updated_at`, check budget for a
        lazy compaction pass on the next assemble."""
        session_meta["_last_touched"] = time.time()
        # Compaction isn't automatic yet — we flag an overfull session
        # so the next assemble() can decide what to drop.
        if self._size(messages) > DEFAULT_HISTORY_CHAR_BUDGET:
            session_meta["_needs_compact"] = True

    def compact(self, agent: AgentSpec,
                session_meta: dict[str, Any],
                messages: list[dict[str, Any]],
                budget: int = DEFAULT_HISTORY_CHAR_BUDGET) -> bool:
        """Drop oldest turns until we fit under ``budget``, leaving a
        synthetic summary block at the head.

        Returns True when a compaction happened. Messages are mutated
        in place. This is the deliberately-simple form; a real engine
        would summarize the dropped region via an LLM call. We just
        note how many turns got elided so the user can see the gap
        when inspecting the DAG.
        """
        if self._size(messages) <= budget:
            return False
        keep: list[dict[str, Any]] = []
        running = 0
        dropped = 0
        # Walk from the newest end backwards.
        for m in reversed(messages):
            entry_size = len((m.get("content") or ""))
            if running + entry_size > budget and keep:
                dropped += 1
                continue
            running += entry_size
            keep.append(m)
        keep.reverse()
        if dropped:
            # Synthesize a summary head so the model knows context
            # was dropped rather than silently shortening history.
            summary = {
                "role": "system",
                "id": f"_compact_{int(time.time())}",
                "parent_id": None,
                "content": (f"[context compacted: "
                            f"{dropped} earlier message(s) elided]"),
                "timestamp": time.time(),
                "_synthesized": True,
            }
            keep.insert(0, summary)
        messages[:] = keep
        session_meta["_needs_compact"] = False
        return True

    # ------------------------------------------------------------------
    # Assemble — the core
    # ------------------------------------------------------------------

    def assemble(self, agent: AgentSpec,
                 session_meta: dict[str, Any],
                 messages: list[dict[str, Any]],
                 *,
                 budget: int = DEFAULT_HISTORY_CHAR_BUDGET,
                 ) -> AssembleResult:
        """Build the content blocks + system-prompt addition for this
        turn's ``rt.exec`` call.

        Automatically compacts before assembly if the stored size is
        over budget; cheap when it isn't.
        """
        if self._size(messages) > budget:
            self.compact(agent, session_meta, messages, budget)

        system_prompt_addition = self.build_system_prompt(agent)
        rendered_history = self._render_history_plain(messages, budget)

        out_messages: list[dict[str, Any]] = []
        if rendered_history:
            out_messages.append({
                "type": "text", "text": rendered_history,
            })
        return AssembleResult(
            messages=out_messages,
            estimated_chars=self._size(messages),
            system_prompt_addition=system_prompt_addition,
        )

    # ------------------------------------------------------------------
    # System prompt composition
    # ------------------------------------------------------------------

    def build_system_prompt(self, agent: AgentSpec) -> str:
        """Layered prompt: identity header → workspace persona files
        → agent.system_prompt → enabled skills summary.

        Blocks are separated by blank lines and wrapped in a single
        "── Agent prompt ──" fence so the model sees the boundary
        clearly.
        """
        parts: list[str] = []

        # 1. Identity banner
        name = (agent.identity.name or agent.name or agent.id).strip()
        header = f"You are {name} (agent_id={agent.id})."
        mentions = agent.identity.mention_patterns or []
        if mentions:
            header += (" Users may address you via: "
                       + ", ".join(mentions) + ".")
        parts.append(header)

        # 2. Workspace files — AGENTS.md first (rules), then SOUL.md
        # (persona), then USER.md (about the user).
        for reader in (_workspace.read_agents_md,
                       _workspace.read_soul_md,
                       _workspace.read_user_md):
            block = (reader(agent.id) or "").strip()
            if block:
                parts.append(block)

        # 3. User-supplied inline prompt (if they set one via `agents
        # show` edits).
        inline = (agent.system_prompt or "").strip()
        if inline:
            parts.append(inline)

        # 4. Skill index (short — full skill bodies come from the
        # skill tool on demand).
        skill_index = self._enabled_skills_summary(agent)
        if skill_index:
            parts.append(skill_index)

        if not parts:
            return ""
        return ("── Agent prompt ──\n"
                + "\n\n".join(parts)
                + "\n── End of agent prompt ──\n")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _size(messages: list[dict[str, Any]]) -> int:
        return sum(len(m.get("content") or "") for m in messages)

    @staticmethod
    def _render_history_plain(messages: list[dict[str, Any]],
                              budget: int) -> str:
        """Walk recent-first so the budget prioritizes the freshest
        turns; re-reverse so the prompt reads chronologically."""
        parts: list[str] = []
        total = 0
        for m in reversed(messages):
            role = m.get("role", "")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                entry = f"[User]: {content}"
            elif role == "assistant":
                entry = f"[Assistant]: {content}"
            elif role == "system" and m.get("_synthesized"):
                entry = f"[System]: {content}"
            else:
                continue
            if total + len(entry) > budget:
                break
            parts.append(entry)
            total += len(entry)
        parts.reverse()
        if not parts:
            return ""
        return (
            "── Conversation history ──\n"
            + "\n".join(parts)
            + "\n── End of history ──\n\n"
        )

    @staticmethod
    def _enabled_skills_summary(agent: AgentSpec) -> str:
        try:
            from openprogram.agentic_programming import (
                default_skill_dirs, load_skills,
            )
        except Exception:
            return ""
        try:
            skills = load_skills(default_skill_dirs())
        except Exception:
            return ""
        if not skills:
            return ""
        disabled = set((agent.skills or {}).get("disabled") or [])
        enabled = [s for s in skills if s.name not in disabled]
        if not enabled:
            return ""
        lines = ["Skills available on demand:"]
        for s in enabled[:20]:
            desc = (getattr(s, "description", "") or "").strip()
            if desc:
                desc = desc.splitlines()[0][:80]
                lines.append(f"  · {s.name} — {desc}")
            else:
                lines.append(f"  · {s.name}")
        if len(enabled) > 20:
            lines.append(f"  ... (+{len(enabled) - 20} more)")
        return "\n".join(lines)


# Module-level singleton — shared across channel handlers so they
# don't each build their own.
default_engine = ContextEngine()
