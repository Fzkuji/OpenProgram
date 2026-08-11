# TODO：待讨论的改进项

*2026-08-01 全项目审计产出。明显 bug 已当场修掉（路径穿越、任务运行器泄漏/竞态、
超时进程误报成功、问答模式过期闭包、Finder 重复文件）；本文只收**需要讨论后再动**
的项，按影响排序。讨论定案一条就删一条。*

## 打包 / 分发

1. **provider.json 损坏被静默吞掉**：`providers/_provider_meta.py` 读
   provider.json 时静默吞异常，数据文件损坏（而非缺失）表现为
   "provider 列表为空且无报错"。
2. **pip 装完不可用 web UI 与文档站**：`web/out/` 与 `docs/_site/` 是构建产物
   且已 gitignore，运行时由仓库根目录解析路径（`parents[2]` / `parents[3]`），
   装进 site-packages 后取不到。要支持"pip 装完即可用"，需把它们改成包内资源。

## 前端

3. **`window.*` 退役（state-layer 阶段 3）**：201 处 / 16 文件。四组：
   `W.currentSessionId` 路由闸门 39 处、`window.conversations` 20 处、
   `W.isRunning` 9 处（最便宜，`runningTasks` 已覆盖）、
   `window.__sessionStore` 38 处。另有 30+ 处 `window.dispatchEvent`
   无类型字符串事件总线（阶段文档未列的第五类）。按设计文档这是阶段 2
   完成后的事，规模大，需专项排期。
4. **WS 层用无类型 CustomEvent 二次广播 store 帧**
   （`web/lib/net/use-ws.ts` 六处）：与 store 平行的第二条状态通路，
   detail 无类型。属于 window.* 退役的同族问题，可并入第 3 条专项。

## 模块规模（>1400 行且多职责，重构窗口另排）

5. 前端两个大文件已拆完（center-tab-strip 479 行、composer 967 行）。
   剩余为 Python 侧，2026-08-11 复测：
   `openprogram/agentic_programming/runtime.py`（2162）、
   `openprogram/store/session/session_store.py`（1803）、
   `openprogram/webui/server.py`（1780）、
   `openprogram/cli.py`（1780）、
   `openprogram/agentic_programming/function.py`（1557）、
   `openprogram/auth/cli.py`（1537）、
   `openprogram/functions/_runtime.py`（1502）。
   `cli/src/runtime/` 下的 yoga-layout 与 ink 运行时属 vendored 移植代码，
   不算多职责问题。
