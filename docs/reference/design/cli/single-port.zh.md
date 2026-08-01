# 单端口架构

> 本文描述单端口运行时：一个进程、一个端口，Python worker 是唯一源站。
> 它取代的双端口拆分见 [ports.zh.md](ports.zh.md)。

## 1. 三进程带来的问题

双端口运行时是三个进程组成的脆弱依赖链：

```
Electron 壳 → Next.js 服务（Node，web 端口） → Python worker（后端端口）
```

- Next 的 `rewrites()` 在**构建时**固定 `/ws` 的代理目标。build 时环境变量
  不对，`/ws` 就指向死端口，所有客户端集体 "disconnected"。现有两个补丁
  纯粹为绕开它：`_patch_manifest_ports` 正则改写
  `.next/routes-manifest.json`（`openprogram/worker/web.py`），以及
  `/api/[...path]` route handler 每请求重读 `worker.port`。
- worker 还要拉起并看护 Next 进程：`openprogram/worker/web.py` 约 330 行的
  端口回收、孤儿 `next-server` 查杀、BUILD_ID 监视、manifest 补丁、父进程
  PID 监视（`web/scripts/with-parent-watch.mjs`）。
- 用户运行时必须装 Node，只为渲染一个本来就 100% 客户端的 UI。

## 2. 为什么合并很便宜

前端没有任何东西需要 Node 服务：

- 应用壳用 `next/dynamic` + `ssr: false` 加载
  （`web/app/(shell)/layout.tsx`），所有实际页面都是 `"use client"`。
- 没有 `middleware.ts`、server actions、`next/image`，也没有 `output`/
  `basePath`/`headers` 等需要拆的配置。
- 仅有的两个 route handler（`app/api/[...path]`、`app/files/[...path]`）
  就是转发到 worker 的代理——单端口下 worker 本身就是源站。
- worker 的 FastAPI 已经在托管静态内容（`/docs` 文档站、`/files/raw`），
  且 `docs_url=None`，没有路由冲突。

## 3. 方案

一个进程、一个端口，Python worker 托管一切：

```
Electron 壳 → Python worker（FastAPI，单端口）
                ├─ /ws            原生 WebSocket（index-0 路由）
                ├─ /api/*         原生 router
                ├─ /files/raw
                ├─ /docs/*
                └─ /*             Next 静态导出（out/）+ SPA 回退
```

### 3.1 前端是静态导出

`web/next.config.mjs`：

- `output: "export"` → `next build` 输出纯 HTML/JS/CSS 到 `web/out/`。
- 没有 `rewrites()`，也没有 `resolveBackend()`——不存在需要解析的代理目标。
- 前端只跟自己的源站说话（`/ws`、`/api/...` 相对路径）。

动态路由段（`(shell)/s/[sessionId]`、`(shell)/skills/[...name]`、
`(shell)/settings/providers/[providerId]`、`plugin/[name]/[...slug]`）都是
返回 null 或纯客户端从 `pathname` 解析参数的占位页。静态导出不带
`generateStaticParams` 会拒绝它们，因此这些 page 文件不存在；SPA 回退
（3.2）为这些路径返回壳页面，客户端路由处理其余部分。若某段真有服务端
逻辑，则保留它并加一个返回占位值的 `generateStaticParams`。

`app/api/[...path]/route.ts` 和 `app/files/[...path]/route.ts` 不存在。

### 3.2 worker 托管导出产物

`openprogram/webui/frontend.py`，在 `create_app()` 最后挂载：

- 静态文件来自 `web/out/`（`/_next/static` 加 immutable 缓存头，HTML
  no-cache）。
- SPA 回退：任何未命中文件也未命中 API 路由的 GET，返回壳 HTML
  （`out/chat.html`——应用本来就 `/` → `/chat` 重定向，其余全靠
  `pathname` 客户端解析）。
- 构建门：`web/out/` 缺失或比 `web/` 源码旧，就在启动时跑一次
  `npm run build`。Node 从此只是**构建期**依赖；打包发布版直接携带预构建的
  `out/`，运行时完全不碰 Node。

### 3.3 没有进程看护

没有任何东西拉起或监视 Node 进程。`openprogram/worker/web.py`（spawn、
端口回收、manifest 补丁、BUILD_ID 监视）、`web/scripts/with-parent-watch.mjs`
以及 `openprogram/worker/runner.py` 里的 `start_web_frontend` 调用在这里都
没有对应物。

### 3.4 端口语义

后端端口就是唯一端口，web 端口相关旋钮退役：

| | 双端口 | 单端口 |
|---|---|---|
| stable | web 18100 / 后端 18109 | **18100** |
| dev | web 18200 / 后端 18209 | **18200** |

- `OPENPROGRAM_WEB_PORT` 与 UI 偏好 `web_port`：过渡期作为后端端口的别名
  接受（打警告日志），之后移除。
- `worker.port` 文件：不变，仍是端口发现的唯一权威。
- Electron `desktop/main.js`：`WEB_PORT` 常量（dev 18200 / 发布 18100）
  现在就是 worker 端口；三处使用（启动 URL、origin 校验、导航守卫）无需
  结构性改动。
- `scripts/promote_stable.sh`：`npm run build` 输出 `out/`。

## 4. 不变量

- **后端是唯一源站。** 没有代理层、没有第二个服务、任何地方都不在构建时
  固定端口。`/ws` 目标天然正确，因为它就是页面加载来源。
- **API 路由永远优先于静态。** 前端挂载注册在最后，SPA 回退只处理没有
  任何 router 认领的路径。
- **Node 只在构建期。** 运行时依赖是 Python 加 worker。

## 5. 取舍

- 开发迭代失去 `next dev` HMR 对合并源站的直连。`npm run dev` 仍可用——
  ws/api 客户端工具在设置了 dev 专用环境变量
  （`NEXT_PUBLIC_BACKEND_ORIGIN`）时指向运行中的 worker；生产代码路径仍是
  同源相对路径。
- 真渲染内容的动态段，在其 page 文件不存在后深链会 404。由 SPA 回退兜底，
  四个段逐一核实。
- 拉取前端改动后 `out/` 可能过期。启动时的构建门（mtime 检查）覆盖；拉代码
  后 `openprogram restart` 是既定工作流。

## 6. 验收标准

1. `openprogram`（dev profile）启动后只监听一个端口；`lsof` 里没有
   `next-server`。
2. 直接刷新 `/chat`、`/s/<id>`、`/settings/providers/<id>`、
   `/skills/<name>` 全部能渲染；`/ws` 连通；`/api/pick-folder` 可用。
3. 杀掉 worker：已加载页面显示 disconnected；重启后重连。任何端口上都没有
   孤儿进程。
4. **不带**任何 profile 环境变量做一次 build，实例照样工作——构建时固定
   端口这一整类故障从结构上消失。
5. 全量测试通过；桌面应用重新打包并验证。

## 路线图定位

单端口是通向零依赖安装的三步中的第一步：

1. **单端口**（本文）：worker 托管前端，Node 降为构建期依赖。
2. **壳监管 worker**：Electron 负责 spawn、监视、重启 worker，配真实状态页，
   覆盖首启引导进度。
3. **零依赖安装（uv 引导）**：安装包携带 Electron、预构建的 `out/` 和独立的
   uv 二进制（约 15 MB）。首次启动 `uv python install` 拉取
   python-build-standalone 的独立 Python（装进应用私有目录，不碰系统
   Python），再按锁文件 `uv sync` 装依赖；之后启动复用已装环境。国内首启
   可靠性要求配置镜像（`UV_PYTHON_INSTALL_MIRROR` + 国内 PyPI 源）。
   不用 PyInstaller。

## 附录：实现状态

设计已定。路线图第 2、3 步建立在本文之上，另有各自的设计文档。
