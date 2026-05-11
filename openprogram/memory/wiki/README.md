# `openprogram.memory.wiki` — portable Obsidian-style wiki subsystem

A self-contained subpackage. The vault format is plain markdown + YAML
frontmatter + `[[wikilink]]` references, fully compatible with
Obsidian out of the box.

## What's inside

```
wiki/
├── __init__.py     Public API — Wiki class + module-level re-exports
├── access.py       Path-based read API (find / read / tree / iter_pages)
├── helpers.py      Frontmatter parse/dump, folder_tree, wikilink rewrite,
│                   code-fence masking, find_node, topic_path
├── ops.py          Lint / rename / relink / prune_broken_links / backlinks /
│                   unlinked_mentions / survey / refactor / git_commit
├── ingest.py       Two-step agentic ingest (analyse → write via runtime.exec)
└── enrich.py       Wikilink enrichment — outbound + inbound passes
```

## Dependencies (intentionally narrow)

* Python standard library (`pathlib`, `re`, `subprocess`, `json`, `sqlite3`)
* `openprogram.memory.store` — only used to resolve the default vault path;
  pass a custom `root` to bypass.
* `openprogram.memory.index` — for the persistent link / FTS cache.
* For agentic ops only: a Runtime injected at construction time, or
  `openprogram.agents.runtime_registry._build_autodetect()` as the
  fallback.

To lift this into another project: drop the `wiki/` folder in, replace
the two `from .. import store` lines with that project's path helper,
and provide a Runtime when calling `ingest_session` / `survey` /
`refactor`.

## Quick start

```python
from openprogram.memory.wiki import Wiki

# Default vault (openprogram's state dir)
w = Wiki()

# Or your own vault
w = Wiki(root="~/my/vault")

# Read
print(w.tree())                    # folder outline
print(w.read("Claude Max Proxy"))  # page contents by filename

# Lint & link maintenance
print(w.lint())
w.rename("OldName", "NewName")     # move + cascade rewrite
w.relink("OldName", "NewName")     # cascade only, no file move
w.prune_broken_links(dry_run=False)

# Backlinks panel (Obsidian-style)
for hit in w.backlinks("Claude Max Proxy"):
    print(hit["page"], hit["snippet"])

# Agentic ops (need a runtime)
w = Wiki(runtime=my_runtime)
w.ingest_session(session_id, messages)
w.survey("Tools")                  # rewrite a topic from its children
w.refactor("OpenProgram")          # split when >=6 children

# Git audit
w.git_commit("manual cleanup pass")
```

## Schema (governance — read by ingest agent on every run)

The vault itself carries three governance docs at its root:

* `AGENTS.md` — agent entry point, links to SCHEMA + purpose
* `SCHEMA.md` — full schema spec (frontmatter, 7 type values,
  folder-hierarchy rules, wikilink syntax)
* `purpose.md` — scope rules; what to ingest vs ignore

These are read-only and must not be edited by the agent. They
co-evolve with the user.

## Public surface

| Method               | Where    | Purpose                              |
|----------------------|----------|--------------------------------------|
| `find(name)`         | access   | Resolve filename stem to absolute path |
| `read(target)`       | access   | Read by path or filename             |
| `tree()`             | access   | Render folder tree                   |
| `iter_pages()`       | access   | Iterate content pages                |
| `page_type(path)`    | access   | Read `type:` frontmatter             |
| `lint()`             | ops      | Structural health report             |
| `rename(old, new)`   | ops      | Move + cascade rewrite               |
| `relink(old, new)`   | ops      | Cascade rewrite only                 |
| `prune_broken_links` | ops      | Strip broken `[[ ]]`                 |
| `backlinks(name)`    | ops      | Inbound references                   |
| `unlinked_mentions`  | ops      | Plain-text mentions not yet linked   |
| `survey(topic)`      | ops      | Agentic; rewrite topic from children |
| `refactor(topic)`    | ops      | Agentic; split overgrown topic       |
| `git_commit(msg)`    | ops      | Stage + commit vault changes         |
| `ingest_session`     | ingest   | Two-step agentic conversation ingest |
| `enrich_*`           | enrich   | Add wikilinks post-write             |

## Tools that pair with this subsystem

`openprogram/tools/memory/memory.py` exposes seven agent tools backed
by this subpackage — `memory_browse`, `memory_get`, `memory_recall`,
`memory_reflect`, `memory_note`, `memory_ingest`, `memory_lint`,
`memory_backlinks`. Drop those when porting to a non-OpenProgram
host; replace with whatever tool registration the host uses.
