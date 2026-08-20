# 自编程agentic workflow

OpenProgram的自编程 workflow 路径会为任务写成真正的Python程序，再由框架执行。planner用框架的积木组合程序：三层LLM原语加上注册的agentic function，控制流就是Python本身的`if`、`for`、异常。运行模型和开发者写代码一样：整个程序从头到尾跑完；崩了就看报错、改代码、整个重跑，已完成的调用直接回放上次结果，效果上从出错处继续。

原先单一的`agentic_workflow(task)`入口正在拆成四个公共入口。搜索、创建、修订和执行保持独立；`auto_workflow`只负责编排这些步骤。该拆分正在实施——新的调用请使用下面四个名字。

| 入口 | 谁可以调用 | 职责 | 禁止 |
|---|---|---|---|
| `search_workflows(task)` | Agent 和用户 | 确定性返回排序候选，并带上固定 revision、合同、权限和命中依据。 | 调用模型、写文件、执行候选或发布。 |
| `create_workflow(task)` | Agent 和用户 | 读取候选组件，生成一个新 package，验证后发布并返回 ref。 | 执行用户任务，或修改已有项目。 |
| `revise_workflow(workflow_id, request)` | Agent 和用户 | 基于指定 revision 创建新候选，验证后发布新 revision。 | 由普通运行失败静默触发，或覆盖旧 revision。 |
| `auto_workflow(task)` | 仅用户表单 | 调用搜索，由一个可见的 selection Agent 判断 reuse/create，再运行选定的 Workflow。 | 出现在 Agent 工具清单或搜索候选里，或自动修订已发布项目。 |

Chat Agent 若已有明显匹配的 Program 或 Workflow，就直接调用；目录较大或候选未加载时才搜索。Agent 不调用`auto_workflow`。需要一次请求完成搜索、选择、必要时创建并执行时，从 Programs 页或函数表单使用`auto_workflow`。在 Programs 页上，这四个名字属于 Workflow 管理能力；`auto_workflow`标为仅用户手动的自动入口。

每次 Workflow 运行仍会新建独立实例，在会话仓库下有自己的目录：`workflows/<run_id>/`，存`code.py`和`state.json`。实例之间什么都不共享，想同时跑几个流程就跑几个。

## 小任务不写程序

planner的第一个决定是这个任务配不配得上一个程序。单个agent一口气能做完的（一处小改动、回答一个问题、写一个文件）直接执行，因为为它写程序的开销超过直接做完。程序留给真正超出一次范围的任务：多个交付物、跨阶段的工作、后一步依赖前一步产出。

## 生成的程序长什么样

planner有只读工具（read、grep、glob、list），输入是任务加函数注册表（积木清单），产出一个普通Python模块，入口`def workflow()`：

```python
def find_issues() -> str:
    return agent(
        "审查 openprogram/auth/ 的代码，逐文件检查错误处理与并发安全，"
        "输出问题清单，按 HIGH / MEDIUM / LOW 分级，每条带文件路径与行号。",
        description="find issues")


def workflow() -> str:
    findings = find_issues()
    if "HIGH" in findings:
        agent("按清单修复 HIGH 级问题，改完跑通相关测试：" + findings,
              description="fix auth")          # 干活的 agent，profile 带工具
        checks = run_tests()                    # 注册表里的 agentic function 直接调
        if "failed" in checks:
            raise RuntimeError("修复后测试仍失败：" + checks)
    return agent("汇总以上结果写报告", description="report")
```

程序里禁止`import`，能调的东西全部由框架注入：

| 注入的名字 | 说明 |
|---|---|
| `llm` | 单次模型请求，无工具，无循环。签名：`llm(prompt, model="", effort="", response_format=None, ...)` |
| `agent` | 工具循环，反复调llm+执行工具直到完成。签名：`agent(prompt, model="", effort="", tools=None, max_iterations=20, ...)` |
| `goal` | 判定循环，反复调agent+用llm判定条件直到满足。签名：`goal(prompt, condition, model="", effort="", max_rounds=10, ...)` |
| `validate_and_retry` | 验证和回溯：执行action，用llm判定结果是否满足check，不满足则执行retry。签名：`validate_and_retry(action, check, retry, max_retries=2)` |
| `route` | 路由选择：让llm从options列表中选择一个。签名：`route(question, options, context="")` |
| `conditional` | 条件分支：llm判定condition（YES/NO）并执行对应分支。签名：`conditional(condition, context="", if_true, if_false)` |
| 注册的agentic function | `AGENTIC_MODULES`注册表里的全部函数，按名直接调用。planner的prompt里带着这份清单。 |

三层是组合关系：goal基于agent，agent基于llm。控制流原语用llm做判定，简化planner的代码生成。

**控制流原语示例**：

```python
def workflow() -> str:
    # 验证和回溯：第一次结果不满足就重试
    files = validate_and_retry(
        action=lambda: agent("找 auth 相关文件"),
        check="文件数量>=3 且包含 oauth",
        retry=lambda: agent("扩大范围，包括 oauth、openid 相关文件")
    )
    
    # 路由选择：让llm选策略
    strategy = route(
        question="选择迁移策略",
        options=["直接迁移", "重构后迁移"],
        context=files
    )
    
    # 条件分支：根据llm判定执行不同分支
    plan = conditional(
        condition="策略是直接迁移",
        context=strategy,
        if_true=lambda: agent("写直接迁移方案：" + files),
        if_false=lambda: agent("写重构方案：" + files)
    )
    
    return plan
```

模块运行前先校验：能解析、必须有`workflow()`、不许import。无效代码带着具体错误打回planner重写，改到能跑为止。

程序里没有任何checkpoint语法。存档是框架的事：注入环境里的每个可调用都被包了一层，真实执行前后写`state.json`，键是（函数名，第几次被调，参数摘要）。验证也不是框架强加的：planner把检查写成程序的一步，不满足就`raise`，交给修订回环。

## 断点续跑：整个程序重跑，调用回放

没有调度器，没有"取下一项"。续跑就是把`workflow()`从第一行重新执行一遍，唯一的机制是调用边界的短路：`state.json`里已完成的调用（同名、同次序、同参数摘要）不真正执行，直接返回上次的结果。重跑时程序飞速掠过做完的部分，到第一个没做完的调用才真正干活。控制流每次都完整重走（`if`重新判断、`for`重新循环），恢复的只是昂贵调用的结果。

进程被杀同理：状态变化先写盘再动手，拿`run_id`再跑一遍即续。续跑是显式的：每次新运行都新建实例（返回值带`run_id`），续跑要传入既有实例的`run_id`。不存在按任务文本匹配旧运行这种事。

## 出错后的修订

程序抛异常时（语法错、积木调用失败、planner自己写的检查`raise`），处理方式和开发者一样：planner拿到异常栈、当前代码和`state.json`里的运行记录，重写`code.py`，然后整个程序重跑。没改动的已完成调用照旧回放，所以修订不推翻已完成的工作。旧版代码留档在实例目录里，修订历史进返回值。

没有abandoned这种状态。无效代码、运行报错都带着具体错误打回planner再改，改到能跑为止。唯一的强制停止是`capped`：40次真实调用执行（回放不计数）触发，到这个份上程序还在长是规划失败。

## 返回什么

```python
{"status": "completed", "run_id": "…", "task": "…", "result": …, "revisions": [...]}
```

| 状态 | 含义 |
|---|---|
| `completed` | `workflow()`正常返回，附结果与全部运行记录。 |
| `capped` | 运行触到40次真实执行的上限。 |

## 和会话目标的分工

[目标](goal.md)让一个会话一直做到条件成立，对话是连续的、每轮都在变长。workflow把计划写成程序，每个调用在自己有界的上下文里跑。终点清楚但路径不清楚时用目标；工作长到"全装在一个上下文里"本身就是问题时用workflow。todo计划板不参与：workflow的状态在自己的实例目录里。
