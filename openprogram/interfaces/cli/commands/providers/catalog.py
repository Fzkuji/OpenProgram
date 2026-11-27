"""``openprogram providers available|list|discover|adopt`` — the provider catalogue and credential adoption."""
from __future__ import annotations
import json
import sys
from typing import Any, Optional
from openprogram.models.auth.store import get_store
from openprogram.models.auth.adoption import _sources, run_adopt_all, source_by_id as _source_by_id


def _cmd_available(
    query: Optional[str], as_json: bool, configured_only: bool = False,
) -> int:
    """List every provider OpenProgram can configure (built-in + the
    models.dev community catalogue), optionally filtered by a search term.
    The ``PROVIDER`` id column is exactly what you pass to
    ``openprogram providers login <id>``."""
    try:
        from openprogram.models.listing import list_providers
        rows = list(list_providers() or [])
    except Exception as e:  # noqa: BLE001
        print(f"Could not load the provider catalogue: {e}", file=sys.stderr)
        return 1

    q = (query or "").strip().lower()
    if q:
        rows = [
            r for r in rows
            if q in (r.get("id") or "").lower()
            or q in (r.get("label") or "").lower()
        ]
    if configured_only:
        rows = [r for r in rows if r.get("configured")]
    # Configured providers first, then alphabetical by id.
    rows.sort(key=lambda r: (not r.get("configured"), (r.get("id") or "")))

    if as_json:
        slim = [{
            "id": r.get("id"),
            "label": r.get("label"),
            "configured": bool(r.get("configured")),
            "enabled": bool(r.get("enabled")),
            "api_key_env": r.get("api_key_env"),
            "model_count": r.get("model_count"),
            "supports_fetch": bool(r.get("supports_fetch")),
        } for r in rows]
        print(json.dumps(slim, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("No providers match that search." if q else "No providers found.")
        return 0

    def _col(key: str, cap: int, header: str) -> int:
        return max(
            min(cap, max((len(str(r.get(key) or "")) for r in rows), default=0)),
            len(header),
        )
    id_w = _col("id", 30, "PROVIDER")
    lbl_w = _col("label", 28, "LABEL")
    env_w = _col("api_key_env", 26, "KEY ENV")

    def _fit(s: str, w: int) -> str:
        # Truncate over-wide values so columns stay aligned.
        s = s or ""
        return s if len(s) <= w else s[: w - 1] + "…"

    print(f"{'PROVIDER':<{id_w}}  {'LABEL':<{lbl_w}}  "
          f"{'KEY ENV':<{env_w}}  MODELS  CFG")
    for r in rows:
        mc = r.get("model_count")
        print(
            f"{_fit(r.get('id'), id_w):<{id_w}}  "
            f"{_fit(r.get('label'), lbl_w):<{lbl_w}}  "
            f"{_fit(r.get('api_key_env') or '—', env_w):<{env_w}}  "
            f"{(str(mc) if mc is not None else '—'):>6}  "
            f"{'✓' if r.get('configured') else '·'}"
        )
    print(
        f"\n{len(rows)} provider(s)"
        + (f" matching {query!r}" if q else "")
        + ".  Add a key with:  openprogram providers keys <PROVIDER> add"
    )
    return 0



def _cmd_list(as_json: bool) -> int:
    """Every provider's keys and sign-ins, in each provider's order."""
    from openprogram.models.listing.manage import provider_credentials
    providers = [p.provider_id for p in get_store().list_pools()]
    if as_json:
        print(json.dumps([provider_credentials(p) for p in providers], indent=2, ensure_ascii=False))
        return 0
    if not providers:
        print("No keys or sign-ins yet. Try:")
        print("  openprogram providers discover              # sign-ins other tools hold")
        print("  openprogram providers keys <provider> add   # add a key or sign in")
        return 0
    from .keys import _print_list
    for i, provider in enumerate(providers):
        if i:
            print()
        _print_list(provider)
    return 0



def _cmd_discover(as_json: bool) -> int:
    from .formatting import _payload_summary
    store = get_store()
    found: list[dict[str, Any]] = []
    for src in _sources():
        try:
            creds = src.try_import(store.root)
        except Exception as e:
            found.append({"source_id": src.source_id, "error": str(e)})
            continue
        for cred in creds:
            found.append({
                "source_id": src.source_id,
                "provider": cred.provider_id,
                "name": cred.display_label(),
                "kind": cred.kind,
                "preview": _payload_summary(cred),
                "read_only": cred.read_only,
            })

    if as_json:
        print(json.dumps(found, indent=2))
        return 0

    if not found:
        print("Nothing found: no Codex, Qwen or GitHub CLI sign-in on this machine.\n"
              "Keys in environment variables need no adopting; they are listed already.")
        return 0

    print(f"Found {len(found)} adoptable credential(s):\n")
    print(f"{'source':28s}  {'provider':24s}  preview")
    for f in found:
        if "error" in f:
            print(f"{f['source_id']:28s}  (error)                   {f['error']}")
            continue
        print(f"{f['source_id']:28s}  {f['provider']:24s}  {f['preview']}")
    print("\nAdopt one with:  openprogram providers adopt <source_id>")
    return 0



def _cmd_adopt(source_id: str) -> int:
    from .formatting import _payload_summary
    store = get_store()
    src = _source_by_id(source_id)
    if src is None:
        print(f"Unknown source: {source_id!r}. "
              f"Run `openprogram providers discover` to see available ids.",
              file=sys.stderr)
        return 1
    try:
        creds = src.try_import(store.root)
    except Exception as e:
        print(f"Source failed: {e}", file=sys.stderr)
        return 1
    if not creds:
        print(f"Source {source_id!r} holds no sign-in to adopt.", file=sys.stderr)
        return 1
    for cred in creds:
        kept = store.add_credential(cred)
        print(f"Adopted {kept.provider_id}: {kept.display_label()} ({_payload_summary(kept)})")
    return 0



def _cmd_adopt_all() -> int:
    """Adopt every sign-in the sources find (CLI wrapper over
    :func:`run_adopt_all`). Exit 0 on success (even zero adopted), 1 if
    any source errored."""
    result = run_adopt_all()
    for item in result["events"]:
        level = item["level"]
        if level == "adopted":
            print(f"  + {item['provider_id']}: {item['preview']}")
        elif level == "error":
            print(f"  ! {item['source_id']}: {item['error']}", file=sys.stderr)
    print(f"\nAdopted {result['adopted']} · "
          f"skipped {result['skipped']} · errored {result['errored']}")
    return 1 if result["errored"] else 0
