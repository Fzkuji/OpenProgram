# 候选标题

1. 我们开源了 OpenProgram：把 Agent 写成 Python 函数
2. Agentic Programming：Python 定流程，LLM 做判断——我们的开源 Agent Harness
3. 别再让 LLM 决定一切了：OpenProgram 的四个核心机制

---

我们开源了 [OpenProgram](https://github.com/Fzkuji/OpenProgram)，一个通用 Agent Harness，背后是我们提出的编程范式：**Agentic Programming**。对应论文 *LLM-as-Code: Agentic Programming for Agent Harness*（[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)）已被 **KDD 2026 AgenticSE Workshop** 接收。

这是系列文章的第一篇，讲整体思路。后面每篇细讲一个机制。

## 问题：现在的 Agent 框架把控制权全交给了模型

主流 Agent 框架的做法是：做什么由 LLM 规划，什么时候停由 LLM 判断，怎么做由 LLM 选工具。代价大家都体会过：

- 同样的输入，每次跑出不同的轨迹；
- 每一步都把全部历史塞回模型，上下文爆炸；
- 没人能保证任务一定跑完；
- 出了问题，分不清是 prompt 问题、工具问题还是模型幻觉。

根源在于：**本来可以用确定性代码完成的工作，被交给了一个黑盒概率系统。**

反过来把一切都硬编码，又丢掉了模型的智能。我们的答案是在两者之间逐步交错：**流程你想固定的部分用 Python 写死，只有需要理解、生成、判断的地方才调 LLM。** LLM 从"控制者"变成一个被你调用、约束、组合的工具。

## Agent 就是一个 Python 函数

同一个工单分类 Agent，两种写法对比。传统写法要手写 prompt 模板、手写工具 JSON schema、手动解析返回、解析失败自己重试。在 OpenProgram 里：

```python
@agentic_function
def triage(ticket: str, runtime=None) -> str:
    """Classify the ticket as bug / feature /
    question, then draft a reply."""
    kind = runtime.exec(                    # 🤖 LLM 判断
        ticket, choices=["bug", "feature", "question"])
    if kind == "bug":                       # 🐍 代码决定分支
        logs = search_logs(ticket)          # 🐍 普通 Python
        return runtime.exec(                # 🤖 LLM 生成回复
            f"Reply using:\n{logs}")
    return runtime.exec("Draft a short reply.")
```

- **docstring 就是 prompt**；
- **类型标注就是工具 schema**；
- `choices=[...]` 是一道代码门：模型答不出合法选项就被打回重答，直到通过；
- `runtime.exec()` 是唯一的 LLM 入口，每次调用是执行图上一个可重试的节点；
- 其余的 `if` / `for` / `return` 是普通 Python，每次都确定执行。

外部调用方看不出区别——`triage(ticket)` 和任何 Python 函数一样，可以单测，可以组合，可以放进 `for` 循环。

## 四个核心机制

多平台、多模型、多渠道这些是基本盘（macOS / Linux / Windows，任意 LLM，终端 / 浏览器 / IM）。OpenProgram 真正不同的是 harness 里的四个机制——一个原语，加上它解锁的三件事。每个后面单独写一篇。

**① Agentic Function——一切的原语。** 上面那个例子。一个装饰器把 Python 函数变成 Agent：没有 prompt 模板文件，没有工具 JSON，没有手写解析。

**② DAG Context——原生多智能体。** 每个用户输入、每次 LLM 调用、每次函数调用，都是同一张扁平 DAG 上的一个节点。上下文是可寻址的节点，不是每个 Agent 私有的 buffer。于是多智能体操作全部退化为"指向另一组节点"：`spawn_branch(...)` 起一个干净上下文的子 Agent，`message_branch(...)` 跨分支发消息拿回复，fork 一个节点就能试另一条路而不丢原路，会改文件的分支自动跑在独立 git worktree 里。

**③ Agentic Workflow——可信且自生长的 Agent。** 代码门是绕不过去的：模型输出过不了校验，就被送回去重新决策，它没法"说服"流程跳过检查。同时 Agent 用普通文件工具编辑自己的 `@agentic_function` 文件，watcher 热加载，下一轮新工具就上线——不需要专门的 `create()` / `fix()` 机制。

**④ Event Infrastructure——主动式 Agent 的地基。** 全进程一条事件总线，Agent 循环、认证、上下文、渠道、记忆都往上面发同一种 `Event(type, payload, ts)` 信封，任何东西都能按类型订阅任何东西。这部分我们如实标注：管线已就位，上面的主动策略层留给使用者构建。

## 和现有项目的关系

"把 Agent 写成带类型的普通 Python，docstring 即 prompt"这个直觉，好几个团队独立走到了。我们认为这种收敛正是方向对的证据，差异才是有意思的地方：

- **NVIDIA NOOA**：面向对象，状态挂在 `self` 上，模型通过往 REPL 里写 Python 来行动（CodeAct）。我们让模型在已注册函数中选择而不是现场生成代码——动作空间更窄，更好沙箱、更好重放。
- **DSPy**：用 Signature 替代手写 prompt，然后对着指标优化 prompt 本身。我们的 prompt 固定且可读，力气花在执行结构上——DAG、重试、上下文作用域。两者互补。
- **Marvin / Mirascope**：聚焦单次良构调用。我们补的是调用**之间**发生的事：共享执行 DAG、spawn、fork、每次调用的上下文预算。
- **LangGraph**：图是事先声明的节点和边。我们的图是**从调用栈录下来的**——你写普通 Python，DAG 是实际执行的痕迹。
- **smolagents**：同样认为"代码是动作语言"，但在沙箱里让模型现写代码。我们把代码绑定在**编写期**（`@agentic_function`），确定性部分在运行前就可审查。

## 上手

```bash
curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash
openprogram
```

Windows 用 PowerShell 一行同理。装完选一个 provider（任何 OpenAI 兼容端点都行），终端和浏览器两个界面共享同一批会话。

项目 AGPL-3.0 开源：https://github.com/Fzkuji/OpenProgram
论文：https://arxiv.org/abs/2606.15874

这是一个范式提案加参考实现。欢迎讨论、欢迎其他语言的实现、欢迎用实际用例验证或挑战这套做法。下一篇细讲 `@agentic_function` 这个原语。
