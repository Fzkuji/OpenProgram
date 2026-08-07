# 候选标题

1. OpenProgram 技术解析（一）：像调用函数一样调用大模型
2. 一个装饰器把 Python 函数变成 Agent：@agentic_function 是怎么工作的
3. Agentic Programming 的核心原语：docstring 是 prompt，参数是输入，返回值是输出

---

大模型生成的内容，很多时候不是终点——生成完还要接着处理：拿代码去分析它、按它的结果决定下一步、把它和数据库或日志里的东西对起来。但生成内容和代码之间的耦合度目前很低：模型的输出先取出来，再专门写一段代码去解析、分析、分发。生成和处理是两个割裂的阶段，来回搬运，效率不高。

于是我们想：能不能把大模型的输入输出直接接到函数调用里？函数体里既有普通语句也有模型调用，输入由代码拼出来，输出落回变量接着被代码处理——直接运行这个函数，就同时用上了代码的确定性和模型的能力。这就是我们开源的 [OpenProgram](https://github.com/Fzkuji/OpenProgram) 的做法。整个框架基于 Python 构建：agent 用原生 Python 函数来写，流程控制就是 Python 的 `if`/`for`，生态里的任何库都能直接 import 进函数体，载体是一个装饰器：`@agentic_function`。这篇文章讲它是怎么工作的。

顺便聊聊这个东西该叫什么。它目前没有公认的名字和定义——你可以说它是"把 Python 函数变成 agent"，可以说是"像调用函数一样调用大模型"，可以说是"模型调用内联进代码"，也可以从范式角度叫它 Agentic Programming，或者反过来叫 LLM-as-Code（我们论文用的名字）、函数即 agent、代码与模型的混合编程。名字很多，指向的是同一类东西：模型调用不再隔着一层接口在代码外面，而是直接长在程序结构里，和普通语句共享变量、控制流和作用域。这是一类新的写法，不是给现有 SDK 换一层壳——壳改变的是调用的样子，这里改变的是模型调用和程序其余部分的关系。后面几篇讲的 DAG 上下文、多 agent 协作，都是这层关系确立之后才自然长出来的东西。

## 方法：一个函数就是一个 agent

需求：一个工单分诊 agent，把工单分成 bug / feature / question，然后起草回复。

常规写法：

```python
TRIAGE_PROMPT = """You are a triage
agent. Classify the ticket as bug,
feature, or question. Reply as JSON."""

TOOLS = [{"type": "function", "function": {
  "name": "triage",
  "parameters": {"type": "object",
    "properties": {"ticket": {"type": "string"}},
    "required": ["ticket"]}}}]

resp = client.chat(TRIAGE_PROMPT, tools=TOOLS)
kind = json.loads(resp)["kind"]     # 解析返回值
if kind not in ("bug", "feature"):
    ...                             # 需要时自己重问
```

OpenProgram 下的写法：

```python
@agentic_function
def triage(ticket: str, runtime=None) -> str:
    """Classify the ticket as bug / feature /
    question, then draft a reply."""
    kind = runtime.exec(                    # 🤖 LLM 决定
        ticket, choices=["bug", "feature", "question"])
    if kind == "bug":                       # 🐍 你决定
        logs = search_logs(ticket)          # 🐍 普通 Python
        return runtime.exec(                # 🤖 LLM 写回复
            f"Reply using:\n{logs}")
    return runtime.exec("Draft a short reply.")
```

![](figures/01-fig1-comparison.png)

行为完全一样。对照着看，这个函数的每个部件都对应大模型调用的一个要素：

- **docstring ≈ system prompt。** 函数注释写的是这个 agent 的角色和任务的固定说明，框架把它作为系统提示。它本来就该写在函数上，Python 语法早就给了位置。
- **参数就是输入。** `ticket` 是每次调用时补充进来的任务相关信息——调用 `triage("app crashes on login")`，这条工单就成了这次模型调用的输入。函数体里还可以继续追加：`f"Reply using:\n{logs}"` 就是把代码算出来的中间结果拼成下一次的输入。
- **返回值是定义好的输出。** 模型的回答不是一段散着的文本，而是按约定落回变量：`kind` 一定是三个类别之一，最终 `return` 的是起草好的回复。输出从"取出来再处理"变成"本来就在变量里"。

方法本身就这么多。下面按输入、上下文、输出、结构四个方向，讲用了它之后得到的好处。

## 用了之后的好处

### 输入更灵活：prompt 由代码现场拼装

常规调用里，prompt 是提前写好的模板，运行时能变的只有留出来的占位符。写进函数之后，模型的输入就是一个普通的 Python 表达式，调用发生那一刻才拼出来——能拼进去的东西一下子多了：

- **前置逻辑的结果。** `search_logs(ticket)` 先查日志，查到什么拼什么。检索、数据库查询、调外部 API、读文件，全是普通 Python，算完的变量直接进 f-string。
- **上一次模型调用的输出。** `kind` 是上一次 `runtime.exec()` 的结果，它可以决定下一次调用发不发生（`if kind == "bug"`）、发什么内容。多次模型调用之间用代码衔接，不需要把中间结果导出去再导回来。
- **循环里的动态内容。** `for chunk in split(document): runtime.exec(f"总结这一段:\n{chunk}")`——每轮输入不同，循环本身是确定的代码。

输入侧还有一层：条件判断可以决定"这次要不要调模型"。规则能处理的分支直接 Python 处理掉，只有真正需要理解和生成的地方才发起调用——省 token，也省延迟。

### 上下文自动管理：输入灵活的另一半

上面说的是单次调用的输入怎么拼；跨越多次调用的输入——也就是上下文——同样不用手动管。每次函数调用和模型调用都记在一张执行 DAG 上，框架顺着这张图自动组装每次调用看到的历史。函数还可以在装饰器上声明自己的上下文边界：

```python
@agentic_function(expose="io", render_range={"callers": 0})
def audit(repo: str, runtime=None) -> str:
    """Read every file and report risky patterns."""
    ...
```

`render_range={"callers": 0}` 让 `audit` 在隔离的草稿上下文里跑——读一万个文件的中间过程全部留在里面，返回后整体回收；`expose="io"` 让父级只看到它的输入和结论。子任务再重，主对话也不膨胀。

这套 DAG 上下文是整个设计里内容最多的部分，值得单独一篇，下一篇《DAG Context》详细讲。

### 输出直接可编程——case study：门控

模型的回答落回变量，立刻参与 `if`、`for`、`return`。这件事有一个前提：回答必须真的落进约定里，`if kind == "bug"` 才有意义。拿 `choices` 这个功能当 case study，看约定是怎么保证的。

```python
kind = runtime.exec(ticket, choices=["bug", "feature", "question"])
```

`choices` 是 `runtime.exec()` 的一个参数，写在调用点上——不在 docstring 里，也不用你写进输入。框架看到它，会自动在这次调用的输入末尾附上选项菜单和一条收尾指令：先做该做的分析，但回复的最后必须是一个 JSON，从菜单里选出一项。回复拿回来后，框架按菜单解析。真实运行日志长这样：

```
llm  → "probably a feature request"
gate ✗ no parseable pick from ["bug", "feature", "question"]
llm  → {"call": "feature"}
gate ✓ → branch taken in Python
```

模型第一次回答含糊，解析不出合法选项，框架带着失败原因再问一次；第二次给出合法值，Python 分支接着跑。重试由框架完成，你的代码只在拿到合法值之后开始执行。

效果就是：输出约定写在代码里，后面的控制流可以放心依赖它。模型负责选择，代码保证这个选择一定合法——`kind` 进 `if` 分支的时候，和任何一个普通 Python 变量没有区别。

![](figures/01-fig3-gate.png)

### agent 变成了普通函数：工具链与三种调用

一个 agent 写成函数之后，软件工程几十年攒下的工具链全部直接可用：单元测试给它写断言，类型检查器检查它的签名，import 把它组合进别的模块，git 管它的版本。别的代码调它的时候，甚至不需要知道里面有大模型。

复用面也跟着展开。一个 `@agentic_function` 放进 `openprogram/functions/agentics/<name>/__init__.py`，文件 watcher 热加载，不用注册不用重启，同一份代码立刻有三种用法：

```bash
# 1. 聊天里,agent 按名字挑它当工具用
# 2. headless 跑,可以进脚本进 CI
openprogram programs run triage --arg ticket="app crashes on login"
```

```python
# 3. 直接 import,当普通函数调
from openprogram.functions.agentics.triage import triage
result = triage("app crashes on login")
```

在聊天界面里它是 agent 可以按名字挑选的工具；在终端里它是一条 CLI 命令，能进脚本、进定时任务、进 CI；在别人的工程里它是一个可以 import 的库函数。三种场景共用同一份实现，改一处三处同时生效。

### 层级嵌套：可扩展性的来源

既然 agent 是函数，它就可以嵌套：我们的函数可以调用 agent，agent 干活时又可以调用别的函数，那个函数里还可以再调 agent——层级随便嵌。

这带来一个很实用的分工方式：复杂、模糊、不好用代码写清楚的流程，整段丢给大模型去做，我们不用再写一大堆复杂代码；而需要严格控制流程和节奏的地方，用实际代码来写，逻辑是不是正确、每步有没有执行，都能严格保证。想松就松，想紧就紧，松紧还能逐层交替——可扩展性就是从这个嵌套结构里来的。

## 设计时的几个考虑

除了上面的主线，还有几处当时想得比较久的地方，也写在这里。

### 约束收尾，不约束过程

`choices` 最容易想到的实现是强约束解码——让模型只能输出选项之一。我们没这么做：附加的指令原文是"先做你需要做的工作，回复的最后必须是一个从菜单里选一项的 JSON"。也就是说模型在给出选择之前可以正常分析、调工具、写推理，只有收尾被约束。选择质量靠的是让模型想清楚再选，而不是把它的嘴堵住只留三个词。

### 让模型在注册过的函数里选，而不是现场写代码

同类工作里有一派（CodeAct、smolagents）让模型直接生成 Python 去执行。我们把代码固定在编写期：模型运行时做的是"从已注册的函数和选项里挑"，动作空间小得多——更容易沙箱、更容易重放、出问题时也更容易定位到底是哪一步选错了。确定性部分在任何东西运行之前就能 review。

### prompt 固定且可读

也有一派（DSPy）把 prompt 交给优化器针对指标去调。我们反着来：docstring 写的什么，模型收到的就是什么，工程师随时能读懂当前行为。力气花在执行结构上——重试、超时、上下文作用域。两条路线其实互补，但"出了问题能直接读懂"对工程落地更重要。

### 每次调用都可以临时改配置

`runtime.exec()` 接受按次生效的覆盖：`model=` 换个模型跑这一步（便宜的做分类，贵的做生成），`toolset=` 换一套工具，`tools_deny=["bash", "edit"]` 给只读步骤上限制。粒度是单次调用，不是整个 agent——一个函数内部就能混用几档模型。

### 重试预算是一个整体

模型调用会失败：网络断、限流、超时、输出解析不出。`runtime.exec()` 内部有多层重试（请求层、流式层、门控重问），设计上它们共享同一个墙钟截止时间，而不是各自为政地相乘——一次调用的耗时上限始终是可预期的。这类可靠性代码人人都要写，写对很难，所以我们把它沉进了框架。

## 为什么是"函数"

回头看，这些好处其实是同一件事的不同面：模型调用一旦长进函数里，函数拥有的一切——灵活的参数、可靠的返回值、完整的工具链、清晰的作用域、天然的嵌套——模型调用就都有了。

我们把这个范式叫 Agentic Programming，正式表述在论文里（已被 KDD 2026 AgenticSE workshop 接收）。`@agentic_function` 是它的第一块砖，后面的 DAG 上下文、多 agent 协作、事件基础设施都建在这上面——那些是另外几篇的内容。

- 代码：https://github.com/Fzkuji/OpenProgram
- 论文：*LLM-as-Code: Agentic Programming for Agent Harness*，[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)
