# Error Handling — 纪律与门禁

> 什么可以被 catch，catch 之后必须说明什么，以及 linter 在哪些路径上强制执行。

## 1. 规则

**只 catch 你能处理的，并为你吞掉的东西留下记录。**

一个不产生日志、不带注释、也没有 fallback 的 catch，与一个尚未被发现的 bug 无法区分。
静默 handler 的大部分代价是后来才付的，由那个正在琢磨"这次写入为什么没落盘"的人来付。

三条具体义务：

1. **精确 catch。** 文件 IO 抛 `OSError`；JSON 抛 `ValueError`（以及
   `json.JSONDecodeError`）；`ContextVar.reset` 用外部 token 抛 `ValueError`、用已消费的
   token 抛 `RuntimeError`。写出这个操作实际会抛的东西，全部。
2. **绝不吞掉程序员错误。** `AttributeError`、`TypeError`、`NameError` 这类是我们自己
   代码的缺陷，不是需要容忍的状况。宽到足以掩盖它们的 handler，要么收窄到掩盖不了，
   要么打日志让缺陷可见。
3. **说明为什么。** 剩下的每一处吞掉，都要带一条含上下文的日志，或者一条说明为什么忽
   略它是正确的注释。理由不显然时两者都写。

## 2. 边界

宽泛的 `except Exception` 在边界上是正当的——在那些地方，让异常继续向上传播会拖垮比
失败操作本身大得多的东西：

- **WebSocket handler 与 HTTP 路由**，异常会断连接而不是返回一个错误。
- **线程与 worker 入口**，异常在那里无人观测地消失。
- **回调调用**，调用方提供的代码运行在我们的循环里。
- **可选子系统**（memory、项目 git、用量计量），缺席只是功能降级，而不是这一轮失败。
- **尽力而为的簿记**，下一轮反正会重新推导出来。

边界上的 catch 仍然要记录足够的上下文，能定位到会话或操作。"边界"是继续运行的理由，
绝不是保持沉默的理由。

## 3. 选日志级别

| 级别 | 何时 |
|---|---|
| `warning` | 丢了用户可能察觉的东西：一次没落盘的写入、一个留在过期状态的 status、一条将会缺失的记录。 |
| `debug` | 一条可选路径按设计降级了：缺席的子系统、尽力而为的缓存、生效了的 fallback。 |

用 `exc_info=True` 保住 traceback，并带上 session id 或等价标识——多个 agent 同时运行
时，一条无法关联到会话的日志几乎没用。

## 4. 好的 handler 长什么样

让持久写入的静默丢失变得可见：

```python
except OSError:
    _log.warning(
        "index.json not saved for session %s", session_id, exc_info=True)
```

把原先掩盖缺陷的宽 catch 收窄：

```python
except (ValueError, RuntimeError):
    # ContextVar.reset raises ValueError for a token minted in another
    # context and RuntimeError for one already spent. Narrowing to only
    # the first lets a cancelled turn's spent token escape and replace
    # the CancelledError.
    _log.debug("context var token already spent or foreign", exc_info=True)
```

只有先弄清全集，收窄才是对的。去查这个调用实际会抛什么：上面的 `ContextVar.reset` 抛
两种毫不相关的类型，只写其中一种的 handler，会把本来被吞掉的失败变成跑出去的失败。

一个可选子系统，写明理由：

```python
except Exception:
    # Memory is optional: an unavailable provider degrades to no memory
    # block, never a failed turn.
    _log.debug("memory system prompt block unavailable", exc_info=True)
```

如果被保护的操作本身已经吞掉自己的错误，或者 catch 的是代码根本抛不出来的东西，这类
handler 直接删掉，而不是加注释。

## 5. 门禁

`ruff` 强制两条规则，配置在 `pyproject.toml`：

| 规则 | 抓什么 |
|---|---|
| `E722` | 裸 `except:`，它连 `KeyboardInterrupt` 和 `SystemExit` 一起吞掉。 |
| `S110` | `try` / `except` / `pass`——异常被丢弃，没有日志、没有注释、没有 fallback。 |

运行方式：

```bash
.venv/bin/ruff check .
```

### 覆盖范围

门禁在三条核心路径上强制执行，这三条目前是干净的：

- `openprogram/store/` — 持久记录
- `openprogram/context/` — 模型看到的东西
- `openprogram/agent/dispatcher/` — 一轮执行本身

它们承载的状态一旦被静默损坏，事后最难诊断。其他位置通过 `per-file-ignores` 静音，因
为几百处存量点会让门禁根本过不了，也就等于没人理它。静音列表就是待办清单：清理干净一
个目录，删掉它那一行，门禁就覆盖它。

`per-file-ignores` 的 pattern 匹配的是整条路径，其中的 `*` 会跨 `/`，所以
`openprogram/*.py` 静音的是整个包而不是顶层那几个模块——顶层模块因此改成逐个列名。新
加 pattern 之前先拿一个嵌套文件验一下：静音过头的 pattern 会把门禁关掉，而且不会让任
何检查失败。

`BLE001`（盲目 `except Exception`）刻意不启用。会打日志并降级的边界 handler 是正确设
计，把它们全标出来产出的是噪声而非信号。第 2 节改由评审来约束这类代码。

ruff 的作用域仅限这道门禁。格式化与 import 顺序刻意不管——这是一条正确性规则，不是一
套风格制度。

## 附录：实现状态

已实现。三条核心路径通过 `E722` 与 `S110`；`ruff` 在 `dev` extra 中，配置在
`pyproject.toml` 的 `[tool.ruff]` 下。

尚未完成：`per-file-ignores` 中被静音的那些目录。`openprogram/webui/` 与 dispatcher 之
外的 `openprogram/agent/` 是剩余存量最集中的两处。

## 相关文件

- [统一运行控制](runtime/execution/execution-control.html) — 暂停、继续、单步、调整、取消，以及 `CancelledError` 继承 `BaseException` 的原因
- [`runtime/dag/overview.zh.md`](runtime/dag/overview.zh.md) — error 作为节点终态
