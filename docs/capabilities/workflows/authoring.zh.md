# 编写、测试和发布 Workflow

Workflow 是一个 Python 包，公开一个 `@agentic_function` 入口。人工编写和 OpenProgram 的编写 Agent 使用同一包验证器与 Git 发布格式。函数通过普通 Python import 和调用进行组合。

## 编码前明确合同

在包的 `README.md` 中写明：

| 项目 | 必需信息 |
| --- | --- |
| 目的 | 具体任务以及完成任务所需的证据 |
| 输入 | `task` 的含义、必需上下文和非法输入的处理 |
| 输出 | 返回类型、产生的文件以及如何验证成功 |
| 副作用 | 模型、工具、文件、网络和外部提交行为 |
| 失败与取消 | 失败或中断后保留什么；重试是否安全 |
| 依赖 | 导入的 Workflow、所需工具与服务 |
| 测试 | 行为验证、被模拟的外部工作和独立的在线验证 |
| 使用 | 一个可复现的调用以及预期结果 |

公开入口保持简短。有独立职责时，再把准备、检查或单独阶段放入辅助模块。直接调用 `llm`、`agent`、`goal`、工具和其他 Workflow，不另建分发器或执行引擎。明确处理预期的非法输入，让运行时的取消正常传播。

## 包结构与身份

目录、项目名、入口名和 Python 包名必须是同一个小写 Python 标识符。

```text
project_report/
├── pyproject.toml
├── README.md
├── __init__.py
├── workflow.py
├── steps/
│   ├── __init__.py
│   └── prepare.py
└── tests/
    └── test_workflow.py
```

也支持 `goals/` 和 `helpers/`。至少需要一个不是 `__init__.py` 的辅助模块。包入口、辅助目录与测试之外的 Python 源码会被拒绝。字节码缓存不属于包源码，验证时会忽略。

名称以小写字母开头，只包含小写字母、数字和下划线，最多 80 个字符。摘要必填，最多 500 个字符。标签字段必填但可以为空，最多 20 项，每项最多 60 个字符。

`pyproject.toml`：

```toml
[project]
name = "project_report"
version = "0.1.0"
description = "Prepare a draft report from verified project evidence."
keywords = ["report", "evidence"]

[tool.openprogram]
display-name = "project_report"

[project.entry-points."openprogram.workflows"]
project_report = "workflows.project_report:project_report"
```

## 实现公开入口

`workflow.py` 恰好定义一个与包同名的公开函数，只接受一个位置参数 `task`：

```python
from openprogram.agentic_programming import agentic_function
from .steps.prepare import prepare


@agentic_function
def project_report(task: str) -> str:
    return prepare(task)
```

`__init__.py` 重新导出入口：

```python
from .workflow import project_report

__all__ = ["project_report"]
```

`steps/__init__.py` 保持为空。`steps/prepare.py`：

```python
from openprogram.programs.workflow.goal import goal


def prepare(task: str) -> str:
    request = task.strip()
    if not request:
        raise ValueError("A report request is required")
    return goal(
        "Prepare a draft report using only supplied or verified evidence. "
        "Do not submit it to an external service. Request: " + request,
        max_rounds=2,
    )
```

这个例子的 README 应说明：输入描述时间范围、证据和格式；输出是草稿字符串；Goal 可以使用配置的模型和工具检查证据；不允许向外部提交。真实模型行为需要单独验证。取消或失败不表示报告完成，重试可能重复证据收集。

## 编写行为测试

验证预期输出、非法输入和允许请求的副作用。发布测试应模拟外部工作，因为沙箱禁用网络。只检查 `callable(entrypoint)` 不能证明行为正确。

示例 `tests/test_workflow.py` 验证只生成草稿的要求，并确保空输入不会调用 Goal：

```python
from workflows.project_report import project_report


def test_draft_request(monkeypatch):
    calls = []

    def fake_goal(prompt, **kwargs):
        calls.append(prompt)
        return "verified draft"

    monkeypatch.setattr("workflows.project_report.steps.prepare.goal", fake_goal)
    result = project_report.__wrapped__("  this week's verified commits  ")
    assert result == "verified draft"
    assert len(calls) == 1
    assert "this week's verified commits" in calls[0]
    assert "Do not submit" in calls[0]


def test_empty_request(monkeypatch):
    def unexpected_goal(*args, **kwargs):
        raise AssertionError("invalid input reached the Goal")

    monkeypatch.setattr("workflows.project_report.steps.prepare.goal", unexpected_goal)
    try:
        project_report.__wrapped__("   ")
    except ValueError:
        return
    raise AssertionError("empty input was accepted")
```

这里的 `__wrapped__` 是装饰器保留的原始 Python 函数，用于隔离的行为测试。正常使用时调用公开函数或提交聊天表单。这些测试不证明模型质量、Runtime 集成或外部操作已经成功。

## 允许的导入与组合

包顶层允许文档字符串、受支持的 `from ... import ...`、可选 `__all__` 和函数定义。类、普通 `import x`、顶层可变常量、任意顶层调用以及覆盖 `llm`、`agent`、`goal` 等托管名称都会被拒绝。

绝对 import 可以引用 `openprogram.agentic_programming`、`openprogram.programs.workflow.*`、`openprogram.programs.tools.*`，或者一个具名 Workflow：

```python
from workflows.project_report import project_report
```

活动目录中的这个公开名称与 `openprogram.programs.workflow.project_report` 对应同一个已授权 callable。包内辅助模块使用相对 import。发布时解析具名 Workflow 依赖，拒绝缺失和循环依赖，并记录确切 Git revision。Snapshot 执行使用这些固定依赖，不使用最新活动包。不要把 checkout 位置写入 import 字符串。

## 验证、测试和发布

使用与 OpenProgram 相同的 Python 环境。pytest 已列为运行依赖；行为测试还需要可用的操作系统沙箱。使用源码 checkout 时，安装当前项目：

```bash
python -m pip install -e .
```

对包目录执行：

```bash
openprogram workflows validate ./project_report --json
openprogram workflows test ./project_report --json
openprogram workflows publish ./project_report --json
```

| 命令 | 可观察结果 |
| --- | --- |
| `validate` | 检查元数据、边界、语法、import、入口签名、重新导出、辅助模块和测试；不导入或执行包，返回 `executed_tests: false` |
| `test` | 复制已验证源码及固定依赖，在强制沙箱中执行 pytest；成功时返回 `executed_tests: true`、`sandboxed: true` 和测试输出，不发布 |
| `publish` | 重新验证并测试实际发布的 snapshot，把它提交到 Workflow 目录，登记来源，返回 `workflow_id` 和不可变 Git `revision` |

行为测试目前支持具有可用操作系统沙箱的 macOS 和 Linux。进程限时 60 秒，禁用网络和 pytest 插件自动加载，清除凭证环境变量，禁止修改 snapshot 源码，使用隔离的 home 和临时工作区，并保证测试结束后没有后台进程继续运行。macOS 禁止创建子进程；Linux 将后代进程限制在私有 PID namespace 中。需要跨平台发布的测试应 mock 外部进程调用。不会退回无沙箱执行。测试失败、超时或沙箱不可用时，不发布。

发布不修改原始编写目录。已有目标默认拒绝覆盖，替换必须明确指定：

```bash
openprogram workflows publish ./project_report --replace --json
```

替换还要求目标 Git 仓库干净；先保留或提交目标中的有意修改。最终发布会在锁内再次检查目标状态。以前的测试报告不能替代对本次 snapshot 的实际测试。

编写 Agent 的 `create_workflow` 和 `revise_workflow` 继续作为独立的生成式入口，发布前也执行同一强制沙箱测试。测试失败时不发布，也不会执行用户的真实任务。上述手工命令用于发布人工编写的文件。旧 `entry.py` 格式只用于历史版本和恢复兼容，新包不使用该格式。

## 相对路径与已安装 App

发布包相对于 OpenProgram 项目位于 `openprogram/programs/workflow/<workflow_id>`。项目内来源使用有明确范围的 POSIX 相对路径：

```json
{"scope": "programs", "path": "workflow/project_report", "kind": "workflow-publish", "source": "workflow:project_report"}
```

范围相对于 `openprogram/programs/` 解析，不相对于当前聊天的工作目录。移动源码 checkout 不改变身份。路径越界、带范围的绝对路径、反斜杠、指向外部的符号链接以及多目录匹配歧义都会被拒绝。

已安装 App 需要一次明确的源码目录绑定，才能找到另外的 checkout。这项安装设置不在每个包中重复保存。本地框架开发从本次要安装的 checkout 执行 `scripts/refresh-local-app.sh`，移动 checkout 后也一样。该命令会重建并重启默认安装，不是只读验证。

对于以前已授权但 checkout 前缀失效的 Workflow，已知目录中存在结构有效的对应包时会自动迁移。仍存在的外部位置保留外部身份。迁移不会授权旁边的其他目录。撤销来源授权后，即使目录当时不存在、后来重新创建，也不会恢复授权。

## 收藏、Use 与排查

打开 **Abilities → Programs**，刷新并选择 Workflow。收藏保存公开函数名。**Use** 在聊天中打开参数表单，提交表单之前不执行。

Programs 与侧边栏共享可调用目录。函数不在缓存中时，Use 请求当前目录；函数不可用或请求失败时显示明确错误。失败刷新保留最近一次成功目录。旧响应不能覆盖新目录，离开聊天会取消待打开的表单。

如果源码可见但 Use 不可用，检查包验证、发布结果和来源授权，然后刷新 Programs。源码可见不证明 Python import 成功。行为测试失败时检查返回输出；在线模型验证与沙箱发布测试分开进行。目标仓库有未提交修改时，必须先处理，`--replace` 才能成功。
