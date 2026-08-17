# CLI Naming Convention

Every `openprogram` subcommand follows the same shape so users can
guess new commands from ones they already know.

## Rule

```
openprogram <noun> [<noun> ...] <verb> [<arg> ...]
```

- **Exactly one verb per command.** It is always the last word before
  positional arguments. A command must never have two verbs, and
  must never mix verbs into the middle of the noun stack.
- **Nouns come first, verb comes last.** Additional nouns stack in
  front of the verb to narrow the namespace.
- **Nouns may be plural.** Use plural when the namespace represents a
  collection (`providers`, `profiles`, `models`, `channels`). Use
  singular only when there is exactly one thing and it can never have
  siblings (rare).
- **Verbs are simple present, no suffix.** `list`, `status`, `add`,
  `remove`, `login`, `logout`, `set`, `get`, `discover`, `adopt`,
  `doctor`, `setup`. Not `listing`, not `lists`, not `added`.
- **Positional arguments come after the verb.** `openprogram providers
  auth login codex` — `codex` is the target of the `login` verb.
- **Flags use double-dash kebab-case.** `--profile`, `--display-name`,
  `--max-poll-seconds`. Never camelCase, never underscores.

## Examples (current and future)

```
openprogram providers login <prov>               ✓
openprogram providers list                       ✓
openprogram providers status <prov>              ✓
openprogram providers accounts list              ✓  (nouns stack: providers > accounts)
openprogram providers accounts create <n>        ✓
openprogram providers doctor                     ✓
openprogram providers setup                      ✓  (interactive wizard)

openprogram providers models list                (future, same pattern)
openprogram providers aliases add <from> <to>    (future, nouns stack)
openprogram channels login discord               (future, same pattern in different domain)
openprogram tools login github                   (future)
```

## When to add a namespace layer

Only add a middle noun (e.g. `providers auth login` instead of
`providers login`) when the parent noun genuinely needs to split into
*multiple* sibling subgroups. If the parent only ever speaks about
one subgroup, collapse the layer — a middle noun with no siblings is
dead weight.

For example, OpenClaw keeps `openclaw models auth login` because
`models` also has `aliases`, `list`, and other siblings. We keep
`providers login` flat because every verb on `providers` is
auth-adjacent.

## Why this rule

1. Discoverability — typing `openprogram providers auth <TAB>` lists
   every action available on that namespace. No hunting.
2. Extensibility — new domains slot in as sibling nouns at any level
   without colliding. `providers models list` doesn't conflict with
   `providers auth list`.
3. Mirrors what mature CLIs converged on:
   - `openclaw models auth login`, `openclaw models aliases add`
   - `gh auth login`, `gh repo create`
   - `docker container ls`, `docker image prune`
   - `kubectl get pods`, `kubectl delete service <name>`

## Anti-patterns — do not do these

- ❌ `openprogram login` — verb at top level, no namespace, clashes as
  soon as we have a second login target.
- ❌ `openprogram providerAuth login` — camelCase names, violates the
  noun-stack rule (should be two words: `providers auth`).
- ❌ `openprogram list-providers` — hyphenated compound verb-noun,
  locks the verb into the noun. Use `providers list`.
- ❌ `openprogram providers listing` — wrong verb form.

## How to add a new command

1. Pick the deepest noun namespace the command belongs to. If none
   exists, create one — but reuse existing namespaces whenever the
   command is a sibling of existing commands.
2. Pick the verb. Prefer verbs already used elsewhere in the CLI
   (`list`, `add`, `remove`, `set`, `status`) over inventing new ones.
3. Wire it under the appropriate `argparse` subparser tree, following
   the package boundary:
   - Command metadata + argparse wiring: `openprogram/cli/parser.py`
   - Command logic: a topic module under `openprogram/cli/commands/`
   - Top-level dispatch only: `openprogram/cli/__init__.py`
