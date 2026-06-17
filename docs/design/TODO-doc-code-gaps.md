# 设计文档与代码不对齐的待修项

审计日期: 2026-06-18（第二次审计）

本文件记录设计文档与实际代码的偏差，按优先级排列。修复后从此处删除对应条目。

---

## ~~路径错误~~（已修复）

### ~~extension-gating/implementation.md~~
- ~~文档写的路径: `openprogram/agents/gating.py`, `openprogram/agents/manager.py`~~
- ~~实际路径: `openprogram/agent/management/gating.py`, `openprogram/agent/management/manager.py`~~
- 状态: ✅ 已修正，文档路径现已正确

---

## 需要更新的文档（HIGH）

### providers/thinking-effort.md
1. **§10 待修项中 Opus 4.7 override 条目过时**: 文档把 `["low","medium","high"]` 限制当作 bug，
   但这是 Anthropic（Claude 4.6 guidance）的 deliberate design choice。应删除该待修条目或改为说明设计原因。
2. **"max" 级别映射标记错误**: 文档声称 5 个 provider 的 max 映射"未映射"，但实际代码中
   `anthropic.py` 已有 `xhigh → max` 映射，所有 provider 都支持 max level。应更新映射表。
3. **Fable 5**: 文档提到缺少 Fable 5 说明，但 `thinking_catalog.py` 中也没有 Fable 5 条目。
   需要确认：是代码缺还是文档多写了？如果该模型已经存在于 models.dev 目录但 catalog 未收录，需加上。

---

## 状态标记过期（MEDIUM）

### memory/memory-v2.md
- Phase 0-1: 已完成（文档标记正确）
- Phase 2: 文档标 "❌ 未开始"，但 §0.5 又提到 "前置读层已落地"（Provenance dataclass）
- 应明确：Phase 2 分拆为子步骤，标注哪些 substep 已 partial、哪些待做

### ~~context/contextgit.md~~
- ~~文档标记: "Status: proposal, not implemented"~~
- 状态: ✅ 文档已标 "partially implemented"，与代码一致（DAG 底座在 `contextgit/dag.py`，上层未建）

---

## 实施滞后（设计有效但代码未完全跟上）

### context/cross-turn-tool-context.md
- "tool aging + 1 行语义 stub" 策略文档描述完整
- `openprogram/context/tool_aging/` 存在但实现与文档有偏差
- 待实施完成后同步文档

### providers/model-catalog-final.md
- models.dev 自动更新 TTL + fetched 覆盖式保存的完整流水线未完全落地
- 模型列表拉取逻辑存在但自动刷新机制未实现

---

## 内容缺失

### runtime/ 缺 process_runner 设计文档
- `agent/process_runner.py` 是重要的子进程执行模块（spawn、stop、user-input bridge）
- 没有对应的设计文档

### runtime/ 缺 dispatcher 设计文档
- `agent/dispatcher/__init__.py` 是 530 行的核心模块
- 没有独立的设计文档（dispatcher-split.md 只讨论拆分，不是完整设计）

---

## 本次审计确认已正确的文档

以下文档经审计与代码完全一致，无需修改：

- `runtime/controllability-and-three-surface-sync.md` — attended/unattended、graceful stop、三端同步均已实现
- `runtime/user-input-requests.md` — Phase 1+2 已落地（QuestionRegistry、三种 Transport 含新增的 TTYTransport）
- `function/function-calling-unification.md` — 已使用 "profiles" 术语，与代码一致
- `extension-gating/implementation.md` — 路径已正确
- `context/contextgit.md` — 状态标记正确

---

## 与代码修改相关的上下文（2026-06-18 本轮修改）

本轮代码变更影响以下设计文档的覆盖范围：
- CLI 现已支持 attended mode + TTYTransport（`user-input-requests.md` Phase 3 的 CLI 部分可视为已解决）
- graceful stop 覆盖新增：session.py wait_for_answer、runtime.py retry loop、research_harness literature orchestrator
- Functions 页 "folders" → "profiles" CSS/文件名重命名完成
- `_resolve_folder_toolset` 现读 `meta.get("profiles", meta.get("folders", {}))` 兼容双键
