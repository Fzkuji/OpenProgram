# 设计文档与代码不对齐的待修项

审计日期: 2026-06-17

本文件记录设计文档与实际代码的偏差，按优先级排列。修复后从此处删除对应条目。

---

## 路径错误（需立即修复）

### extension-gating/implementation.md
- 文档写的路径: `openprogram/agents/gating.py`, `openprogram/agents/manager.py`
- 实际路径: `openprogram/agent/management/gating.py`, `openprogram/agent/management/manager.py`
- 单复数错误（agents→agent）+ 缺少 management/ 子目录
- 影响: 按文档找代码会找不到

---

## 状态标记过期

### context/contextgit.md
- 文档标记: "Status: proposal, not implemented"
- 实际: `openprogram/contextgit/dag.py`（219 行）已有 DAG 节点模型实现
- 应改为: "部分实现——DAG 底座已有，上层应用在建"

### memory/memory.md (v1)
- 文档描述 journal/wiki/core 三层体系
- 实际: 已被 memory-v2.md 替代（实体层 + 虚拟层）
- 应在文件头部加注: "已被 memory-v2.md 替代，仅保留作历史背景"

### memory/memory-v2.md
- Phase 0-1 标记为 TODO 但实际已完成
- Phase 2-5 未开始，文档未标注实施状态
- 应更新各 phase 的完成标记

---

## 实施滞后（设计有效但代码未完全跟上）

### context/cross-turn-tool-context.md
- "tool aging + 1 行语义 stub" 策略文档描述完整
- `openprogram/context/tool_aging/` 存在但实现与文档有偏差
- 待实施完成后同步文档

### providers/model-catalog-final.md
- models.dev 自动更新 TTL + fetched 覆盖式保存的完整流水线未完全落地
- 模型列表拉取逻辑存在但自动刷新机制未实现

### providers/thinking-effort.md §8 THINKING_OVERRIDES
- Opus 4.7 override 只有 ["low","medium","high"]
- API 实际支持 xhigh 和 max
- 应更新 override 或删除（让 derive_thinking_fields 的默认逻辑覆盖）

---

## 内容缺失

### providers/thinking-effort.md
- 缺少 Fable 5 的 thinking 行为说明（always-on, 不能 disabled）
- 缺少各模型 supports_xhigh / supports_max 的判定逻辑说明

### runtime/ 缺 process_runner 设计文档
- `agent/process_runner.py` 是重要的子进程执行模块（spawn、stop、user-input bridge）
- 没有对应的设计文档

### runtime/ 缺 dispatcher 设计文档
- `agent/dispatcher/__init__.py` 是 530 行的核心模块
- 没有独立的设计文档（dispatcher-split.md 只讨论拆分，不是完整设计）
