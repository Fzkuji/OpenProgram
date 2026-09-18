"""Create a minimal package whose UI and Agent share backend operations."""
import html
import json
from pathlib import Path
from .catalog import ID

BACKEND = '''def read(value, context):
    return context.load()


def save(value, context):
    return context.save({"text": value["text"]}, expected_version=value["version"])


operations = {"read": read, "save": save}
'''
UI = '''<!doctype html><html><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px system-ui;margin:24px}}textarea{{display:block;width:95%;height:160px;margin:12px 0}}button{{padding:8px 16px}}</style>
<h1>{title}</h1><textarea id="text" aria-label="Text"></textarea><button id="save">Save</button><p id="status" role="status"></p>
<script>
let version = 0;
async function run(operation, input = {{}}) {{
  let result = await openprogramApp.run(operation, input);
  while (["pending", "running"].includes(result.status)) {{
    await new Promise(resolve => setTimeout(resolve, 100));
    result = await openprogramApp.status(result.id);
  }}
  if (result.status !== "completed") throw Error(result.error || result.status);
  return result.result;
}}
async function load() {{ const state = await run("read"); version = state.version; document.getElementById("text").value = state.value.text || ""; }}
document.getElementById("save").onclick = async () => {{
  try {{ const result = await run("save", {{text: document.getElementById("text").value, version}}); version = result.version; document.getElementById("status").textContent = "Saved"; }}
  catch (error) {{ document.getElementById("status").textContent = error.message; }}
}};
openprogramApp.ready.then(load).catch(error => {{ document.getElementById("status").textContent = error.message; }});
</script></html>
'''


def create(path: str, app_id: str, title: str) -> dict:
    if not isinstance(app_id, str) or not ID.fullmatch(app_id):
        raise ValueError('invalid application id')
    if not isinstance(title, str) or not 1 <= len(title) <= 160:
        raise ValueError('invalid title')
    target = Path(path).expanduser().resolve()
    # Never populate or overwrite an existing user directory.
    target.mkdir(parents=False, exist_ok=False)
    empty = {'type': 'object', 'additionalProperties': False}
    definition = {'id': app_id, 'title': title, 'version': '1.0.0', 'scope': 'global',
        'capabilities': ['storage.app'], 'ui': {'root': 'ui', 'entry': 'index.html'},
        'backend': {'kind': 'python', 'entry': 'backend.operations:operations'},
        'operations': {
            'read': {'agent': True, 'input': empty, 'output': {'type': 'object'}},
            'save': {'agent': True, 'input': {'type': 'object', 'properties': {
                'text': {'type': 'string'}, 'version': {'type': 'integer', 'minimum': 0}},
                'required': ['text', 'version'], 'additionalProperties': False}, 'output': {'type': 'object'}},
        }}
    (target / 'backend').mkdir()
    (target / 'backend/__init__.py').write_text('')
    (target / 'backend/operations.py').write_text(BACKEND)
    (target / 'ui').mkdir()
    (target / 'ui/index.html').write_text(UI.format(title=html.escape(title)))
    (target / 'application.json').write_text(json.dumps(definition, indent=2) + '\n')
    return {'path': str(target), 'application': definition,
            'next': 'Review package, then POST /api/applications/install with path and trust=true.'}
