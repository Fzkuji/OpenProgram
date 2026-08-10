# TODO：待讨论的改进项

*2026-08-01 全项目审计产出。明显 bug 已当场修掉（路径穿越、任务运行器泄漏/竞态、
超时进程误报成功、问答模式过期闭包、Finder 重复文件）；本文只收**需要讨论后再动**
的项，按影响排序。讨论定案一条就删一条。*

## 安全姿态（需要产品决策，建议优先讨论）

1. **无调用方认证**。默认绑定已是 `127.0.0.1`（`webui/server.py:1302`），
   剩余缺陷：显式设置 `web.host` 对外绑定时，HTTP/WS/SSE 仍无 token 认证。
   方案已定稿（实例 token + fragment bootstrap cookie，见
   remote-web-access 设计文档），随该文档实施后删除本条。
2. **明文取 key 接口 ×2**：`GET /api/providers/{p}/accounts/{n}/reveal`
   （`webui/routes/accounts.py:396`）与 `GET /api/config/key/{env_var}?reveal=1`
   （`webui/routes/providers.py:254`）。已定稿为整体删除（密钥仅录入时可见，
   之后只显示掩码），前端 provider detail / API key 设置 / account manager
   随之改造。随 remote-web-access 实施后删除本条。

## 打包 / 分发

3. ~~**pip wheel 缺数据文件**~~ —— 已修。`pyproject.toml` 现有
   `[tool.setuptools.package-data]`，49 个运行时数据文件（27 个
   `provider.json`、2 个 `claude_models.json`、2 个 bundled SKILL.md、
   webui 静态资源与 `*_meta.json`）进 wheel；`packages.find` 排除
   `openprogram.functions.agentics.*`（各 harness 是独立仓库，由
   `openprogram programs install` 拉取）。干净 venv 装 wheel 后
   `openprogram --help` / `web --help` 正常，22 个 provider 可见。

   遗留：`providers/_provider_meta.py` 读 provider.json 时仍静默吞异常，
   数据文件损坏（而非缺失）依然表现为"provider 列表为空且无报错"。
   与打包无关，属独立的错误处理问题。

   不打包的两项，均为构建产物且已 gitignore：`web/out/`（Next.js 导出，
   `webui/frontend.py:ensure_frontend_built` 在缺失时用 `npx next build`
   重建）、`docs/_site/`（文档站，`webui/routes/docs.py` 按 mtime 重建）。
   两者都由仓库根目录解析路径（`parents[2]` / `parents[3]`），装进
   site-packages 后取不到——要支持"pip 装完即可用 web UI"，需先把它们
   改成包内资源。属独立课题。
4. ~~**`requirements.txt` 与 `pyproject.toml` 手工双维护**~~ —— 已修。
   `requirements.txt` 改成一行 `-e .`（外加说明注释），pyproject 成为
   依赖的唯一真相源。全仓无任何 README / 文档 / CI 引用被破坏。

## 前端

5. **`window.*` 退役（state-layer 阶段 3）**：201 处 / 16 文件。四组：
   `W.currentSessionId` 路由闸门 39 处、`window.conversations` 20 处、
   `W.isRunning` 9 处（最便宜，`runningTasks` 已覆盖）、
   `window.__sessionStore` 38 处。另有 30+ 处 `window.dispatchEvent`
   无类型字符串事件总线（阶段文档未列的第五类）。按设计文档这是阶段 2
   完成后的事，规模大，需专项排期。
6. **WS 层用无类型 CustomEvent 二次广播 store 帧**
   （`web/lib/net/use-ws.ts` 六处）：与 store 平行的第二条状态通路，
   detail 无类型。属于 window.* 退役的同族问题，可并入第 5 条专项。

## 模块规模（>1000 行且多职责，重构窗口另排）

7. `web/components/center-tabs/center-tab-strip.tsx`（2045 行：DnD、路由
   推导、容器、复合 tab 渲染混在一起）；
   `web/components/chat/composer/index.tsx`（2007 行单函数）；
   `openprogram/agentic_programming/runtime.py`（2040）；
   `openprogram/cli.py`（1540）；
   `openprogram/webui/server.py`（1538）。

## 清理

8. ~~**FastAPI 弃用的 `@app.on_event`**~~ —— 已完成。`create_app()` 已用
   `_lifespan` 上下文管理器（`webui/server.py:1448`），`@app.on_event` 零残留。
