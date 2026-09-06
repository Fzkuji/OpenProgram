"""Read-only Activity membership from the existing conversation branch index."""
from __future__ import annotations


def conversation_activity_branches(items: list[dict], *, conversation_session_id: str | None = None, session_store=None) -> list[dict]:
    from openprogram.store import SessionNodeWriter
    if session_store is None:
        from openprogram.agent.session_db import default_db
        session_store = default_db()

    branches = []
    emitted = set()
    for sid in dict.fromkeys(item['session_id'] for item in items):
        scoped = [item for item in items if item['session_id'] == sid]
        nodes = SessionNodeWriter(session_store, sid).load().nodes
        for tip in session_store.list_branches(sid):
            head = tip['head_msg_id']
            ancestry = set()
            pending = [head]
            while pending:
                key = pending.pop()
                if not key or key in ancestry:
                    continue
                ancestry.add(key)
                node = nodes.get(key)
                if node is not None:
                    pending.append(node.predecessor)
                    # A summary replaces context, not the conversation's history.
                    pending.extend((node.metadata or {}).get('covers_ids') or [])
            members = []
            anchors = []
            for item in scoped:
                display = (item.get('snapshot') or {}).get('display') or {}
                assistant = display.get('assistant_message_id')
                user = display.get('user_message_id')
                # Retry siblings share a user anchor; their assistant anchors
                # distinguish actual branches. Before admission persists the
                # assistant, the user node is the best available association.
                anchor = assistant if assistant in nodes else user
                if anchor and anchor in ancestry:
                    members.append(item['execution_id'])
                    anchors.append(anchor)
            if members:
                # A called session is not authorized wholesale. Do not expose
                # a later unrelated tip or its title via shared ancestry.
                name = tip.get('name')
                if conversation_session_id is not None and sid != conversation_session_id:
                    head = max(anchors, key=lambda key: getattr(nodes.get(key), "seq", -1))
                    name = None
                identity = (sid, head)
                if identity in emitted:
                    continue
                emitted.add(identity)
                branches.append({
                    'branch_id': f'{sid}:{head}', 'session_id': sid,
                    'head_msg_id': head, 'name': name,
                    'execution_ids': members,
                })
    return branches
