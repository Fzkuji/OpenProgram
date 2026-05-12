"""``openprogram agents <verb>`` dispatcher."""
from __future__ import annotations

import json
import sys


def _dispatch_agents_verb(args, parser) -> None:
    from openprogram.agents import manager as _A
    verb = getattr(args, "agents_verb", None)
    if verb == "list":
        rows = _A.list_all()
        if not rows:
            print("No agents. Create one with `openprogram agents add main`.")
            return
        print(f"{'id':16} {'default':8} {'provider/model':40} effort")
        for a in rows:
            pm = f"{a.model.provider}/{a.model.id}" if a.model.provider else "-"
            print(f"{a.id:16} {str(a.default):8} {pm:40} "
                  f"{a.thinking_effort}")
        return
    if verb == "add":
        try:
            a = _A.create(
                args.id,
                name=args.name,
                provider=args.provider,
                model_id=args.model,
                thinking_effort=args.effort,
                make_default=getattr(args, "default", False),
            )
        except ValueError as e:
            print(f"[error] {e}")
            sys.exit(1)
        print(f"Created agent {a.id!r} "
              f"(provider={a.model.provider or '-'}, "
              f"model={a.model.id or '-'}, default={a.default})")
        return
    if verb == "rm":
        _A.delete(args.id)
        print(f"Agent {args.id!r} removed")
        return
    if verb == "show":
        a = _A.get(args.id)
        if a is None:
            print(f"No agent {args.id!r}")
            sys.exit(1)
        print(json.dumps(a.to_dict(), indent=2, sort_keys=True, default=str))
        try:
            from openprogram.channels import bindings as _b
            rows = _b.list_for_agent(a.id)
        except Exception:
            rows = []
        print()
        print("Channel bindings:")
        if not rows:
            print("  (none — inbound messages fall back to the default "
                  "agent if that's this one, otherwise ignored)")
            return
        for r in rows:
            m = r["match"]
            peer = m.get("peer") or {}
            peer_str = (f"  peer={peer.get('kind','?')}:{peer.get('id','?')}"
                        if peer else "")
            print(f"  · {r['id']}  channel={m.get('channel','*')}  "
                  f"account={m.get('account_id','*')}{peer_str}")
        return
    if verb == "set-default":
        _A.set_default(args.id)
        print(f"Default agent is now {args.id!r}")
        return
    parser.print_help()
