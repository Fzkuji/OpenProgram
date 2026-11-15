"""`openprogram dev`: the development instance and promotion to stable."""
from __future__ import annotations

import sys


def _short(value: str | None) -> str:
    return value[:10] if value else "-"


def _cmd_dev(args) -> int:
    from openprogram.operations.worker import dev_instance as instances

    try:
        if args.dev_verb == "deploy":
            revision = instances.deploy_dev(args.ref)
            print(f"development instance now serves {_short(revision)} "
                  f"at http://127.0.0.1:{instances.DEV_PORT}")
            return 0
        if args.dev_verb == "open":
            import subprocess
            import webbrowser
            url = instances.auth_url()
            # `open` hands the link to the default browser without echoing it.
            if sys.platform == "darwin":
                subprocess.run(["open", url], check=True)
            else:
                webbrowser.open(url)
            print(f"opened the development instance, signed in (port {instances.DEV_PORT})")
            return 0
        if args.dev_verb == "copy-models":
            result = instances.copy_models()
            print(f"copied {len(result['copied'])} provider account(s) to the development instance")
            if result["oauth_not_copied"]:
                print("OAuth logins are not shared (their refresh tokens rotate); sign in to these "
                      "again in the development instance if you need them:")
                for name in result["oauth_not_copied"]:
                    print(f"  {name}")
            print("restart the development worker to load them: openprogram --profile dev restart")
            return 0
        if args.dev_verb == "auth-url":
            print(instances.auth_url())
            return 0
        if args.dev_verb == "promote":
            revision = instances.promote(args.revision, on_wait=lambda text: print(text, file=sys.stderr, flush=True))
            print(f"stable now runs {_short(revision)}")
            return 0
        value = instances.status()
        if getattr(args, "json", False):
            print(instances.as_json(value))
            return 0
        stable, dev = value["stable"], value["dev"]
        print(f"stable  {_short(stable['installed_revision'])}  branch stable={_short(stable['branch_revision'])}  "
              f":{stable['port']} {'healthy' if stable['healthy'] else 'down'}")
        print(f"dev     {_short(dev['deployed_revision'])}  branch {dev['branch']}={_short(dev['branch_revision'])}  "
              f":{dev['port']} {'healthy' if dev['healthy'] else 'down'}")
        pending = value["promotable"]
        if pending:
            print(f"\n{len(pending)} commit(s) promotable to stable:")
            for item in pending[:20]:
                print(f"  {item['sha'][:10]}  {item['subject']}")
        return 0
    except instances.InstanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
