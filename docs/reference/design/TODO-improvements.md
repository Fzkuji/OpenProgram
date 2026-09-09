# TODO：待讨论的改进项

> 跨专项分支的已完成状态、未合并实现和后续实施顺序统一记录在
> [OpenProgram implementation status and handoff](implementation-status.html)。
> 本文件只保留尚未定案、需要讨论后再排期的改进项。

本文只收当前仍需要讨论后再动的项，按影响排序。历史审计日期、一次性计数和已修复
问题不作为当前状态依据；讨论定案一条就删除对应条目。

## 打包 / 分发

1. **provider.json 损坏被静默吞掉**：`providers/_provider_meta.py` 读
   provider.json 时静默吞异常，数据文件损坏（而非缺失）表现为
   "provider 列表为空且无报错"。
## 前端

2. **`window.*` 退役（state-layer 阶段 3）**：仍有多处旧状态入口，需按 state-layer
设计逐步收敛。四组：
   `W.currentSessionId` 路由闸门 39 处、`window.conversations` 20 处、
   `W.isRunning` 9 处（最便宜，`runningTasks` 已覆盖）、
   `window.__sessionStore` 38 处。另有 30+ 处 `window.dispatchEvent`
   无类型字符串事件总线（阶段文档未列的第五类）。按设计文档这是阶段 2
   完成后的事，规模大，需专项排期。
3. **WS 层用无类型 CustomEvent 二次广播 store 帧**
   （`apps/web/lib/net/use-ws.ts` 六处）：与 store 平行的第二条状态通路，
   detail 无类型。属于 window.* 退役的同族问题，可并入第 3 条专项。

## 模块规模（>1400 行且多职责，重构窗口另排）

4. 前端两个大文件已拆完。剩余为 Python 侧的多职责模块，具体行数不在本设计文档中
   固定；排期时应以当前源码和职责边界重新测量。
   当前候选包括 `openprogram/agentic_programming/runtime.py`、
   `openprogram/store/session/session_store.py`、`openprogram/agentic_programming/function.py`、
   `openprogram/auth/cli.py` 和 `openprogram/programs/_runtime.py`。
   `apps/cli/src/runtime/` 下的 yoga-layout 与 ink 运行时属 vendored 移植代码，
   不算多职责问题。
