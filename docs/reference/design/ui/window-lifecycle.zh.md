# 桌面窗口生命周期

桌面壳是一个进程、**一个主窗口**。撕下来的窗口是额外的、短命的。打开应用、点 Dock、再点一次启动，都不能再新建一个主窗口。

尺寸和最大化见 [`window-state.md`](window-state.md)。代码：`apps/desktop/window-lifecycle.js`、`apps/desktop/main.js`。

## 原来错在哪

三条路都会直接 `createWindow()`：

1. `app.whenReady()`：第一次启动。
2. `app.on("activate")`：macOS 点 Dock，启动过程中也会发。
3. `app.on("second-instance")`：第一个进程还没建完窗，用户又点了一次。

`createWindow` 是异步的：先 new `BrowserWindow`，再等 worker 地址。第一个窗口还没进 `getAllWindows()` 时，后两条以为没有窗口，再新建一个 id 也是 `main` 的窗。Map 只留最后一个。用户看到两个一样的应用窗。

开机自启的 LaunchAgent（`ai.openprogram.worker`）只保证 18100 上的 worker，不是第二个应用窗。worker 慢时，主窗口可能先闪错误页再加载，仍然是一个窗。

## 规则

| 情况 | 动作 |
|---|---|
| Ready，还没有活着的主窗口 | 建一个主窗口。 |
| Ready / activate / second-instance，已经在建 | 加入同一个创建，不再开第二个。 |
| 主窗口还在 | 复用。还原、显示、聚焦。 |
| 主窗口已经被关掉 | 再新建一个主窗口。 |
| 第二个操作系统进程 | 退出。交给第一个进程处理这次点击。 |
| 撕下来的窗口 | 不是主窗口。自己的 id。不写尺寸文件。 |

活着的主窗口是 `windows.get("main")`。只有 `ensureMainWindow()` 能创建或复用它。`createWindow({ detached: true })` 仍走撕离路径。

## 实现

`createMainWindowGate({ windows, createWindow })` 返回 `ensureMainWindow`，最多同时挂一个创建 Promise。

`registerSingleMainWindow(...)` 先拿单实例锁，再把上面三条事件接到这个入口。`onReady` 是 IPC、菜单、主题，只跑一次，然后才第一次 `ensureMainWindow()`。

测试：`apps/desktop/scripts/check-window-lifecycle.js`。

## 不在这里

worker 启动、`ui.open_browser`、打包 `.app` 都不会创建主 `BrowserWindow`。桌面壳已经是界面时，不要再打开系统浏览器去 `127.0.0.1:18100`。
