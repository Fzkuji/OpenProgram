# 编写 Workflow package

本页是可复用 Workflow package 的 authoring 合同，适用于人工编写和 OpenProgram author Agent 生成；两条路径使用同一个静态 validator。

## 必需目录

项目目录名、project name、入口函数名和 Python package name 必须是同一个小写 Python identifier。

```text
weekly_report/
├── pyproject.toml
├── README.md
├── __init__.py
├── workflow.py
├── steps/
│   └── prepare.py
└── tests/
    └── test_workflow.py
```

helper 也可以放在 `goals/` 或 `helpers/`。必须至少有一个不是 `__init__.py` 的 helper 模块；其他位置的 Python 源码会被拒绝。

## Metadata

```toml
[project]
name = "weekly_report"
version = "0.1.0"
description = "Prepare an evidence-based weekly report."
keywords = ["weekly report", "status update"]

[tool.openprogram]
display-name = "weekly_report"

[project.entry-points."openprogram.workflows"]
weekly_report = "workflows.weekly_report:weekly_report"
```

名称必须以小写字母开头，只能包含小写字母、数字和下划线。summary 必填，最多 500 字符；`keywords`/tags 数组必填但可以为空，最多 20 项，每项最多 60 字符。

## 公开入口

`workflow.py` 必须定义一个与项目同名的公开函数，使用现有 `@agentic_function` 装饰器，并且只接受一个位置参数 `task`。

```python
from openprogram.agentic_programming import agentic_function

from .steps.prepare import prepare


@agentic_function
def weekly_report(task: str):
    return prepare(task)
```

`__init__.py` 重新导出该函数：

```python
from .workflow import weekly_report

__all__ = ["weekly_report"]
```

## 允许的 Python 结构

package 顶层只能包含模块 docstring、允许的 `from ... import ...`、可选 `__all__` 和函数定义。禁止 class、普通 `import x`、可变模块常量、任意顶层调用，以及重新定义 `llm`、`agent`、`goal` 等托管名称。

绝对 import 只允许：

- `openprogram.agentic_programming`
- `openprogram.programs.workflow.*`
- `openprogram.programs.tools.*`
- 每次 import 一个 `workflows.<name>`，且导入函数与 package 同名

package 内允许普通相对 import。`tests/test_workflow.py` 可以 import `workflows.<project_name>`。静态目录校验只检查 import 形状；create/revise 发布时才解析每个 Workflow 依赖、拒绝缺失或循环依赖，并固定所选 Git revision。

## 静态校验

```bash
openprogram workflows validate ./weekly_report
openprogram workflows validate ./weekly_report --json
```

该命令检查目录边界、metadata、必需文件、Python 语法、顶层语句、import、装饰器、入口签名、helper 和 re-export。它是只读操作：不会初始化 Git、写文件、import package 或执行测试。

Python 生成的 `__pycache__` 目录会被忽略，因此 package 在 import 后仍可校验；其他文件仍遵守 package 路径合同，校验过程不会删除缓存文件。

成功的 JSON 包含 `ok`、`workflow_id`、规范化 metadata、校验后的 Python 文件列表和 `executed_tests: false`。非法 package 退出码为 1，并返回 `error_type` 与 `error`。

## 当前接入边界

静态校验本身不会发布 package。OpenProgram 当前通过 `create_workflow` 发布生成项目，通过显式 `revise_workflow` 发布更新。人工 publish 命令必须先建立强制 sandbox 的行为测试门，防止未信任 Python 读取凭据、写出 candidate 目录、联网或无限运行。

Legacy `entry.py` 只用于历史 revision 和 run 的只读兼容；新 package 不要使用该格式。
