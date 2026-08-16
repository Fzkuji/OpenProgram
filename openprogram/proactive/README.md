# `openprogram/proactive/`

> Proactive layer — 事件层之上的主动性应用（迁移步 5）。

## Overview

事件底座（openprogram/events/ 包）已经把框架的全部活动统一成
一条可订阅的事件流。这个包是它的第一个真正消费者：一组**规则（Policy）**
订阅事件，在该出手时出手——挡下危险命令、提醒该补的测试、推模型去验证。

设计：docs/design/proactive/（overview / execution-model / policies-mvp /
invariants）。本包刻意只做"地基级"主动性，不含归档在 _research_archive/ 的
打扰预算 / 熔断 / 回放等研究级装修。

公开面：
* Action — 规则出手的几种动作（Gate / Notify / Inject / Prepare）
* Policy — 规则基类（on / lane / cooldown_s / evaluate）
* register_policy / registered_policies — 注册表
* install_proactive — 把引擎接到事件层（worker 启动调一次）

## Files in this directory

- **`actions.py`** — 规则出手的动作（Action）。
- **`engine.py`** — Proactive 引擎：把注册的规则接到事件层。
- **`policy.py`** — Policy 基类 + 进程级注册表。
- **`state.py`** — SessionState

## Sub-packages

- **`policies/`** — MVP 三条 policy（policies-mvp.md）。

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
