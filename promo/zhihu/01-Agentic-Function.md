# 候选标题

1. OpenProgram技术解析（一）：像调用函数一样调用大模型
2. 一个装饰器把Python函数变成Agent：@agentic_function是怎么工作的
3. Agentic Programming的核心原语：docstring是prompt，参数是输入，返回值是输出

---

Agent在软件系统中，输入和输出都需要专门设计：

1. 输入agent的内容需要设计prompt。需要控制这次调用给模型看什么：任务说明怎么写、当前的数据和状态怎么塞进去、历史对话带多少、格式要求放在哪。这些信息往往散在程序各处，得先收集起来拼成一段文本，才能发给模型。
2. 生成的内容，通常需要后处理。比如：用代码去解析、按它的结果决定下一步、把它和数据库或日志里的东西对起来。但生成内容和代码之间的耦合度目前很低：模型的输出先取出来，再专门写一段代码去解析、分析、分发。生成和处理是两个割裂的阶段，来回搬运，效率不高。

于是我们想：能不能在写代码的时候，这些信息就自动传给大模型，而不需要专门去设计？对应上面两点：

1. 输入的时候，上下文自动拼接。程序运行的整个过程中，函数的注释、参数、前面语句算出来的变量、之前模型调用的结果，自动组装成这次调用的上下文。模型在被调用的那一刻，自动就知道自己该干什么、处在什么背景环境下、这个软件运行到了哪一步，而不是靠人把这些信息一条一条收集、拼接、再喂给它。
2. 输出的时候，模型自动知道自己该输出什么。输出的形式在调用点就约定好了：落回哪个变量、是几个类别里选一个还是一段生成的文本，模型按约定给出结果，代码直接拿到就能用，不再需要专门写一段后处理去解析和分发。

我们开源的[OpenProgram](https://github.com/Fzkuji/OpenProgram)就是按这个想法设计的，核心是一个装饰器：`@agentic_function`。加上这个装饰器后，函数在被调用时会收到一个`runtime`参数，函数体内通过`runtime.exec(...)`就能调用agent。agent的system prompt取自函数的docstring，上下文由框架自动拼接（函数的参数、之前的调用历史都在里面），agent的输出按调用点写好的约定解析后落回变量。这篇文章讲它是怎么工作的。

顺便聊聊这个东西该叫什么。它目前没有公认的名字和定义。你可以说它是"把Python函数变成agent"，可以说是"像调用函数一样调用大模型"，可以说是"模型调用内联进代码"，也可以从范式角度叫它Agentic Programming，或者反过来叫LLM-as-Code（我们论文用的名字）、函数即agent、代码与模型的混合编程。名字很多，指向的是同一类东西：模型调用不再隔着一层接口在代码外面，而是直接长在程序结构里，和普通语句共享变量、控制流和作用域。这是一类新的写法，不是给现有SDK换一层壳：壳改变的是调用的样子，这里改变的是模型调用和程序其余部分的关系。后面几篇讲的DAG上下文、多agent协作，都是这层关系确立之后才自然长出来的东西。

再多说几句闲话。现在围绕大模型搞基建，有一种流行的预测：接下来会把过去计算机系统的那一整套（操作系统、中间件、各种层层抽象）原样在大模型上重建一遍，历史完整重演一次。我们的主张是：这不会发生。有些层就是不会再出现了：凡是模型能直接做的事，不会有人再为它专门造一层系统；被AI取代的部分是真的被取代，不是换个地方重做一遍。未来的形态更可能是：绝大部分任务直接丢给AI去完成，只有极少数需要严格保证的部分才落成代码、落成系统。这也是我们把框架设计成现在这个样子的原因：代码在这套体系里不是主体，是留给"必须严格正确"的那一小部分的工具，剩下的都交给模型。

## 方法：装饰器把agent接进函数

需求：一个工单分诊功能，把工单分成bug / feature / question，然后起草回复。

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

OpenProgram下的写法：

```python
@agentic_function
def triage(ticket: str, runtime=None) -> str:
    """Classify the ticket as bug / feature /
    question, then draft a reply."""
    kind = runtime.exec(                    # agent分类
        ticket, choices=["bug", "feature", "question"])
    if kind == "bug":                       # 代码分支
        logs = search_logs(ticket)          # 普通Python
        return runtime.exec(                # agent分析根因并起草回复
            f"Find the root cause in these logs, "
            f"then draft a reply with a workaround:\n{logs}")
    return runtime.exec("Draft a short reply.")
```

![](figures/01-fig1-comparison.png)

两段代码做的是同一件事。对照着看，函数的三个部件分别承担了agent调用的三个要素：

- **docstring就是system prompt。**函数注释里写这个agent的角色和任务说明，框架把它作为系统提示发给agent。写在函数上，Python本来就有这个位置。
- **参数就是输入。** `ticket`是每次调用时带进来的任务信息：调用`triage("app crashes on login")`，这条工单就成了这次agent调用的输入。函数体内还可以继续追加，bug分支里把查到的`logs`拼进下一次调用，代码算出来的中间结果就这样成了agent的输入。
- **返回值就是约定好的输出。** agent的回答按约定落回变量：`kind`一定是三个类别之一，最终`return`的是起草好的回复。拿到手的就是能直接用的值。

方法本身就这么多。下面按输入、上下文、输出、结构四个方向，讲用了它之后得到的好处。

## 用了之后的好处

### 输入更灵活：由代码现场拼装

写进函数之后，agent的输入是一个普通的Python表达式，调用发生那一刻才拼出来，能拼进去的东西很多：

- **前置逻辑的结果。** `search_logs(ticket)`先查日志，查到什么拼什么。检索、数据库查询、调外部API、读文件，全是普通Python，算完的变量直接进f-string。
- **上一次agent调用的输出。** `kind`是上一次`runtime.exec()`的结果，它可以决定下一次调用发不发生（`if kind == "bug"`）、发什么内容。多次调用之间用代码衔接，中间结果留在变量里。
- **循环里的动态内容。** `for chunk in split(document): runtime.exec(f"总结这一段:\n{chunk}")`，每轮输入不同，循环本身是确定的代码。

条件判断还可以决定这次要不要调agent。规则能处理的分支直接用Python处理掉，需要理解和生成的地方才发起调用，省token，也省延迟。

### 上下文自动管理：作用域跟着函数走

上面说的是单次调用的输入怎么拼，跨越多次调用的上下文同样不用手动管。每次函数调用和agent调用都记在一张执行DAG上，框架顺着这张图自动组装每次调用看到的历史。函数还可以在装饰器上声明自己的上下文边界：

```python
@agentic_function(expose="io", render_range={"callers": 0})
def audit(repo: str, runtime=None) -> str:
    """Read every file and report risky patterns."""
    ...
```

`render_range={"callers": 0}`让`audit`在隔离的草稿上下文里跑：读一万个文件的中间过程全部留在里面，返回后整体回收；`expose="io"`让父级只看到它的输入和结论。子任务再重，主对话也不膨胀。

这件事直接影响token开销。单条对话的agent每轮都要把全部历史重发一遍，n轮下来是$O(n^2)$；上下文按函数作用域分段之后，一段内容只在自己所在的作用域和它下面的调用里被重复读到，重复次数正比于调用嵌套的深度，正常程序的深度在$\log n$量级，总量落在$O(n\log n)$（这是理论估计，我们还没有实测数据）。

这套DAG上下文是整个设计里内容最多的部分，值得单独一篇，下一篇《DAG Context》详细讲。

### 输出直接可编程：以门控为例

agent的回答落回变量，立刻参与`if`、`for`、`return`。这里有一个前提：回答必须真的落进约定里，`if kind == "bug"`才有意义。以`choices`为例，看这个约定是怎么保证的。

```python
kind = runtime.exec(ticket, choices=["bug", "feature", "question"])
```

`choices`是`runtime.exec()`的一个参数，写在调用点上，不用写进docstring，也不用写进输入。框架看到它，会自动在这次调用的输入末尾附上选项菜单和一条收尾指令：先做该做的分析，回复的最后必须是一个JSON，从菜单里选出一项。回复拿回来后，框架按菜单解析。真实运行日志长这样：

```
llm  → "probably a feature request"
gate ✗ no parseable pick from ["bug", "feature", "question"]
llm  → {"call": "feature"}
gate ✓ → branch taken in Python
```

第一次回答含糊，解析不出合法选项，框架带着失败原因再问一次；第二次给出合法值，Python分支接着跑。重试由框架完成，函数体内的代码在拿到合法值之后才继续执行。

于是输出的约定写在代码里，后面的控制流可以放心依赖它。agent负责选择，框架保证这个选择一定合法，`kind`进`if`分支的时候，和任何一个普通Python变量没有区别。

![](figures/01-fig3-gate.png)

### 工具链直接可用：一份实现三种调用

agent写成函数之后，软件工程几十年攒下的工具链全部直接可用：单元测试给它写断言，类型检查器检查它的签名，import把它组合进别的模块，git管它的版本。别的代码调它的时候，不需要知道里面有agent。

复用面也跟着展开。一个`@agentic_function`放进`openprogram/programs/functions/agentic/<name>/__init__.py`，并在`openprogram/programs/_registry.py`中注册模块，同一份代码有三种用法：

```bash
# 1. 在聊天界面里,agent按名字挑它当工具用
# 2. headless跑,可以进脚本进CI
openprogram programs run triage --arg ticket="app crashes on login"
```

```python
# 3. 直接import,当普通函数调
from openprogram.functions.agentics.triage import triage
result = triage("app crashes on login")
```

在聊天界面里它是可以被按名字挑选的工具，在终端里它是一条CLI命令，能进脚本、进定时任务、进CI，在别人的工程里它是一个可以import的库函数。三种场景共用同一份实现，改一处三处同时生效。

### 层级嵌套：可扩展性的来源

函数可以调用agent，agent干活时可以调用别的函数，那个函数里还可以再调agent，层级随便嵌。

这带来一个实用的分工方式：复杂、模糊、不好用代码写清楚的流程，整段交给agent去做；需要严格控制流程和节奏的地方，用代码写出来，逻辑对不对、每一步有没有执行，都能严格保证。想松就松，想紧就紧，松紧还能逐层交替，可扩展性就是从这个嵌套结构里来的。

复杂的agent workflow也是这样搭起来的：流程用代码固定，每一步里的判断交给agent，整条链路可读、可测、可版本管理。

## 框架还提供的几个能力

### 门控只约束收尾，不约束过程

`choices`不是强约束解码。附加的指令是：先做你需要做的工作，回复的最后必须是一个从菜单里选一项的JSON。agent在给出选择之前可以正常分析、调工具、写推理，只有收尾被约束，既保证返回值合法，又保留完整的推理空间。

### agent在注册过的函数里选择

agent运行时做的事情是从已注册的函数和选项里挑，代码本身在编写期就固定下来。动作空间明确，每一步选了什么都有记录，可以重放、可以定位到具体哪一步选错，确定性的部分在运行之前就能review。

### prompt固定且可读

docstring写的什么，agent收到的就是什么。随时打开函数就能看懂这个agent当前的行为，改prompt就是改注释，和改代码走同一套review流程。

### 每次调用可以单独配置

`runtime.exec()`接受按次生效的参数：`model=`给这一步换个模型（便宜的做分类，贵的做生成），`toolset=`换一套工具，`tools_deny=["bash", "edit"]`限制只读步骤的权限。粒度是单次调用，一个函数内部就能混用几档模型和几套工具。

### 重试和超时由框架统一处理

网络断、限流、超时、输出解析失败，`runtime.exec()`内部自动重试（请求层、流式层、门控重问），所有重试共享同一个墙钟截止时间，一次调用的耗时上限始终可预期。这部分可靠性代码不需要使用者自己写。

## 为什么是"函数"

回头看，上面这些好处是同一件事的不同侧面：agent调用一旦长进函数里，函数拥有的一切（灵活的参数、可靠的返回值、完整的工具链、清晰的作用域、天然的嵌套），agent调用就都有了。

我们把这个范式叫Agentic Programming，正式表述在论文里（已被KDD 2026 AgenticSE workshop接收）。`@agentic_function`是它的第一块砖，后面的DAG上下文、多agent协作、事件基础设施都建在这上面，那些是另外几篇的内容。

- 代码：https://github.com/Fzkuji/OpenProgram
- 论文：*LLM-as-Code: Agentic Programming for Agent Harness*，[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)
