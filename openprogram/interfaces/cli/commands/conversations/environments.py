"""``openprogram environments`` — where conversations run."""
from __future__ import annotations


def _print_list() -> int:
    from openprogram.host.environments import default_environment_id, list_environments
    default = default_environment_id()
    print(f"{'':2}{'id':16} {'kind':7} {'name':20} where")
    for env in list_environments():
        mark = "* " if env.id == default else "  "
        if env.kind == "ssh":
            where = env.ssh_target + (f":{env.workdir}" if env.workdir else "")
        elif env.kind == "docker":
            where = env.image
        else:
            where = "this computer"
        print(f"{mark}{env.id:16} {env.kind:7} {env.name[:20]:20} {where}")
    print("\n* = default for new conversations")
    return 0


def dispatch(args) -> int:
    from openprogram.host.environments import (
        EnvironmentError,
        delete_environment,
        get_environment,
        save_environment,
        set_default_environment,
    )
    verb = getattr(args, "environments_verb", None)
    if verb in (None, "list"):
        return _print_list()
    try:
        if verb == "add":
            env = save_environment({
                "id": args.id, "kind": args.kind, "name": args.name,
                "ssh_target": args.target, "workdir": args.workdir,
                "image": args.image,
            })
            if args.default:
                set_default_environment(env.id)
            print(f"Saved {env.kind} environment {env.id!r}"
                  + (" (default)" if args.default else ""))
            return 0
        if verb == "rm":
            if get_environment(args.id) is None:
                print(f"[error] unknown environment {args.id!r}")
                return 1
            delete_environment(args.id)
            print(f"Removed {args.id!r}. Conversations created in it stop "
                  "running until it is added back.")
            return 0
        if verb == "default":
            set_default_environment(args.id)
            print(f"New conversations now start in {args.id!r}.")
            return 0
        if verb == "test":
            env = get_environment(args.id)
            if env is None:
                print(f"[error] unknown environment {args.id!r}")
                return 1
            from openprogram.host.environments.check import check_environment
            ok, detail = check_environment(env)
            print(("ok: " if ok else "failed: ") + detail)
            return 0 if ok else 1
    except EnvironmentError as exc:
        print(f"[error] {exc}")
        return 1
    print(f"[error] unknown verb {verb!r}")
    return 2
