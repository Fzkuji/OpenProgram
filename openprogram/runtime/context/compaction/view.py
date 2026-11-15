"""Frozen DAG input and candidate rendering for context compaction."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from openprogram.state.store.session.nodes import Call, Graph
from openprogram.runtime.context.nodes import render_context
from openprogram.runtime.context.render import render_dag_messages
from openprogram.runtime.context.tokens import estimate_history_tokens


def _text(messages: list) -> str:
    """Keep tool arguments/results and visible text; never serialize image bytes."""
    rows = []
    for message in messages:
        blocks = message.content
        if isinstance(blocks, str):
            rows.append(f'{message.role}: {blocks}')
            continue
        for block in blocks:
            kind = getattr(block, 'type', '')
            if kind in ('text', 'thinking'):
                value = getattr(block, 'text', '') or getattr(block, 'thinking', '')
            elif kind == 'toolCall':
                value = json.dumps({'tool': block.name, 'arguments': block.arguments},
                                   ensure_ascii=False, default=str)
            elif kind in ('image', 'video', 'audio'):
                value = f'[{kind} retained in original session record]'
            else:
                value = str(block)
            rows.append(f'{message.role}: {value}')
    return '\n'.join(rows)


@dataclass
class CompactionView:
    graph: Graph
    head_id: str | None
    history: list[dict]
    messages: list
    rendered_ids: list[str]

    def candidate_messages(self, summary_text: str, cut: int, restored_state: str = '') -> list:
        from openprogram.runtime.context.persistence import covered_chain_ids, summary_content, SUMMARY_NODE_NAME
        graph = copy.deepcopy(self.graph)
        graph.add(Call(
            id='compaction_candidate', role='llm', name=SUMMARY_NODE_NAME,
            output=summary_content(summary_text, 'user'),
            predecessor=self.history[0].get('predecessor') or None,
            metadata={'covers_ids': covered_chain_ids(self.history[:cut]), 'render_role': 'user',
                      **({'restored_state': restored_state} if restored_state else {})},
        ))
        ids = render_context(graph, head_id=self.head_id, frame_entry_seq=-1)
        return render_dag_messages(graph, ids)

    def unchanged(self, db, session_id: str) -> bool:
        """Whether the chain this view was read from still reads the same.

        The chain is the one ending at ``head_id``; where the session's head
        has moved since does not matter. Only what the model reads on that
        chain counts: a write to another branch must not throw away a
        summary that has already been paid for.
        """
        from openprogram.state.store.session.session_node_writer import SessionNodeWriter
        current = SessionNodeWriter(db, session_id).load()
        if self.head_id not in current.nodes:
            return False
        if render_context(current, head_id=self.head_id, frame_entry_seq=-1) != self.rendered_ids:
            return False
        return all(current.nodes[i].to_dict() == self.graph.nodes[i].to_dict()
                   for i in self.rendered_ids)


def load_compaction_view(db, session_id: str, head_id: str | None = None,
                         *, audience: str = 'own_prompt') -> CompactionView:
    """What the chain ending at ``head_id`` reads, frozen for one compaction.

    ``None`` reads the session's head once, here; the view then stays on
    that chain whatever the head does next. ``audience`` is who the render
    is for (``context.turn_context``); the context panel passes the
    conversation's, so it counts the notes its requests send.
    """
    from openprogram.runtime.context.persistence import rendered_history
    from openprogram.state.store.session.session_node_writer import SessionNodeWriter

    head_id = head_id or (db.get_session(session_id) or {}).get('head_id')
    graph = SessionNodeWriter(db, session_id).load()
    history = rendered_history(db, session_id, head_id)
    ids = render_context(graph, head_id=head_id, frame_entry_seq=-1)
    messages = render_dag_messages(graph, ids, audience=audience)
    groups: dict[str, list[str]] = {m['id']: [] for m in history}
    for nid in ids:
        owner = nid
        visited = set()
        while owner and owner not in groups and owner not in visited:
            visited.add(owner)
            node = graph.nodes.get(owner)
            owner = node.caller if node else ''
        if owner in groups:
            groups[owner].append(nid)
    enriched = []
    for row in history:
        rendered = render_dag_messages(graph, groups[row['id']], audience=audience)
        enriched.append({**row, 'content': _text(rendered),
                         '_context_tokens': estimate_history_tokens(rendered)})
    return CompactionView(graph, head_id, enriched, messages, ids)
