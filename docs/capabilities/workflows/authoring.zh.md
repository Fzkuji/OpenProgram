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

## 可迁移的身份与位置

发布后的包相对于 OpenProgram 项目位于 `openprogram/programs/workflow/<workflow_id>`。身份是 `<workflow_id>`，不是完整文件系统位置。包内辅助模块使用相对 import，其他 Workflow 使用普通 Python import。源码、元数据、测试和说明都保存在包内，不写入用户主目录或 checkout 前缀。

运行时按明确的 Programs 范围记录项目内来源：

```json
{"scope": "programs", "path": "workflow/weekly_report", "kind": "workflow-publish", "source": "workflow:weekly_report"}
```

这个范围相对于 `openprogram/programs/` 解析，不相对于当前聊天的工作目录。移动源码 checkout 不改变相对身份。路径不允许包含 `..`、绝对路径前缀、反斜杠或指向外部的符号链接。如果多个活动目录包含相同的范围路径，系统拒绝加载，不会隐式选择其中一个。

已安装 App 和源码 checkout 是两个安装位置。App 需要一次明确的源码目录绑定；每个 Workflow 不重复保存这项安装设置。本地框架开发使用 `scripts/refresh-local-app.sh` 将默认 App 绑定到本次安装的 checkout。移动 checkout 后，从新位置运行刷新脚本；它也会重新构建并重启默认实例，不是只读验证命令。

对于之前已授权、仍使用旧绝对前缀的项目内 Workflow，只要已知目录中存在结构有效的对应包，就会迁移为相对身份。仍存在的外部位置继续保持外部来源身份。迁移不会授权同目录下其他包。撤销登记时即使目录已不存在，之后重新创建目录也不会恢复该授权。

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

## 收藏与 Use

打开 **Abilities → Programs**，选择 Workflow。收藏保存公开函数名；侧边栏根据可调用函数目录解析这个名称。**Use** 在聊天中打开 Workflow 参数表单。提交表单之前，不会执行 Workflow 或发送消息。

打开 Programs 时会刷新函数目录。如果 Use 找不到缓存中的函数，会请求一次当前可调用目录。函数不可用或请求失败时，界面显示明确错误。刷新失败会保留最近一次成功的目录，不会清空收藏项。等待加载期间离开聊天，会取消在其他聊天中打开这个表单。

如果源码目录显示在 Programs 中却无法使用，应检查包验证结果和安装授权，然后刷新 Programs。源码可见不代表 Python 入口已经成功加载。

## 当前接入边界

静态校验本身不会发布 package。OpenProgram 当前通过 `create_workflow` 发布生成项目，通过显式 `revise_workflow` 发布更新。人工 publish 命令必须先建立强制 sandbox 的行为测试门，防止未信任 Python 读取凭据、写出 candidate 目录、联网或无限运行。

Legacy `entry.py` 只用于历史 revision 和 run 的只读兼容；新 package 不要使用该格式。
