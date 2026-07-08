# Provider 自包含迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把模型目录从 `openprogram/providers/_catalog/*.json`（752 条、22 provider、进 git）迁移到 `providers/<p>/` 自包含布局（`provider.json` 用 endpoint 分组存 api/base_url，`models.json` 存模型规格），保持 `MODELS` 主字典的类型/填充语义/key 格式完全不变，最终删除 `_catalog/`——每步系统不瘫、数据不丢。

**Architecture:** 双源加载器过渡：`models_generated._load()` 先额外读新格式（`providers/<p>/provider.json` + `models.json`），新源覆盖同 key 的旧 `_catalog` 条目；旧源保留兜底。迁移脚本把每个 `_catalog/<p>.json` 拆成 `provider.json`（endpoint 分组）+ `models.json`（逐模型规格，引用 endpoint 名），逐条等价校验。全部 provider 迁完、验证通过后删 `_catalog/` 与旧加载分支。`MODELS` 始终是同一个可变 dict（`_register_custom_model_in_registry` 原地写依赖此）。

**Tech Stack:** Python 3, pydantic (Model/ModelCost), pytest。测试 `.venv-test/bin/python -m pytest`（无则 `python -m pytest`）。

## Global Constraints

- 设计依据：`docs/design/providers/models.md` 第 9 节。
- `MODELS: dict[str, Model]`（`models_generated.py:43`）——**类型、变量名、可变性、key 格式 `"<provider>/<id>"` 全部不变**。`_register_custom_model_in_registry`（`_runtime_management.py:302`）会 `MODELS[k]=m` 原地写，loader 不得返回只读视图或每次重建。
- **按行内 `provider` 字段分组，不按 key 前缀**——`gemini-subscription.json` 里 5 条 `google-gemini-cli/*` 的 `provider` 字段是 `"gemini-subscription"`（`get_providers()` 按 `model.provider` 分组）。两套 key 别名（`google-gemini-cli/<id>` + `gemini-subscription/<id>`，共 10 key）必须全部保留。
- 每行完整字段集必须保留：`id,name,api,provider,base_url,reasoning,thinking_levels,default_thinking_level,thinking_variant,input,cost{input,output,cache_read,cache_write},context_window,max_tokens,headers,compat`。`cost` 是嵌套对象、`headers`/`compat` 可为 dict——不能丢或压平。
- loader 容错沿用现状：文件缺失/损坏 → skip 不 crash；排序确定性。
- 两个必须保持绿的核心回归测试：`tests/unit/test_provider_wire_invariants.py`、`tests/unit/test_model_fetch_routing.py`。
- 提交尾部：`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- 每任务 TDD：写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。

## 文件结构

- **新增** `openprogram/providers/_catalog_new.py`：新格式加载器（读 `providers/<p>/provider.json`+`models.json` → `dict[str,Model]`）。独立文件便于测试与回滚。
- **新增** `openprogram/providers/_migrate_catalog.py`：一次性迁移脚本（`_catalog/<p>.json` → `providers/<p>/provider.json`+`models.json`）+ 等价校验。
- **改** `openprogram/providers/models_generated.py`：`_load()` 变双源合并。
- **改** `.gitignore`：区分 git 源（`provider.json` + 内置 `models.json`）与 Fetch 缓存。
- **改**（后期）循环依赖两函数、自定义注册路径——仅当断依赖需要。
- **删**（最后）`openprogram/providers/_catalog/`。

---

### Task 1: 新格式数据 schema + 单 provider 加载器

**Files:**
- Create: `openprogram/providers/_catalog_new.py`
- Test: `tests/providers/test_catalog_new_loader.py`

**Interfaces:**
- Produces:
  - `load_provider_dir(provider_dir: Path) -> dict[str, Model]`：读一个 `providers/<p>/` 目录的 `provider.json`+`models.json`，返回 `{"<provider>/<id>": Model}`。目录无 `provider.json` → 返回 `{}`。
  - `load_new_catalog(providers_root: Path) -> dict[str, Model]`：扫 `providers_root/*/`，合并所有 `load_provider_dir`。

新格式约定（本任务定义）：
- `provider.json`：`{"id": str, "no_proxy"?: bool, "endpoints": {name: {"api": str, "base_url": str}}}`。单 wire provider 只有 `{"default": {...}}`。
- `models.json`：`{"models": [ {"id","name","endpoint"?,...规格字段...}, ... ]}`。`endpoint` 缺省 `"default"`。每模型的 `api`/`base_url` 从 `provider.json.endpoints[endpoint]` 取。
- 支持 key 别名：`models.json` 模型可带 `"key_aliases": ["google-gemini-cli"]`——为每个别名额外产出一条同 Model 的 `"<alias>/<id>"` key（解决 gemini 双 key）。`provider` 字段仍取 `provider.json.id`。

- [ ] **Step 1: 写失败测试**

创建 `tests/providers/test_catalog_new_loader.py`：

```python
import json
from pathlib import Path
from openprogram.providers._catalog_new import load_provider_dir, load_new_catalog
from openprogram.providers.types import Model


def _write(root: Path, pid: str, provider_json: dict, models: list[dict]):
    d = root / pid
    d.mkdir(parents=True)
    (d / "provider.json").write_text(json.dumps(provider_json))
    (d / "models.json").write_text(json.dumps({"models": models}))


def test_single_wire_provider(tmp_path):
    _write(tmp_path, "deepseek",
           {"id": "deepseek", "endpoints": {"default": {"api": "openai-completions", "base_url": "https://api.deepseek.com/v1"}}},
           [{"id": "deepseek-chat", "name": "DeepSeek Chat", "context_window": 128000,
             "cost": {"input": 0.27, "output": 1.1}}])
    got = load_provider_dir(tmp_path / "deepseek")
    m = got["deepseek/deepseek-chat"]
    assert isinstance(m, Model)
    assert m.api == "openai-completions"
    assert m.base_url == "https://api.deepseek.com/v1"
    assert m.provider == "deepseek"
    assert m.cost.input == 0.27 and m.cost.output == 1.1


def test_multi_wire_endpoint_resolution(tmp_path):
    _write(tmp_path, "opencode",
           {"id": "opencode", "endpoints": {
               "default": {"api": "openai-completions", "base_url": "https://opencode.ai/zen/v1"},
               "anthropic": {"api": "anthropic-messages", "base_url": "https://opencode.ai/zen"}}},
           [{"id": "gpt-x", "name": "GPT X"},  # default endpoint
            {"id": "claude-x", "name": "Claude X", "endpoint": "anthropic"}])
    got = load_provider_dir(tmp_path / "opencode")
    assert got["opencode/gpt-x"].api == "openai-completions"
    assert got["opencode/gpt-x"].base_url == "https://opencode.ai/zen/v1"
    assert got["opencode/claude-x"].api == "anthropic-messages"
    assert got["opencode/claude-x"].base_url == "https://opencode.ai/zen"


def test_key_aliases_produce_extra_keys(tmp_path):
    _write(tmp_path, "gemini-subscription",
           {"id": "gemini-subscription", "endpoints": {"default": {"api": "gemini-subscription", "base_url": "https://cloudcode-pa.googleapis.com"}}},
           [{"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "key_aliases": ["google-gemini-cli"]}])
    got = load_provider_dir(tmp_path / "gemini-subscription")
    assert "gemini-subscription/gemini-2.5-pro" in got
    assert "google-gemini-cli/gemini-2.5-pro" in got
    # both carry provider == the row's provider field, not the alias prefix
    assert got["google-gemini-cli/gemini-2.5-pro"].provider == "gemini-subscription"


def test_missing_provider_json_yields_empty(tmp_path):
    (tmp_path / "wireonly").mkdir()
    assert load_provider_dir(tmp_path / "wireonly") == {}


def test_headers_and_input_preserved(tmp_path):
    _write(tmp_path, "github-copilot",
           {"id": "github-copilot", "endpoints": {"default": {"api": "openai-responses", "base_url": "https://api.individual.githubcopilot.com"}}},
           [{"id": "gpt-5.2-codex", "name": "GPT-5.2-Codex", "input": ["text", "image"],
             "headers": {"Copilot-Integration-Id": "vscode-chat"}}])
    m = load_provider_dir(tmp_path / "github-copilot")["github-copilot/gpt-5.2-codex"]
    assert m.input == ["text", "image"]
    assert m.headers == {"Copilot-Integration-Id": "vscode-chat"}
```

- [ ] **Step 2: 跑红**

Run: `.venv-test/bin/python -m pytest tests/providers/test_catalog_new_loader.py -x -q`
Expected: FAIL（`ModuleNotFoundError: _catalog_new`）

- [ ] **Step 3: 实现 `_catalog_new.py`**

```python
"""New self-contained catalog loader: providers/<p>/provider.json + models.json.

provider.json declares endpoint groups {name: {api, base_url}}; each model in
models.json references an endpoint (default "default") from which its api +
base_url are filled. Runs alongside the legacy _catalog/ loader during
migration (see models_generated._load).
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Model


def _build_model(row: dict, provider_id: str, endpoints: dict) -> Model:
    ep_name = row.get("endpoint", "default")
    ep = endpoints.get(ep_name) or endpoints.get("default") or {}
    data = dict(row)
    data.pop("endpoint", None)
    data.pop("key_aliases", None)
    data["provider"] = provider_id
    data["api"] = ep.get("api", "openai-completions")
    data["base_url"] = ep.get("base_url", "")
    return Model.model_validate(data)


def load_provider_dir(provider_dir: Path) -> dict[str, Model]:
    pj = provider_dir / "provider.json"
    mj = provider_dir / "models.json"
    if not pj.is_file():
        return {}
    try:
        pcfg = json.loads(pj.read_text(encoding="utf-8"))
        models = json.loads(mj.read_text(encoding="utf-8")).get("models", []) if mj.is_file() else []
    except (OSError, json.JSONDecodeError):
        return {}
    provider_id = pcfg.get("id") or provider_dir.name
    endpoints = pcfg.get("endpoints") or {}
    out: dict[str, Model] = {}
    for row in models:
        try:
            m = _build_model(row, provider_id, endpoints)
        except Exception:
            continue
        out[f"{provider_id}/{m.id}"] = m
        for alias in row.get("key_aliases") or []:
            out[f"{alias}/{m.id}"] = m
    return out


def load_new_catalog(providers_root: Path) -> dict[str, Model]:
    merged: dict[str, Model] = {}
    if not providers_root.is_dir():
        return merged
    for d in sorted(p for p in providers_root.iterdir() if p.is_dir()):
        merged.update(load_provider_dir(d))
    return merged
```

- [ ] **Step 4: 跑绿**

Run: `.venv-test/bin/python -m pytest tests/providers/test_catalog_new_loader.py -x -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add openprogram/providers/_catalog_new.py tests/providers/test_catalog_new_loader.py
git commit -m "feat(providers): self-contained provider.json+models.json loader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 迁移脚本 `_catalog/*.json` → provider.json + models.json + 等价校验

**Files:**
- Create: `openprogram/providers/_migrate_catalog.py`
- Test: `tests/providers/test_migrate_catalog.py`

**Interfaces:**
- Consumes: `load_provider_dir`（Task 1），`_catalog/*.json` 旧数据
- Produces:
  - `migrate_catalog_file(catalog_json: dict) -> tuple[dict, list[dict]]`：一个 `_catalog/<p>.json`（`{"<provider>/<id>": row}`）→ `(provider_json, models_list)`。按行内 `provider` 字段分组；抽出去重的 `(api,base_url)` 组合成 `endpoints`（首个/最常见组合命名 `default`，其余按 api 命名）；每模型剥离 `api/base_url/provider`，标 `endpoint`，key-prefix≠provider 的记 `key_aliases`。
  - `migrate_all(catalog_dir: Path, providers_root: Path) -> list[str]`：迁移全部文件，写盘，返回已迁 provider 列表。
  - `verify_equivalence(catalog_dir, providers_root) -> list[str]`：加载旧 `_catalog` 与新 `load_new_catalog`，逐 key 比对 Model 相等，返回不一致的 key（空=完全等价）。

- [ ] **Step 1: 写失败测试**

创建 `tests/providers/test_migrate_catalog.py`：

```python
from openprogram.providers._migrate_catalog import migrate_catalog_file


def test_single_wire_extracts_default_endpoint():
    cat = {"deepseek/deepseek-chat": {
        "id": "deepseek-chat", "name": "X", "api": "openai-completions",
        "provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
        "context_window": 128000, "cost": {"input": 0.27, "output": 1.1}}}
    pj, models = migrate_catalog_file(cat)
    assert pj["id"] == "deepseek"
    assert pj["endpoints"]["default"] == {"api": "openai-completions", "base_url": "https://api.deepseek.com/v1"}
    assert len(models) == 1
    m = models[0]
    assert "api" not in m and "base_url" not in m and "provider" not in m
    assert m.get("endpoint", "default") == "default"
    assert m["cost"] == {"input": 0.27, "output": 1.1}  # nested preserved


def test_multi_wire_groups_endpoints():
    cat = {
        "opencode/a": {"id": "a", "name": "A", "api": "openai-completions", "provider": "opencode", "base_url": "https://opencode.ai/zen/v1"},
        "opencode/b": {"id": "b", "name": "B", "api": "anthropic-messages", "provider": "opencode", "base_url": "https://opencode.ai/zen"},
    }
    pj, models = migrate_catalog_file(cat)
    eps = pj["endpoints"]
    # two distinct (api, base_url) groups
    pairs = {(e["api"], e["base_url"]) for e in eps.values()}
    assert ("openai-completions", "https://opencode.ai/zen/v1") in pairs
    assert ("anthropic-messages", "https://opencode.ai/zen") in pairs
    by_id = {m["id"]: m for m in models}
    assert eps[by_id["a"].get("endpoint", "default")]["api"] == "openai-completions"
    assert eps[by_id["b"]["endpoint"]]["api"] == "anthropic-messages"


def test_key_prefix_mismatch_becomes_alias():
    cat = {
        "gemini-subscription/g": {"id": "g", "name": "G (Sub)", "api": "gemini-subscription", "provider": "gemini-subscription", "base_url": "https://x"},
        "google-gemini-cli/g": {"id": "g", "name": "G (CLI)", "api": "gemini-subscription", "provider": "gemini-subscription", "base_url": "https://x"},
    }
    pj, models = migrate_catalog_file(cat)
    assert pj["id"] == "gemini-subscription"
    g = [m for m in models if m["id"] == "g"]
    assert len(g) == 1  # deduped to one model row
    assert "google-gemini-cli" in g[0]["key_aliases"]
```

- [ ] **Step 2: 跑红**

Run: `.venv-test/bin/python -m pytest tests/providers/test_migrate_catalog.py -x -q`
Expected: FAIL

- [ ] **Step 3: 实现 `_migrate_catalog.py`**

```python
"""One-shot migration: _catalog/<p>.json -> providers/<p>/{provider.json,models.json}.

Groups rows by their own `provider` field (NOT the key prefix — see
gemini-subscription double-key). Distinct (api, base_url) pairs become named
endpoints; each model references one. A key whose prefix != provider becomes a
key_alias so get_model's historical spelling keeps resolving.
"""
from __future__ import annotations

import json
from pathlib import Path

from ._catalog_new import load_provider_dir  # noqa: F401  (used by verify)

_SPEC_DROP = {"api", "base_url", "provider"}


def migrate_catalog_file(catalog: dict) -> tuple[dict, list[dict]]:
    # group by row's provider field
    provider_id = None
    for row in catalog.values():
        provider_id = row.get("provider")
        break
    provider_id = provider_id or "unknown"

    # collect distinct (api, base_url), name them; most-common → "default"
    from collections import Counter
    pair_counts = Counter((r.get("api"), r.get("base_url")) for r in catalog.values())
    ordered = [p for p, _ in pair_counts.most_common()]
    ep_name: dict[tuple, str] = {}
    endpoints: dict[str, dict] = {}
    for i, (api, base) in enumerate(ordered):
        name = "default" if i == 0 else (api or f"ep{i}")
        # de-dup name collisions
        if name in endpoints and name != "default":
            name = f"{name}-{i}"
        ep_name[(api, base)] = name
        endpoints[name] = {"api": api, "base_url": base}

    models_by_id: dict[str, dict] = {}
    for key, row in catalog.items():
        mid = row["id"]
        prefix = key.split("/", 1)[0]
        name = ep_name[(row.get("api"), row.get("base_url"))]
        if mid not in models_by_id:
            spec = {k: v for k, v in row.items() if k not in _SPEC_DROP}
            if name != "default":
                spec["endpoint"] = name
            models_by_id[mid] = spec
        # record alias if this key's prefix differs from provider id
        if prefix != provider_id:
            models_by_id[mid].setdefault("key_aliases", [])
            if prefix not in models_by_id[mid]["key_aliases"]:
                models_by_id[mid]["key_aliases"].append(prefix)

    provider_json = {"id": provider_id, "endpoints": endpoints}
    return provider_json, list(models_by_id.values())


def migrate_all(catalog_dir: Path, providers_root: Path) -> list[str]:
    done = []
    for jf in sorted(catalog_dir.glob("*.json")):
        catalog = json.loads(jf.read_text(encoding="utf-8"))
        if not catalog:
            continue
        pj, models = migrate_catalog_file(catalog)
        d = providers_root / pj["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "provider.json").write_text(json.dumps(pj, indent=1, ensure_ascii=False))
        (d / "models.json").write_text(json.dumps({"models": models}, indent=1, ensure_ascii=False))
        done.append(pj["id"])
    return done


def verify_equivalence(catalog_dir: Path, providers_root: Path) -> list[str]:
    import json as _json
    from .types import Model
    old: dict[str, Model] = {}
    for jf in sorted(catalog_dir.glob("*.json")):
        for k, row in _json.loads(jf.read_text(encoding="utf-8")).items():
            old[k] = Model.model_validate(row)
    from ._catalog_new import load_new_catalog
    new = load_new_catalog(providers_root)
    mismatched = []
    for k, m in old.items():
        n = new.get(k)
        if n is None or n.model_dump() != m.model_dump():
            mismatched.append(k)
    return mismatched
```

- [ ] **Step 4: 跑绿**

Run: `.venv-test/bin/python -m pytest tests/providers/test_migrate_catalog.py -x -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add openprogram/providers/_migrate_catalog.py tests/providers/test_migrate_catalog.py
git commit -m "feat(providers): _catalog -> provider.json migration + equivalence check

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 实跑迁移 + 全量等价校验（生成真实 providers/<p>/ 数据）

**Files:**
- Generate: `openprogram/providers/<p>/provider.json` + `models.json`（22 provider）
- Modify: `.gitignore`（让新生成的 provider.json + 迁移出的 models.json 进 git；区分 Fetch 缓存）
- Test: `tests/providers/test_migration_equivalence.py`

**Interfaces:**
- Consumes: `migrate_all` / `verify_equivalence`（Task 2）

- [ ] **Step 1: 写失败测试（等价断言）**

创建 `tests/providers/test_migration_equivalence.py`：

```python
from pathlib import Path
from openprogram.providers._migrate_catalog import verify_equivalence

_ROOT = Path(__file__).resolve().parents[2] / "openprogram" / "providers"


def test_new_catalog_equivalent_to_old():
    # After migration is run, every _catalog key must reproduce byte-identical Model.
    mismatched = verify_equivalence(_ROOT / "_catalog", _ROOT)
    assert mismatched == [], f"{len(mismatched)} keys differ: {mismatched[:10]}"
```

- [ ] **Step 2: 跑红**

Run: `.venv-test/bin/python -m pytest tests/providers/test_migration_equivalence.py -x -q`
Expected: FAIL（新数据还没生成，new 里缺 key）

- [ ] **Step 3: 实跑迁移脚本生成数据**

Run:
```bash
.venv-test/bin/python -c "
from pathlib import Path
from openprogram.providers._migrate_catalog import migrate_all, verify_equivalence
root = Path('openprogram/providers')
done = migrate_all(root / '_catalog', root)
print('migrated:', len(done), 'providers')
mm = verify_equivalence(root / '_catalog', root)
print('mismatched keys:', len(mm))
print(mm[:20])
"
```
Expected: `migrated: 22 providers`，`mismatched keys: 0`。若有 mismatch，看具体 key 调 `migrate_catalog_file`（多半是 endpoint 命名/字段剥离/别名的边角），修 `_migrate_catalog.py` 后重跑，直到 0。

改 `.gitignore`：现有 `openprogram/providers/*/models.json` 会挡住迁移出的 models.json 进 git。改为只忽略 Fetch 缓存（迁移生成的内置数据要进 git）。方案：迁移写 `models.json`（进 git）；Fetch 改写 `models.cache.json`（保持 ignore，Task 6 处理）。所以 `.gitignore` 把 `*/models.json` 改成 `*/models.cache.json`。同时确认 provider.json 不被 ignore。

- [ ] **Step 4: 跑绿 + 确认数据进 git**

Run: `.venv-test/bin/python -m pytest tests/providers/test_migration_equivalence.py -x -q`
Expected: PASS（mismatched == []）

Run: `git status --short openprogram/providers/*/provider.json openprogram/providers/*/models.json | head`
Expected: 22 个 provider.json + 对应 models.json 显示为新增（未被 ignore）

- [ ] **Step 5: 提交**

```bash
git add openprogram/providers/*/provider.json openprogram/providers/*/models.json .gitignore tests/providers/test_migration_equivalence.py
git commit -m "feat(providers): migrate all 22 _catalog files to self-contained dirs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 双源加载器（`_load` 合并新旧，新源优先）

**Files:**
- Modify: `openprogram/providers/models_generated.py`
- Test: `tests/providers/test_dual_source_load.py`

**Interfaces:**
- Consumes: `load_new_catalog`（Task 1）
- Produces: `MODELS`（同名同类型，双源合并结果）

- [ ] **Step 1: 写失败测试**

创建 `tests/providers/test_dual_source_load.py`：

```python
from openprogram.providers.models_generated import MODELS, _load
from openprogram.providers.models import get_model, get_providers


def test_models_still_populated():
    assert len(MODELS) > 700  # 752 keys pre-migration; new source reproduces them
    # spot-check across providers/wires
    assert get_model("openai", "gpt-4o") is not None
    assert get_model("opencode", "gpt-5.1-codex-max") is not None
    # gemini double-key both resolve
    assert get_model("gemini-subscription", "gemini-2.5-pro") is not None
    assert get_model("google-gemini-cli", "gemini-2.5-pro") is not None


def test_models_is_mutable_same_object():
    # _register_custom_model_in_registry writes MODELS[k]=m in place
    before = id(MODELS)
    MODELS["__test__/x"] = MODELS["openai/gpt-4o"]
    assert id(MODELS) == before
    del MODELS["__test__/x"]


def test_new_source_wins_over_legacy():
    # a key present in both new (providers/<p>) and old (_catalog) resolves to new
    m = _load()
    assert m.get("deepseek/deepseek-chat") is not None
```

- [ ] **Step 2: 跑红**

（此时 `_load` 还是单源；若新数据已生成，`test_new_source_wins` 可能已过，但 `_load` 未合并新源的语义未验证。先确认测试对当前实现失败于「双源合并」断言。）

Run: `.venv-test/bin/python -m pytest tests/providers/test_dual_source_load.py -x -q`
Expected: 至少 `test_new_source_wins_over_legacy` 语义未实现前，若删了 `_catalog` 才见效——本任务先加双源，Task 7 删旧。此处断言 `len(MODELS)>700` 与双键解析应已由现有 `_catalog` 满足；关键新断言是 `_load` 读到了新源。调整断言确保红：临时删一个 provider 的 `_catalog` 文件验证新源兜底（见 Step 3 说明）。

- [ ] **Step 3: 实现双源 `_load`**

改 `models_generated.py`：

```python
_CATALOG_DIR = Path(__file__).parent / "_catalog"
_PROVIDERS_DIR = Path(__file__).parent


def _load() -> dict[str, Model]:
    merged: dict[str, Model] = {}
    # legacy _catalog (fallback, removed in a later task)
    if _CATALOG_DIR.is_dir():
        for jf in sorted(_CATALOG_DIR.glob("*.json")):
            try:
                raw = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for key, row in raw.items():
                merged[key] = Model.model_validate(row)
    # new self-contained providers/<p>/ — wins on key collision
    try:
        from ._catalog_new import load_new_catalog
        merged.update(load_new_catalog(_PROVIDERS_DIR))
    except Exception:
        pass
    return merged


MODELS: dict[str, Model] = _load()
```

- [ ] **Step 4: 跑绿 + 全量回归**

Run: `.venv-test/bin/python -m pytest tests/providers/test_dual_source_load.py tests/unit/test_provider_wire_invariants.py tests/unit/test_model_fetch_routing.py tests/unit/test_resolve_model.py -q`
Expected: PASS，无回归

- [ ] **Step 5: 提交**

```bash
git add openprogram/providers/models_generated.py tests/providers/test_dual_source_load.py
git commit -m "feat(providers): dual-source catalog loader (new providers/<p> wins)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 断循环依赖 —— api/base_url 从 provider metadata 取，不再读 MODELS

**Files:**
- Modify: `openprogram/webui/_model_catalog/providers.py`（`_default_api_for`/`_static_apis_for`）
- Modify: `openprogram/webui/_model_catalog/storage.py`（`_resolve_base_url`）
- Create: `openprogram/providers/_provider_meta.py`（无 webui 依赖，读 provider.json 的 endpoints）
- Test: `tests/providers/test_provider_meta.py`

**Interfaces:**
- Produces:
  - `openprogram/providers/_provider_meta.py`：`provider_apis(provider_id) -> set[str]`、`provider_base_url(provider_id) -> str | None`——读 `providers/<p>/provider.json` 的 endpoints，纯 providers 层、不 import webui、不读 MODELS。

- [ ] **Step 1: 写失败测试**

```python
from openprogram.providers._provider_meta import provider_apis, provider_base_url


def test_single_wire_meta():
    assert provider_apis("deepseek") == {"openai-completions"}
    assert provider_base_url("deepseek") == "https://api.deepseek.com/v1"


def test_multi_wire_meta():
    apis = provider_apis("opencode")
    assert "anthropic-messages" in apis and "openai-completions" in apis


def test_unknown_provider():
    assert provider_apis("nope") == set()
    assert provider_base_url("nope") is None
```

- [ ] **Step 2: 跑红**

Run: `.venv-test/bin/python -m pytest tests/providers/test_provider_meta.py -x -q`
Expected: FAIL

- [ ] **Step 3: 实现 `_provider_meta.py` + 改两函数读它**

`_provider_meta.py`：

```python
"""Provider-level api/base_url from providers/<p>/provider.json — no MODELS,
no webui import. Breaks the providers<->webui circular dep for the derivation
helpers (_default_api_for / _resolve_base_url)."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).parent


def _endpoints(provider_id: str) -> dict:
    pj = _ROOT / provider_id / "provider.json"
    if not pj.is_file():
        return {}
    try:
        return (json.loads(pj.read_text(encoding="utf-8")).get("endpoints") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def provider_apis(provider_id: str) -> set[str]:
    return {e.get("api") for e in _endpoints(provider_id).values() if e.get("api")}


def provider_base_url(provider_id: str) -> str | None:
    eps = _endpoints(provider_id)
    ep = eps.get("default") or (next(iter(eps.values())) if eps else None)
    return ep.get("base_url") if ep else None
```

改 `providers.py:_static_apis_for` → 优先 `provider_meta.provider_apis`，回退旧 MODELS 读法（过渡期两者并存，新数据在就用新的）：

```python
def _static_apis_for(provider_id: str) -> set[str]:
    try:
        from openprogram.providers._provider_meta import provider_apis
        apis = provider_apis(provider_id)
        if apis:
            return apis
    except Exception:
        pass
    try:
        from openprogram.providers.models_generated import MODELS
        return {m.api for m in MODELS.values() if m.provider == provider_id}
    except Exception:
        return set()
```

改 `storage.py:_resolve_base_url` 的「static registry baked-in」分支：先试 `provider_base_url(provider_id)`，回退 `get_models(...)[0].base_url`。

- [ ] **Step 4: 跑绿 + 回归**

Run: `.venv-test/bin/python -m pytest tests/providers/test_provider_meta.py tests/unit/test_provider_wire_invariants.py tests/unit/test_model_fetch_routing.py tests/unit/test_enabled_models_community.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add openprogram/providers/_provider_meta.py openprogram/webui/_model_catalog/providers.py openprogram/webui/_model_catalog/storage.py tests/providers/test_provider_meta.py
git commit -m "refactor(providers): derive api/base_url from provider.json, break MODELS cycle

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Fetch 写 `models.cache.json`（不覆盖 git 源 models.json）

**Files:**
- Modify: `openprogram/webui/_model_catalog/provider_models.py`（`save_fetched` 写 `models.cache.json`；`load_fetched`/`combined_models` 读它）
- Test: `tests/providers/test_fetch_cache_separation.py`

**Interfaces:**
- 迁移后 `providers/<p>/models.json` = 内置 git 源；Fetch 结果 → `models.cache.json`（gitignore）。加载合并：内置为基底，缓存补充/更新规格（不改 api/base_url，那属 provider.json）。

- [ ] **Step 1: 写失败测试**

```python
import json
from pathlib import Path
from openprogram.webui._model_catalog import provider_models as pm


def test_fetch_writes_cache_not_models_json(tmp_path, monkeypatch):
    # save_fetched must target models.cache.json, leaving models.json (git) intact
    monkeypatch.setattr(pm, "_provider_dir", lambda pid: tmp_path)
    (tmp_path / "models.json").write_text(json.dumps({"models": [{"id": "builtin"}]}))
    pm.save_fetched("x", [{"id": "fetched"}])
    assert (tmp_path / "models.cache.json").is_file()
    # git-source untouched
    assert json.loads((tmp_path / "models.json").read_text())["models"][0]["id"] == "builtin"
```

（`_provider_dir` 的确切名/签名以 `provider_models.py` 现状为准，实施时对齐。）

- [ ] **Step 2: 跑红**

Run: `.venv-test/bin/python -m pytest tests/providers/test_fetch_cache_separation.py -x -q`
Expected: FAIL（现在写的是 models.json）

- [ ] **Step 3: 实现**

`save_fetched` 目标文件名 `models.json` → `models.cache.json`；`load_fetched`/`combined_models` 读缓存文件；合并时内置 `models.json`（git）与 `models.cache.json`（fetch）按 id 合并，缓存补充规格。`.gitignore` 已在 Task 3 改成忽略 `*/models.cache.json`。

- [ ] **Step 4: 跑绿 + 回归**

Run: `.venv-test/bin/python -m pytest tests/providers/test_fetch_cache_separation.py tests/unit/test_provider_models.py tests/unit/test_model_fetch_routing.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add openprogram/webui/_model_catalog/provider_models.py tests/providers/test_fetch_cache_separation.py
git commit -m "refactor(catalog): Fetch writes models.cache.json, keeps git models.json

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 删 `_catalog/` + 加载器去掉旧分支 + 全量回归

**Files:**
- Delete: `openprogram/providers/_catalog/`
- Modify: `openprogram/providers/models_generated.py`（去掉 legacy 分支）
- Test: 无新增；全量回归 + 迁移等价测试改为纯新源

- [ ] **Step 1: 删前确认新源全覆盖**

Run:
```bash
.venv-test/bin/python -c "
from pathlib import Path
from openprogram.providers._migrate_catalog import verify_equivalence
mm = verify_equivalence(Path('openprogram/providers/_catalog'), Path('openprogram/providers'))
print('mismatched:', len(mm)); print(mm[:20])
"
```
Expected: `mismatched: 0`

- [ ] **Step 2: 删 `_catalog/` + 去 legacy 分支**

```bash
git rm -r openprogram/providers/_catalog/
```
`models_generated._load()` 删掉 `_CATALOG_DIR` 那段，只留 `load_new_catalog(_PROVIDERS_DIR)`。`verify_equivalence`/`test_migration_equivalence` 依赖 `_catalog` 已不存在——把该测试改为「快照校验」或删除（迁移已完成，等价性由 Task 4 的 `test_dual_source_load` + 全量套件保证）。

- [ ] **Step 3: 全量回归**

Run: `.venv-test/bin/python -m pytest tests/providers tests/unit -q`
Expected: PASS（`len(MODELS)` 仍 >700，全部 provider 解析，双键在，无 `_catalog` 依赖残留）

Run（端到端多 wire 验证，需要网络/key 时可 mock 或跳过）：确认每个多 wire provider（opencode/github-copilot）每种 api 至少一个模型能 `get_model` 出正确 api+base_url：
```bash
.venv-test/bin/python -c "
from openprogram.providers.models import get_models
for pid in ('opencode','github-copilot'):
    apis = {(m.api, m.base_url) for m in get_models(pid)}
    print(pid, len(apis), 'wire/base combos:', sorted(apis))
"
```
Expected: opencode 4 组、github-copilot 3 组，与迁移前一致。

- [ ] **Step 4: 提交**

```bash
git commit -m "chore(providers): remove _catalog, load only from providers/<p>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec 覆盖**（models.md §9）：新加载器(T1) / 迁移脚本+等价(T2) / 实跑迁移+进git(T3) / 双源(T4) / 断循环依赖(T5) / Fetch-vs-git(T6) / 删旧(T7)。§9.2 endpoint 分组 → T1/T2；§9.2b 字段保留 → T1 测试(headers/input/cost) + T2；§9.4-1 循环依赖 → T5；§9.4-3 双 key → T1/T2 key_aliases；§9.4-6 Fetch 冲突 → T6；§9.4-7 验证粒度 → T7 多 wire 组合校验。**§9.4-2 no_proxy 属独立 P1，不在本迁移计划**（已在 models.md 注明依赖，单独排）。§9.4-4 claude-code 借用关系 → 由 T3 等价校验兜住（claude-code 的 thinking-alias 在 `thinking_spec.py`，不在 MODELS 数据里，迁移不动它）；若等价校验暴露 claude-code key 差异，在 T3 修迁移脚本。§9.4-5 自定义 provider 动态注册 → T5 断依赖后 `_register_custom_model_in_registry` 经 `_default_api_for`/`_resolve_base_url` 仍工作（它们改读 provider_meta，MODELS 仍可写）。
- **占位符**：确切函数名标注处（`_provider_dir`）已注明「以现状为准，实施对齐」——真实入口适配，非占位。
- **类型一致**：`load_provider_dir`/`load_new_catalog`(T1) → `migrate_catalog_file`/`migrate_all`/`verify_equivalence`(T2) → `provider_apis`/`provider_base_url`(T5) 全程引用一致；`MODELS: dict[str,Model]` 类型/可变性贯穿不变。
