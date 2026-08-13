# 询问 OpenProgram 自身

"OpenProgram 能不能做 X""怎么配 Y"这类问题，这份文档站本来就有答案；模型凭着对别的 agent 产品的印象回答，正是错误答案的来源。`run_docs_question` 靠读这些页面来回答：它派一个只读的 agent，工作范围锁在仓库的 `docs/` 目录内，并报出答案来自哪几页。

在 Functions 面板里以 `run_docs_question` 运行，或从 Python 调用：

```python
from openprogram.programs.agentic_functions.docs_question import run_docs_question

result = run_docs_question("能让一个会话一直做到条件成立吗？")
```

## 它读什么

整棵文档树，此外什么都不读。这个 agent 只拿到四个工具（`read`、`grep`、`glob`、`list`），写不了、改不了、也起不了 shell，prompt 把它的工作范围钉在给定的 `docs/` 目录上。它也看不到派生它的那轮对话：问题就是它的全部交待，所以答案来自页面，而不是来自会话当时在聊什么。

它不是上来就开文件。prompt 里带着每个英文页的路径和标题清单，agent 先按路径和标题挑候选页，再去读正文。英文页（`xxx.md`）为准；旁边的 `xxx.zh.md` 在问题是中文或英文页有歧义时对照着看，两者不一致时报出的是英文页的说法。

生成的参考页（`reference/cli/`、`reference/config-keys.md`、`reference/provider-registry.md`）是构建产物，所以没构建过站点的检出里，清单上就少这几页。不会因此报错。

## 三种回答，不是两种

"OpenProgram 支不支持 X"这个问题有三种诚实的回答，它们被分开：

| `covered` | 回答 |
|---|---|
| `true` | 文档回答了这个问题。答案照页面的写法给出命令、配置键或路径。 |
| `true` | 文档写了这东西**不**支持，或者写的行为和问题的假设不一样。这是文档里的事实，同样附出处。 |
| `false` | 文档根本没提。答案直说没覆盖，并指向最接近的相关页，让你知道这个话题该落在哪里。 |

值得有的正是第三种。"文档未覆盖"是一个真答案，猜某个功能存不存在不是；prompt 明确禁止用对 agent 产品的泛泛认识去补这个缺口。

## 返回什么

```python
{"answer": "…", "sources": ["capabilities/goal.md"], "covered": True}
```

`sources` 是相对 `docs/` 的路径，按引用顺序去重。指向不存在页面的引用会被丢掉，所以编出来的页名不会作为出处递到你手上。`covered` 为 `false` 时，`sources` 装的是 agent 找到的最接近的相关页，而不是回答了问题的页。

空问题在派 agent 之前就被拒掉；agent 的回复无法表达成这个结构时直接报错，而不是返回半个答案。

## 答案的来源

[概览](README.md)以及本站其他 Tab 下的全部内容。答案不对，要修的是它引的那一页：这个函数报的是文档，它不读代码。
