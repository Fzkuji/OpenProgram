"""The code side of the wiki, in plain code: every module with its classes, functions,
docstrings and imports (Python through `ast`, other languages through a few regexes for
function and class names), the calls between the project's own functions, the entry
points and the configuration files. Nothing is executed.
"""

import ast
import configparser
import json
import re
import tomllib
import warnings
from pathlib import Path

SKIP_DIRS = {".git", ".hg", ".svn", "drafts", "workflow", "venv", ".venv", "env", "node_modules", "__pycache__",
             "site-packages", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
             "target", "vendor", "third_party", "docs", "wandb", "outputs", "checkpoints", "logs"}
TEST_DIRS = {"tests", "test", "testing"}
PY = (".py",)
OTHER = {".js": "javascript", ".mjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
         ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".cu": "cpp", ".c": "cpp", ".h": "cpp", ".hpp": "cpp",
         ".rs": "rust", ".go": "go"}
CONFIG_EXT = (".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".conf")
CONFIG_SKIP = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "tsconfig.json", ".pre-commit-config.yaml"}
MAX_FILES = 400          # source files indexed
MAX_FILE = 1_000_000     # bytes of one source file
ENTRY_NAMES = re.compile(r"^(?:train|eval|evaluate|test_model|main|run|inference|infer|predict|sample|generate|"
                         r"finetune|pretrain|benchmark)(?:[_-].*)?$", re.I)
MODULE = "<module>"      # the top-level statements of a file, as one pseudo-function

# Regex fallback per language: (kind, pattern with the name in group 1).
PATTERNS = {
    "javascript": [("class", r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"),
                   ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
                   ("function", r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")],
    "cpp": [("class", r"^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+([A-Za-z_]\w*)\s*(?::[^{;]*)?\{"),
            ("function", r"^\s*(?:__global__\s+|__device__\s+|static\s+|inline\s+|virtual\s+|constexpr\s+)*"
                         r"[A-Za-z_][\w:<>,\s\*&]*?[\s\*&]([A-Za-z_]\w*(?:::[A-Za-z_~]\w*)?)\s*\([^;{]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{")],
    "rust": [("class", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)"),
             ("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)")],
    "go": [("class", r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)"),
           ("function", r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(")],
}
PATTERNS["typescript"] = PATTERNS["javascript"] + [("class", r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")]
CPP_NOT = {"if", "for", "while", "switch", "return", "catch", "sizeof"}


def skipped(rel: Path) -> bool:
    return any(p in SKIP_DIRS or (p.startswith(".") and p not in (".", "..")) for p in rel.parts[:-1]) \
        or rel.name == ".env" or rel.name.startswith(".")


def is_test(rel: str) -> bool:
    parts = Path(rel).parts
    return any(p in TEST_DIRS for p in parts[:-1]) or Path(rel).name.startswith("test_") or rel.endswith("_test.py")


def source_files(code: Path) -> list[Path]:
    """The source files of the code folder, Python first, at most MAX_FILES."""
    found = []
    for path in sorted(code.rglob("*")):
        try:
            rel = path.relative_to(code)
        except ValueError:
            continue
        if path.suffix not in PY and path.suffix not in OTHER:
            continue
        if skipped(rel) or not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE:
            continue
        found.append(path)
    found.sort(key=lambda p: (p.suffix not in PY, is_test(p.relative_to(code).as_posix()), len(p.relative_to(code).parts),
                              p.relative_to(code).as_posix()))
    return found[:MAX_FILES]


def module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or Path(rel).stem


# Python ------------------------------------------------------------------------------------

def first_line(doc: str) -> str:
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def signature(node) -> str:
    try:
        return f"{node.name}({ast.unparse(node.args)})"
    except Exception:  # noqa: BLE001 - an odd node still gets a name
        return f"{node.name}(...)"


def call_name(func) -> str:
    """The dotted name a call is made through: f, mod.f, self.f, self.attr.f."""
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    elif isinstance(func, ast.Call):
        inner = call_name(func.func)
        if inner:
            parts.append(inner + "()")
    else:
        return ""
    return ".".join(reversed(parts))


class Calls(ast.NodeVisitor):
    """The calls made in one function body, without descending into nested defs."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def visit_Call(self, node):
        name = call_name(node.func)
        if name:
            self.calls.append((name, node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        pass


def body_calls(nodes) -> list[tuple[str, int]]:
    v = Calls()
    for n in nodes:
        v.visit(n)
    return v.calls


def self_attributes(cls: ast.ClassDef) -> dict[str, str]:
    """self.x = Name(...) assignments in a class: attribute -> the called name (a layer's class)."""
    out = {}
    for node in ast.walk(cls):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        value = getattr(node, "value", None)
        if not isinstance(value, ast.Call):
            continue
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                name = call_name(value.func)
                if name:
                    out.setdefault(t.attr, name)
        # nn.ModuleDict(dict(wte=nn.Embedding(...), h=nn.ModuleList([Block(...)])))
    for node in ast.walk(cls):
        if isinstance(node, ast.keyword) and node.arg and isinstance(node.value, ast.Call):
            name = call_name(node.value.func)
            inner = [call_name(c.func) for c in ast.walk(node.value) if isinstance(c, ast.Call)]
            project = next((n for n in inner if n and n[0].isupper() and not n.startswith("nn.")), "")
            out.setdefault(node.arg, project or name)
    return out


def main_block(tree: ast.Module) -> ast.If | None:
    for node in tree.body:
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test):
            return node
    return None


def python_module(rel: str, text: str) -> dict:
    """One Python file: docstring, imports, classes, functions (with spans and calls),
    the top-level code as the <module> pseudo-function, and whether it is a script."""
    mod = {"file": rel, "module": module_name(rel), "language": "python", "lines": text.count("\n") + 1,
           "doc": "", "imports": [], "classes": [], "functions": [], "parsed": True, "error": "",
           "main": 0, "argparse": False}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")          # invalid escapes in the parsed code are its own business
            tree = ast.parse(text)
    except (SyntaxError, ValueError) as e:
        mod.update(parsed=False, error=f"{e.__class__.__name__}: {e}")
        return mod
    mod["doc"] = ast.get_docstring(tree) or ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mod["imports"] += [{"name": a.name, "as": a.asname or a.name.split(".")[0], "level": 0, "from": ""}
                               for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod["imports"] += [{"name": a.name, "as": a.asname or a.name, "level": node.level,
                                "from": node.module or ""} for a in node.names]
    mod["argparse"] = any(c[0].endswith("ArgumentParser") or c[0].startswith("fire.") or c[0] in ("tyro.cli", "hydra.main")
                          for c in body_calls(tree.body))
    block = main_block(tree)
    mod["main"] = block.lineno if block else 0

    def function(node, cls: str = "", attrs: dict | None = None) -> dict:
        calls = body_calls(node.body)
        if cls and attrs and node.name != "__init__":     # for block in self.transformer.h: block(x)
            used = {a.attr for a in ast.walk(node) if isinstance(a, ast.Attribute)}
            calls += [(f"self.{a}", node.lineno) for a in sorted(used & set(attrs))
                      if attrs[a].split(".")[-1][:1].isupper() and not attrs[a].startswith("nn.")]
        return {"name": node.name, "qualname": f"{cls}.{node.name}" if cls else node.name, "class": cls,
                "line": node.lineno, "end": getattr(node, "end_lineno", node.lineno) or node.lineno,
                "signature": signature(node), "doc": ast.get_docstring(node) or "",
                "calls": calls, "attrs": attrs or {},
                "decorators": [ast.unparse(d) for d in node.decorator_list]}

    top = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod["functions"].append(function(node))
        elif isinstance(node, ast.ClassDef):
            attrs = self_attributes(node)
            methods = [function(n, node.name, attrs) for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            bases = [ast.unparse(b) for b in node.bases]
            mod["classes"].append({"name": node.name, "line": node.lineno, "end": node.end_lineno or node.lineno,
                                   "doc": ast.get_docstring(node) or "", "bases": bases,
                                   "methods": [m["qualname"] for m in methods]})
            mod["functions"] += methods
        elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)) \
                and not isinstance(node, (ast.Import, ast.ImportFrom)):
            top.append(node)
    work = [n for n in top if not isinstance(n, (ast.Assign, ast.AnnAssign)) or isinstance(getattr(n, "value", None), ast.Call)
            or any(isinstance(c, ast.Call) for c in ast.walk(n))]
    if work:
        mod["functions"].append({"name": MODULE, "qualname": MODULE, "class": "", "line": top[0].lineno,
                                 "end": max(getattr(n, "end_lineno", n.lineno) or n.lineno for n in top),
                                 "signature": "top-level code", "doc": "", "calls": body_calls(top), "attrs": {},
                                 "decorators": [], "spans": [(n.lineno, getattr(n, "end_lineno", n.lineno) or n.lineno)
                                                             for n in top]})
    return mod


# Other languages ---------------------------------------------------------------------------

def other_module(rel: str, text: str, language: str) -> dict:
    """Function and class names with their lines; a span ends where the next definition starts."""
    mod = {"file": rel, "module": rel, "language": language, "lines": text.count("\n") + 1, "doc": "", "imports": [],
           "classes": [], "functions": [], "parsed": True, "error": "", "main": 0, "argparse": False}
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines, 1):
        for kind, pat in PATTERNS[language]:
            m = re.match(pat, line)
            if m and m.group(1).split("::")[-1] not in CPP_NOT:
                found.append((i, kind, m.group(1)))
                break
    for k, (line, kind, name) in enumerate(found):
        end = (found[k + 1][0] - 1) if k + 1 < len(found) else len(lines)
        if kind == "class":
            mod["classes"].append({"name": name, "line": line, "end": end, "doc": "", "bases": [], "methods": []})
        else:
            mod["functions"].append({"name": name.split("::")[-1], "qualname": name, "class": "", "line": line,
                                     "end": end, "signature": lines[line - 1].strip()[:160], "doc": "", "calls": [],
                                     "attrs": {}, "decorators": []})
            if name.split("::")[-1] == "main" or (language == "go" and name == "main"):
                mod["main"] = line
    names = {f["name"] for f in mod["functions"]}
    for f in mod["functions"]:
        body = "\n".join(lines[f["line"]:f["end"]])
        f["calls"] = [(n, 0) for n in sorted(names) if n != f["name"] and re.search(rf"(?<![\w.]){re.escape(n)}\s*\(", body)]
    return mod


# The whole code base -----------------------------------------------------------------------

def index(code: Path) -> dict:
    """Every module, the resolved call graph, the entry points and the config files."""
    modules = []
    for path in source_files(code):
        rel = path.relative_to(code).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        modules.append(python_module(rel, text) if path.suffix in PY else other_module(rel, text, OTHER[path.suffix]))
    by_name = {m["module"]: m for m in modules if m["language"] == "python"}
    resolve_imports(modules, by_name)
    edges = call_edges(modules, by_name)
    for m in modules:
        m["test"] = is_test(m["file"])
    return {"modules": modules, "edges": edges, "entries": entry_points(code, modules), "configs": config_files(code)}


def resolve_module(m: dict, imp: dict, by_name: dict) -> tuple[str, str]:
    """An import as (project module, name inside it or ''), or ('', '') when it is external."""
    package = m["module"].split(".")
    if not m["file"].endswith("__init__.py"):
        package = package[:-1]
    if imp["level"]:
        base = package[:len(package) - (imp["level"] - 1)] if imp["level"] > 1 else package
        target = ".".join(base + ([imp["from"]] if imp["from"] else []))
    else:
        target = imp["from"] or imp["name"]
    candidates = [target]
    if not imp["level"] and package:      # a script importing a sibling: from model import GPT
        candidates.append(".".join(package + [target]))
    for t in candidates:
        if imp["from"] or imp["level"]:
            if f"{t}.{imp['name']}" in by_name:
                return f"{t}.{imp['name']}", ""
            if t in by_name:
                return t, imp["name"]
        elif t in by_name:
            return t, ""
        else:
            for name in by_name:            # a suffix match: import mingpt.model from projects/x.py
                if name.endswith("." + t) or name == t:
                    return name, ""
    return "", ""


def resolve_imports(modules: list[dict], by_name: dict) -> None:
    for m in modules:
        m["internal"] = []
        m["aliases"] = {}
        for imp in m["imports"]:
            target, name = resolve_module(m, imp, by_name)
            if not target:
                continue
            if target not in m["internal"] and target != m["module"]:
                m["internal"].append(target)
            m["aliases"][imp["as"]] = (target, name)


def call_edges(modules: list[dict], by_name: dict) -> list[dict]:
    """Calls from one project function to another: (caller file::qualname, callee file::qualname, line)."""
    defs = {}          # (module, qualname) -> node id
    classes = {}       # (module, class) -> set of method qualnames
    for m in modules:
        for f in m["functions"]:
            defs[(m["module"], f["qualname"])] = f"{m['file']}::{f['qualname']}"
        for c in m["classes"]:
            classes[(m["module"], c["name"])] = set(c["methods"])
    edges, seen = [], set()
    for m in modules:
        for f in m["functions"]:
            src = f"{m['file']}::{f['qualname']}"
            for name, line in f["calls"]:
                for dst in targets(m, f, name, defs, classes):
                    if dst != src and (src, dst) not in seen:
                        seen.add((src, dst))
                        edges.append({"from": src, "to": dst, "line": line})
    return edges


def class_entry(module: str, cls: str, defs: dict, classes: dict, call: bool) -> list[str]:
    """Calling a class constructs it (its __init__); calling an instance runs forward/__call__."""
    methods = classes.get((module, cls), set())
    order = ("forward", "__call__") if call else ("__init__",)
    return [defs[(module, f"{cls}.{n}")] for n in order if f"{cls}.{n}" in methods][:1]


def lookup(m: dict, name: str, defs: dict, classes: dict, call: bool = False) -> list[str]:
    """A bare name in module m: a local def or class, or something imported from the project."""
    if (m["module"], name) in defs:
        return [defs[(m["module"], name)]]
    if (m["module"], name) in classes:
        return class_entry(m["module"], name, defs, classes, call)
    if name in m.get("aliases", {}):
        target, inner = m["aliases"][name]
        if inner:
            if (target, inner) in defs:
                return [defs[(target, inner)]]
            if (target, inner) in classes:
                return class_entry(target, inner, defs, classes, call)
    return []


def targets(m: dict, f: dict, name: str, defs: dict, classes: dict) -> list[str]:
    if m["language"] != "python":
        return [defs[(m["module"], n)] for n in [name] if (m["module"], n) in defs]
    parts = name.split(".")
    if len(parts) == 1:
        return lookup(m, parts[0], defs, classes)
    if parts[0] == "self" and len(parts) == 1 and f["class"]:      # self(x) runs the instance
        return class_entry(m["module"], f["class"], defs, classes, call=True)
    if parts[0] in ("self", "cls") and f["class"]:
        cls = f["class"]
        if len(parts) == 2:
            if (m["module"], f"{cls}.{parts[1]}") in defs:
                return [defs[(m["module"], f"{cls}.{parts[1]}")]]
            layer = f["attrs"].get(parts[1], "")           # self.attn(x): the layer assigned in __init__
            return lookup(m, layer.split(".")[-1], defs, classes, call=True) if layer else []
        layer = f["attrs"].get(parts[1], "")               # self.transformer.wte(...)
        return lookup(m, layer.split(".")[-1], defs, classes, call=True) if layer and layer[0].isupper() else []
    if parts[0] in m.get("aliases", {}) and len(parts) == 2:
        target, inner = m["aliases"][parts[0]]
        if not inner:                                       # module.func
            if (target, parts[1]) in defs:
                return [defs[(target, parts[1])]]
            if (target, parts[1]) in classes:
                return class_entry(target, parts[1], defs, classes, False)
        elif (target, inner) in classes and (target, f"{inner}.{parts[1]}") in defs:   # GPT.from_pretrained
            return [defs[(target, f"{inner}.{parts[1]}")]]
    if len(parts) == 2 and (m["module"], parts[0]) in classes and (m["module"], f"{parts[0]}.{parts[1]}") in defs:
        return [defs[(m["module"], f"{parts[0]}.{parts[1]}")]]
    return []


# Entry points and configs ------------------------------------------------------------------

def entry_points(code: Path, modules: list[dict]) -> list[dict]:
    """Scripts with a __main__ block or argument parsing, train/eval-named files, console
    scripts from pyproject.toml / setup.py / setup.cfg, and package.json bins and scripts."""
    out = []
    files = {m["file"]: m for m in modules}
    for m in modules:
        if m["test"]:
            continue
        why = []
        if m["main"]:
            why.append(f'__main__ block at line {m["main"]}' if m["language"] == "python" else f"main at line {m['main']}")
        if m["argparse"]:
            why.append("parses command-line arguments")
        if ENTRY_NAMES.match(Path(m["file"]).stem):
            why.append("named like a train/eval script")
        if why and m["language"] == "python" or m["main"]:
            out.append({"kind": "script", "name": m["file"], "file": m["file"], "target": "", "why": "; ".join(why)})
    scripts = {}
    py = code / "pyproject.toml"
    if py.is_file():
        try:
            data = tomllib.loads(py.read_text(encoding="utf-8", errors="replace"))
            scripts.update((data.get("project") or {}).get("scripts") or {})
            scripts.update(((data.get("tool") or {}).get("poetry") or {}).get("scripts") or {})
        except (tomllib.TOMLDecodeError, AttributeError):
            pass
    setup = code / "setup.py"
    if setup.is_file():
        text = setup.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"['\"]\s*([\w.-]+)\s*=\s*([\w.]+)\s*:\s*(\w+)\s*['\"]", text):
            scripts.setdefault(m.group(1), f"{m.group(2)}:{m.group(3)}")
    cfg = code / "setup.cfg"
    if cfg.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read_string(cfg.read_text(encoding="utf-8", errors="replace"))
            for line in parser.get("options.entry_points", "console_scripts", fallback="").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    scripts.setdefault(k.strip(), v.strip())
        except configparser.Error:
            pass
    for name, target in scripts.items():
        mod, _, func = str(target).partition(":")
        hit = next((m for m in modules if m["module"] == mod), None)
        out.append({"kind": "console_script", "name": name, "file": hit["file"] if hit else "",
                    "target": str(target), "why": "console script"})
    pkg = code / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            bins = data.get("bin") or {}
            bins = {data.get("name", "bin"): bins} if isinstance(bins, str) else bins
            for name, target in bins.items():
                out.append({"kind": "bin", "name": name, "file": str(target).lstrip("./") if str(target).lstrip("./") in files else "",
                            "target": str(target), "why": "package.json bin"})
            for name, target in (data.get("scripts") or {}).items():
                out.append({"kind": "npm_script", "name": name, "file": "", "target": str(target), "why": "package.json script"})
        except (json.JSONDecodeError, AttributeError):
            pass
    return out


def config_files(code: Path) -> list[dict]:
    """Configuration files in the top two levels and in folders named config(s)/conf."""
    out = []
    for path in sorted(code.rglob("*")):
        try:
            rel = path.relative_to(code)
        except ValueError:
            continue
        if path.suffix not in CONFIG_EXT or path.name in CONFIG_SKIP or skipped(rel) or not path.is_file():
            continue
        in_config = any(p.lower() in ("config", "configs", "conf", "cfg") for p in rel.parts[:-1])
        if len(rel.parts) > 2 and not in_config:
            continue
        if path.name in ("package.json", "pyproject.toml", "setup.cfg") and not in_config:
            kind = "project"
        else:
            kind = "config"
        keys = []
        try:
            if path.suffix == ".json" and path.stat().st_size < 200_000:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                keys = list(data)[:20] if isinstance(data, dict) else []
            elif path.suffix in (".yaml", ".yml") and path.stat().st_size < 200_000:
                keys = re.findall(r"^([A-Za-z_][\w-]*)\s*:", path.read_text(encoding="utf-8", errors="replace"), re.M)[:20]
            elif path.suffix == ".toml" and path.stat().st_size < 200_000:
                keys = list(tomllib.loads(path.read_text(encoding="utf-8", errors="replace")))[:20]
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeError, ValueError):
            keys = []
        out.append({"file": rel.as_posix(), "kind": kind, "keys": [str(k) for k in keys]})
        if len(out) >= 60:
            break
    return out


# Lookups used by the checks ----------------------------------------------------------------

def nodes(idx: dict) -> dict[str, dict]:
    """Every function by its id file::qualname, with its module's file."""
    out = {}
    for m in idx["modules"]:
        for f in m["functions"]:
            out[f"{m['file']}::{f['qualname']}"] = dict(f, file=m["file"], module=m["module"])
    return out


def in_span(fn: dict, line: int) -> bool:
    if fn.get("spans"):
        return any(a <= line <= b for a, b in fn["spans"])
    return fn["line"] <= line <= fn["end"]


def enclosing(idx_nodes: dict, file: str, line: int) -> str:
    """The innermost function of file whose span holds line ('' when none)."""
    best = ""
    for nid, fn in idx_nodes.items():
        if fn["file"] == file and in_span(fn, line):
            if not best or (fn["end"] - fn["line"]) < (idx_nodes[best]["end"] - idx_nodes[best]["line"]):
                best = nid
    return best


def flat(text: str) -> str:
    return " ".join((text or "").split())


def find_line(lines: list[str], quote: str, near: int, lo: int = 1, hi: int = 0) -> int:
    """The line (1-based) of lines[lo..hi] holding quote, whitespace runs compared as one
    space, nearest to near; a quote spanning lines matches where it starts. 0 when absent."""
    q = flat(quote)
    hi = hi or len(lines)
    if not q:
        return 0
    hits = [i for i in range(max(lo, 1), min(hi, len(lines)) + 1) if q in flat(lines[i - 1])]
    if not hits and "\n" in quote.strip():
        joined, starts, pos = [], [], 0
        for i in range(max(lo, 1), min(hi, len(lines)) + 1):
            piece = flat(lines[i - 1])
            if not piece:
                continue
            starts.append((pos, i))
            joined.append(piece)
            pos += len(piece) + 1
        text = " ".join(joined)
        at = text.find(q)
        while at >= 0:
            hits.append(max(i for p, i in starts if p <= at))
            at = text.find(q, at + 1)
    if not hits:
        return 0
    return min(hits, key=lambda i: (abs(i - near), i))


__all__ = ["MODULE", "enclosing", "find_line", "in_span", "index", "is_test", "nodes"]
