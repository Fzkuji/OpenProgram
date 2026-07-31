# TODO：待讨论的改进项

*2026-08-01 全项目审计产出。明显 bug 已当场修掉（路径穿越、任务运行器泄漏/竞态、
超时进程误报成功、问答模式过期闭包、Finder 重复文件）；本文只收**需要讨论后再动**
的项，按影响排序。讨论定案一条就删一条。*

## 安全姿态（需要产品决策，建议优先讨论）

1. **服务绑定 `0.0.0.0` 且无认证**（`openprogram/webui/server.py:1507`）。
   所有 WS 变更操作和 HTTP 路由都暴露给局域网。选项：默认改绑
   `127.0.0.1` + 配置键开放；或保持现状但加 token 认证。改默认值可能影响
   你从其他设备访问的场景，所以没有直接改。
2. **明文取 key 接口**：`GET /api/providers/{p}/accounts/{n}/reveal`
   （`webui/routes/accounts.py:395`）返回解密后的完整 API key，无认证。
   前端"显示密钥"按钮依赖它。与上一条组合 = 局域网内可窃取全部凭据。
   若绑定改 localhost，此接口风险大幅下降；两条应一起定。

## 打包 / 分发

3. **pip wheel 缺数据文件**（`pyproject.toml:174` 无 package-data，也无
   MANIFEST.in）：25 个 `provider.json`、`claude_models.json`、静态资源全
   不进 wheel，pip 安装的用户 provider 列表为空且无报错
   （`providers/_provider_meta.py:94` 静默吞异常）。修法明确（加
   package-data + 把静默吞改成告警），但当前唯一分发方式是源码检出，
   属于"何时值得修"的问题。
4. **`requirements.txt` 与 `pyproject.toml` 手工双维护**，注释自认
   "keep in sync"。建议删掉 requirements.txt 或改为由 pyproject 生成。

## 前端

5. **`window.*` 退役（state-layer 阶段 3）**：201 处 / 16 文件。四组：
   `W.currentSessionId` 路由闸门 39 处、`window.conversations` 20 处、
   `W.isRunning` 9 处（最便宜，`runningTasks` 已覆盖）、
   `window.__sessionStore` 38 处。另有 30+ 处 `window.dispatchEvent`
   无类型字符串事件总线（阶段文档未列的第五类）。按设计文档这是阶段 2
   完成后的事，规模大，需专项排期。
6. **4 个无引用 npm 依赖**：`@heroicons/react`、`@phosphor-icons/react`、
   `next-themes`、`d3-hierarchy`（含 `@types/d3-hierarchy`；唯一"引用"是
   注释里的一句话）。可直接删，列在这里只因要跑一次完整回归确认。
7. **滚动条轮询**（`web/lib/runtime-bridge/scrollbar.ts:161`）：常驻
   `setInterval(2000)` 全文档 querySelectorAll + 永不移除的 resize
   监听。MutationObserver 可替代。
8. **WS 层用无类型 CustomEvent 二次广播 store 帧**
   （`web/lib/net/use-ws.ts` 六处）：与 store 平行的第二条状态通路，
   detail 无类型。属于 window.* 退役的同族问题，可并入第 5 条专项。

## 模块规模（>1000 行且多职责，重构窗口另排）

9. `web/components/center-tabs/center-tab-strip.tsx`（2045 行：DnD、路由
   推导、容器、复合 tab 渲染混在一起）；
   `web/components/chat/composer/index.tsx`（2007 行单函数）；
   `openprogram/agentic_programming/runtime.py`（2040）；
   `openprogram/agent/dispatcher/__init__.py`（1234，已破
   dispatcher-split.md 定的 1000 行线）；`openprogram/cli.py`（1540）；
   `openprogram/webui/server.py`（1538）。

## 清理

10. **死目录** `openprogram/webui/static/`（13 个 JS/CSS/HTML）与
    `static/_legacy_archive/`：零引用（`webui/frontend.py` 只服务
    `web/out/`）。确认无历史包袱后可整目录删除。
11. **FastAPI 弃用的 `@app.on_event`** ×8（`webui/server.py`）：迁到
    lifespan 上下文管理器，顺手事，但动 server.py 建议和第 1 条一起做。
12. **死代码**：`agent/streaming/registry.py` 的 `_persist`/`_broadcast`
    是 TODO 空壳，`open_stream` 全仓库零调用——流式状态从未持久化/推送。
    要么排进流式恢复的实施计划，要么删。
13. **过时迁移文档**：`web/MIGRATION.md`、`web/MIGRATION_PLAN.md`
    （2026-05-17 后未动）。确认迁移已完成即可删。
