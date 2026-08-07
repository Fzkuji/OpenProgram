# 候选标题

1. 多智能体框架里最贵的一行代码，是把消息数组复制一份
2. 上下文不该是每个 agent 的私有 buffer——OpenProgram 的 DAG Context 设计
3. 当会话变成一张 git DAG：spawn、fork、跨分支消息为什么都只是一次调用

---

我们在做 OpenProgram（一个开源的通用 agent harness）时，最早定下的一条设计决定不是模型怎么调、工具怎么注册，而是**上下文放在哪**。这篇讲这条决定：上下文是一张扁平 DAG 上的可寻址节点，不是每个 agent 手里的一份私有消息数组。多智能体的所有操作，都是这条决定的推论。

## 常见框架的做法，以及它贵在哪

主流做法里，一个 agent 就是一个消息列表：`messages = [...]`，每轮 append，调用时整个发给模型。要多个 agent 协作，就出现第二个、第三个列表。子 agent 需要背景？从父列表里挑几条拷过去。子 agent 干完了？把结果摘要 append 回父列表。想回退到三轮之前试另一条路？对不起，列表是线性的，你得自己存快照。

这套做法的成本不在代码量，在**状态被复制之后就分叉了**。父子两份消息数组各自演化，谁也不知道对方后来发生了什么；"把结果传回去"要专门写编排代码；回溯、对比、审计都没有天然支持。每加一种协作模式，就加一套拷贝和回传的胶水。

## OpenProgram 的做法：一张 DAG，节点可寻址

在 OpenProgram 里，一个会话就是一张扁平 DAG。用户消息是节点，每次 LLM 调用是节点，每次函数调用也是节点。节点有 id，有父指针，全局可寻址。不存在"某个 agent 私有的 buffer"——所谓一个 agent 的上下文，只是"从某个 head 节点往回走能读到的那一串节点"。

每个 `@agentic_function` 用一行声明自己读什么、暴露什么：

```python
@agentic_function(expose="io", render_range={"callers": 0})
def audit(repo: str, runtime=None) -> str:
    """Read every file and report risky patterns."""
    ...
```

`render_range={"callers": 0}` 表示这个子任务在隔离的临时上下文里跑，返回后回收，不污染父 prompt；`expose="llm"` 表示父级能看到它的推理过程而不只是结论；`expose="hidden"` 表示这是父级完全不可见的内部助手。上下文的读写边界写在函数签名旁边，跟代码一起被 review。

## 推论：多智能体操作全部退化成"指向另一组节点"

一旦上下文是可寻址节点，协作操作就不再需要拷贝任何东西。README 里的这张表是字面意思，每一行都真的是一次调用：

| 想做什么 | 一次调用 |
|---|---|
| 起一个干净上下文的子 agent | `spawn_branch(...)` |
| 给另一条分支发消息、拿回复 | `message_branch(message, target=...)` |
| 试另一条路且不丢原路 | fork 那个节点 |
| 让分支安全地改文件 | 它跑在自己的 `git worktree` 里 |

底层其实只有一个原语：**分支间通信**——往某条分支投递内容，触发它跑一轮，结果自动回送发起方。派生子 agent 是"新建一条分支再投递"；跨 session 发消息是"往已存在的分支投递"；综合多条分支是"投递时带上多个来源分支的内容"。三种用法共用同一条投递、触发、回送的路径，target 参数取值不同而已：

```
message_branch(message, target="new")          # 全新分支，跑完自动回流
message_branch(message, target="new:sid:msg")  # 从某个节点 fork 出新分支
message_branch(message, target="sid:head")     # 投给已存在的分支，跨 session 同路径
```

fork 值得单独说一句。因为节点不可变、靠父指针成链，"从三轮之前岔出去试另一个方案"就是新建一个 head 指向那个历史节点。原路一个字节都不动，两条路还能事后对比。这在消息数组模型里要靠手工快照,在 DAG 模型里是免费的。

改文件的分支是唯一需要额外隔离的：上下文靠 DAG 天然隔离了，但文件系统是共享的。所以每条会碰文件的分支跑在独立的 git worktree 里，改坏了丢掉 worktree，改好了合并回来。

## 会话本身就是 git 语义

我们把这套结构做到了底：session 就是 commits + branches + merges。fork 是开分支，多条分支的结论汇给一个模型综合就是 merge，回溯就是 checkout 到历史节点。web UI 右栏有一个 mini-DAG，实时画出当前会话的每个节点和每条边，跟着聊天滚动；分支、合并、attach 都能直接在图上操作。你在终端 TUI 里开的会话，换到浏览器里接着看这张图，数据是同一份。

对调试的意义比对演示的意义大。多 agent 跑挂了,你面对的不是七份互相拷贝过的消息数组,而是一张图:哪个节点产出了错误结论、它当时读到了哪些上游节点、错误怎么沿边传播的,顺着指针走就是。

## 小结

把上下文从"每个 agent 一份私有数组"改成"一张图上的可寻址节点"，多智能体就从一堆需要各自实现的功能，塌缩成一个原语的几种参数化。spawn、fork、跨分支消息、worktree 隔离，都不是 feature list 上并列的四项，是同一条设计决定的四个推论。

代码和文档都在 GitHub：https://github.com/Fzkuji/OpenProgram

配套论文《LLM-as-Code: Agentic Programming for Agent Harness》发表于 KDD 2026 AgenticSE Workshop：arXiv:2606.15874
