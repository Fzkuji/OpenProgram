# 单端口架构

状态：设计已定，尚未实现。
关联：[ports.zh.md](ports.zh.md)（现行双端口语义——本文实现后取代之）。

## 1. 问题

运行时是三个进程组成的脆弱依赖链：

```
Electron 壳 → Next.js 服务（Node，web 端口） → Python worker（后端端口）
```

- Next 的 `rewrites()` 在**构建时**把 `/ws` 代理目标烘死。build 时环境变量
  不对，`/ws` 就指向死端口，所有客户端集体 "disconnected"。现有两个补丁
  纯粹为绕开它：`_patch_manifest_ports` 正则改写
  `.next/routes-manifest.json`（`openprogram/worker/web.py`），以及
  `/api/[...path]` route handler 每请求重读 `worker.port`。
- worker 还要拉起并看护 Next 进程：`openprogram/worker/web.py` 约 330 行的
  端口回收、孤儿 `next-server` 查杀、BUILD_ID 监视、manifest 补丁、父进程
  PID 监视（`web/scripts/with-parent-watch.mjs`）。
- 用户运行时必须装 Node，只为渲染一个本来就 100% 客户端的 UI。

## 2. 为什么合并很便宜

已对照当前代码核实：

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
                ├─ /ws            原生 WebSocket（本来就是 index-0 路由）
                ├─ /api/*         原生 router（已存在）
                ├─ /files/raw     已存在
                ├─ /docs/*        已存在
                └─ /*             Next 静态导出（out/）+ SPA 回退
```

### 3.1 前端改为静态导出

`web/next.config.mjs`：

- `output: "export"` → `next build` 输出纯 HTML/JS/CSS 到 `web/out/`。
- 整体删除 `rewrites()` 和 `resolveBackend()`——再没有可烘死的东西。
- 前端只跟自己的源站说话（`/ws`、`/api/...` 相对路径——现状已是如此）。

动态路由段（`(shell)/s/[sessionId]`、`(shell)/skills/[...name]`、
`(shell)/settings/providers/[providerId]`、`plugin/[name]/[...slug]`）都是
返回 null 或纯客户端从 `pathname` 解析参数的占位页。静态导出不带
`generateStaticParams` 会拒绝它们，所以**删掉这些 page 文件**，由 SPA
回退（3.2）为这些路径返回壳页面，客户端路由照常工作。若实现时发现某段
真有服务端逻辑，改为保留并加一个返回占位值的 `generateStaticParams`。

删除：`app/api/[...path]/route.ts`、`app/files/[...path]/route.ts`。

### 3.2 worker 托管导出产物

新模块 `openprogram/webui/frontend.py`，在 `create_app()` 最后挂载：

- 静态文件来自 `web/out/`（`/_next/static` 加 immutable 缓存头，HTML
  no-cache）。
- SPA 回退：任何未命中文件也未命中 API 路由的 GET，返回壳 HTML
  （`out/chat.html`——应用本来就 `/` → `/chat` 重定向，其余全靠
  `pathname` 客户端解析）。
- 构建门：沿用 `_ensure_built` 思路——`web/out/` 缺失或比 `web/` 源码旧
  就在启动时跑一次 `npm run build`。Node 从此只是**构建期**依赖；打包
  发布版直接携带预构建的 `out/`，运行时完全不碰 Node。

### 3.3 删除进程看护

- `openprogram/worker/web.py` 整文件（spawn、端口回收、manifest 补丁、
  BUILD_ID 监视）。
- `web/scripts/with-parent-watch.mjs`。
- `openprogram/worker/runner.py` 中的 `start_web_frontend` 调用。

### 3.4 端口语义

后端端口就是唯一端口，web 端口相关旋钮退役：

| | 之前 | 之后 |
|---|---|---|
| stable | web 18100 / 后端 18109 | **18100** |
| dev | web 18200 / 后端 18209 | **18200** |

- `OPENPROGRAM_WEB_PORT` 与 UI 偏好 `web_port`：过渡期作为后端端口的别名
  接受（打警告日志），之后移除。
- `worker.port` 文件：不变，仍是端口发现的唯一权威。
- Electron `desktop/main.js`：`WEB_PORT` 常量保留（dev 18200 / 发布
  18100）——它现在就是 worker 端口；三处使用（启动 URL、origin 校验、
  导航守卫）无需结构性改动。
- `scripts/promote_stable.sh`：`npm run build` 改为输出 `out/`，其余不变。

## 4. 不变量

- **后端是唯一源站。** 没有代理层、没有第二个服务、任何地方都不再在
  构建时烘端口。`/ws` 目标天然正确，因为它就是页面加载来源。
- **API 路由永远优先于静态。** 前端挂载注册在最后，SPA 回退只处理没有
  任何 router 认领的路径。
- **Node 只在构建期。** 运行时依赖：Python + worker。（路线图第 2 步
  Electron 监管 worker、第 3 步零依赖安装，在此基础上另出设计文档。）

## 路线图

1. **单端口**（本文）：worker 托管前端，Node 降为构建期依赖。
2. **壳监工**：Electron 负责 spawn/监视/重启 worker，配真实状态页
   （首启引导进度也走它）。
3. **零依赖安装（uv 引导）**：安装包只带 Electron + 预构建 `out/` +
   独立 uv 二进制（约 15 MB）。首次启动 `uv python install` 拉取
   python-build-standalone 的独立 Python（装进应用私有目录，不碰系统），
   再按锁文件 `uv sync` 装依赖；之后启动直接复用。必须默认配国内镜像
   （`UV_PYTHON_INSTALL_MIRROR` + 国内 PyPI 源），否则首启卡死。
   不用 PyInstaller。

## 5. 风险

- 开发迭代失去 `next dev` HMR 对合并源站的直连。缓解：保留 `npm run dev`
  可用——ws/api 客户端工具在设置了 dev 专用环境变量
  （`NEXT_PUBLIC_BACKEND_ORIGIN`）时指向运行中的 worker；生产代码路径
  仍然是同源相对路径。
- 被删的动态段若真渲染内容，其深链会 404。由 SPA 回退兜底；实现时逐一
  核实四个段。
- `git pull` 后 `out/` 过期。启动时的构建门（mtime 检查）覆盖；拉代码后
  `openprogram restart` 本来就是既定工作流。

## 6. 验收

1. `openprogram`（dev profile）启动后只监听一个端口；`lsof` 里没有
   `next-server`。
2. 直接刷新 `/chat`、`/s/<id>`、`/settings/providers/<id>`、
   `/skills/<name>` 全部能渲染；`/ws` 连通；`/api/pick-folder` 可用。
3. 杀掉 worker：已加载页面显示 disconnected；重启后重连。任何端口上都
   没有孤儿进程。
4. **不带**任何 profile 环境变量做一次 build，实例照样工作——烘死端口
   这一整类故障从结构上消失。
5. 全量测试通过；桌面应用重新打包并验证。
