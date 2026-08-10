# Language server 工具

三个工具让模型直接问 language server 类型检查器知道的事：这个文件当前有哪些错误、某个符号真正定义在哪、它到底在哪些地方被用到。答案来自当前工作树的状态，所以一次编辑刚落盘就是准的，不用跑测试。

## 装 server

不管有没有装 server，这三个工具都注册着。没装时它们返回 `unavailable: install …` 并给出安装命令，不崩溃，模型也能知道这条路存在。

```bash
npm install -g pyright                                    # Python
npm install -g typescript-language-server typescript      # TypeScript / JavaScript
```

| 语言 | 文件后缀 | Server 二进制 |
|---|---|---|
| Python | `.py`、`.pyi` | `pyright-langserver` |
| TypeScript / JavaScript | `.ts`、`.tsx`、`.js`、`.jsx`、`.mts`、`.cts` | `typescript-language-server` |

后缀不在表里的文件会直接返回一条说明。

## 三个工具

| 工具 | 参数 | 回答 |
|---|---|---|
| `lsp_diagnostics` | `file` | 该文件的错误与警告 |
| `lsp_references` | `file`、`line`、`column` | 该位置符号的每一处使用 |
| `lsp_definition` | `file`、`line`、`column` | 该符号的声明位置 |

`file` 是绝对路径。`line` 和 `column` 从 1 开始，跟 `read` 打印的行号、跟 traceback 里的行号一致，并且要指向符号本身而不是行首。

`lsp_diagnostics` 每条问题一行：

```
runner.py: 2 diagnostics
14:5 warning: "requests" is not accessed [pyright]
27:12 error: "widget" is not defined [pyright]
```

`lsp_references` 和 `lsp_definition` 返回 `path:line:column` 加上那行源码，路径相对于工作区根目录：

```
3 references
openprogram/agent/loop.py:88:9   result = dispatch(call)
openprogram/agent/loop.py:140:5  dispatch(retry)
tests/unit/test_loop.py:31:11    monkeypatch.setattr(loop, "dispatch", fake)
```

结果超过 50 条会截断并给出剩余条数，这时应该把问题问得更窄，而不是翻页。

## Server 怎么跑

每个语言、每个工作区一个 server。工作区取最近的、含 `pyproject.toml`、`setup.py`、`package.json`、`tsconfig.json` 或 `.git` 的祖先目录，同一个根下的两个文件共用一个 server 进程。

Server 首次使用时启动，之后在进程生命周期内缓存复用，OpenProgram 退出时关闭。Server 进程走 OpenProgram 启动子进程的同一条路径，所以配置的[沙箱](../reference/design/runtime/sandbox.md)对它同样生效：默认的 `workspace-write` 模式下，language server 只读工作区、不往外写，这正好够用。

## 和 grep、CodeGraph 的分工

`grep` 匹配文本，所以它会把注释里同名的字符串报出来，也会漏掉经过别名的调用。`lsp_references` 解析符号，这两类错都不会犯。

CodeGraph 是整个代码库的预建索引，适合自顶向下读不熟的代码。language server 补上索引给不了的东西：针对文件此刻磁盘状态的实时诊断。
