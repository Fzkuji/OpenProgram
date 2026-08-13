# 自编程agentic workflow

`agentic_workflow`是OpenProgram的自编程agentic workflow（self-programmed agentic workflow）：agent自己把任务写成一个真正的Python程序，再由框架执行。planner用框架的积木组合程序：三层LLM原语加上注册的agentic function，控制流就是Python本身的`if`、`for`、异常。运行模型和开发者写代码一样：整个程序从头到尾跑完；崩了就看报错、改代码、整个重跑，已完成的调用直接回放上次结果，效果上从出错处继续。

在Programs面板里以`agentic_workflow`运行，或从Python调用：

```python
from openprogram.programs.agentic_functions.agentic_workflow import agentic_workflow

result = agentic_workflow("把 auth 模块迁到新客户端并更新它的测试")
```

每次调用新建一个独立实例，在会话仓库下有自己的目录：`workflows/<run_id>/`，存`code.py`和`state.json`。实例之间什么都不共享，想同时跑几个流程就跑几个。

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
| 注册的agentic function | `AGENTIC_MODULES`注册表里的全部函数，按名直接调用。planner的prompt里带着这份清单。 |

三层是组合关系：goal基于agent，agent基于llm。

模块运行前先校验：能解析、必须有`workflow()`、不许import。无效代码带着具体错误打回planner重写，改到能跑为止。

程序里没有任何checkpoint语法。存档是框架的事：注入环境里的每个可调用都被包了一层，真实执行前后写`state.json`，键是（函数名，第几次被调，参数摘要）。验证也不是框架强加的：planner把检查写成程序的一步，不满足就`raise`，交给修订回环。

## 断点续跑：整个程序重跑，调用回放

没有调度器，没有"取下一项"。续跑就是把`workflow()`从第一行重新执行一遍，唯一的机制是调用边界的短路：`state.json`里已完成的调用（同名、同次序、同参数摘要）不真正执行，直接返回上次的结果。重跑时程序飞速掠过做完的部分，到第一个没做完的调用才真正干活。控制流每次都完整重走（`if`重新判断、`for`重新循环），恢复的只是昂贵调用的结果。

进程被杀同理：状态变化先写盘再动手，拿`run_id`再跑一遍即续。续跑是显式的：每次`agentic_workflow(task)`都新建实例（返回值带`run_id`），续跑要传入既有实例的`run_id`。不存在按任务文本匹配旧运行这种事。

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
