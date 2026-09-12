"""Frozen DAG input and candidate rendering for context compaction."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from openprogram.context.nodes import Call, Graph, render_context
from openprogram.context.render import _aged_code_ids, render_dag_messages
from openprogram.context.tokens import estimate_history_tokens


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
    manifest: dict
    messages: list

    def candidate_messages(self, summary_text: str, cut: int) -> list:
        from openprogram.context.persistence import covered_chain_ids, SUMMARY_NODE_NAME
        graph = copy.deepcopy(self.graph)
        graph.add(Call(
            id='compaction_candidate', role='llm', name=SUMMARY_NODE_NAME,
            output=f'[Previous conversation summary]\n{summary_text}',
            predecessor=self.history[0].get('predecessor') or None,
            metadata={'covers_ids': covered_chain_ids(self.history[:cut])},
        ))
        ids = render_context(graph, head_id=self.head_id, frame_entry_seq=-1)
        return render_dag_messages(graph, ids, manifest=self.manifest)

    def unchanged(self, db, session_id: str) -> bool:
        from openprogram.store.session.session_node_writer import SessionNodeWriter
        if (db.get_session(session_id) or {}).get('head_id') != self.head_id:
            return False
        current = SessionNodeWriter(db, session_id).load()
        return {k: n.to_dict() for k, n in current.nodes.items()} == {
            k: n.to_dict() for k, n in self.graph.nodes.items()
        }


def load_compaction_view(db, session_id: str, head_id: str | None = None) -> CompactionView:
    from openprogram.context.persistence import rendered_history
    from openprogram.store.session.session_node_writer import SessionNodeWriter

    head_id = head_id or (db.get_session(session_id) or {}).get('head_id')
    graph = SessionNodeWriter(db, session_id).load()
    history = rendered_history(db, session_id, head_id)
    ids = render_context(graph, head_id=head_id, frame_entry_seq=-1)
    _, boundary = _aged_code_ids(graph, ids)
    manifest = {'aged_before_seq': boundary}
    messages = render_dag_messages(graph, ids, manifest=manifest)
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
        rendered = render_dag_messages(graph, groups[row['id']], manifest=manifest)
        enriched.append({**row, 'content': _text(rendered),
                         '_context_tokens': estimate_history_tokens(rendered)})
    return CompactionView(graph, head_id, enriched, manifest, messages)
