# 桌面窗口状态

桌面主窗口把上次的**普通尺寸**和最大化 / 全屏 chrome 分开记忆。恢复上一次会话时，不会再创建一个四边贴齐菜单栏、Dock 或屏幕边缘的普通窗口——那就是「卡住、拖不动」这一类 bug。

相关代码：`apps/desktop/window-state.js`、`apps/desktop/main.js`、
`apps/web/app/styles/base.css`、`apps/web/components/app-shell.tsx`。
主窗口有几个、谁负责创建，见 [`window-lifecycle.md`](window-lifecycle.md)。

## 为什么贴边的普通窗口拖不动

Electron 的 `BrowserWindow` 本身可缩放。过去的持久化只把
`win.getBounds()` 的 `{x,y,width,height}` 写进
`app.getPath("userData")/window-state.json`（桌面名
`openprogram-desktop`）。用户 Zoom 或拖满工作区后退出，这些像素会按
**普通窗口**原样恢复。

在逻辑宽度 1512 的显示器上，这就是 `{x:0,y:38,width:1512,height:851}`：
四边贴齐菜单栏、Dock 和屏幕边缘，没有可抓的边。绿灯 Zoom 也没有可回退的普通尺寸。
桌面 40px tab 行曾经整条 `-webkit-app-region: drag`，把上沿和角上的缩放命中也吃掉了。

把 `resizable: true` 再设一遍、或删掉状态文件，都解决不了问题。存储模型必须把 chrome 和普通尺寸分开。

## 持久化 schema

文件是桌面 userData 目录下的 `window-state.json`。当前是 version 2。
version 1 就是旧的 `{x,y,width,height}` 对象，加载时迁移。

```json
{
  "version": 2,
  "x": 160,
  "y": 90,
  "width": 1280,
  "height": 800,
  "isMaximized": false,
  "isFullScreen": false,
  "displayId": 1,
  "displayWorkArea": { "x": 0, "y": 38, "width": 1512, "height": 851 }
}
```

| 字段 | 作用 |
|---|---|
| `x,y,width,height` | 上次**普通**尺寸。绝不是铺满工作区的矩形。 |
| `isMaximized` | 上次 Zoom / 最大化 chrome。 |
| `isFullScreen` | 上次 macOS 原生全屏 chrome。 |
| `displayId` | Electron 显示器 id，屏幕仍在时优先用它。 |
| `displayWorkArea` | 工作区矩形；重连后 `id` 变了也能对上。 |

最小可用尺寸是 `800×500`，不设最大尺寸。首次启动默认仍是
`1440×900`，在主屏工作区居中；若这个默认本身会铺满工作区，再向内缩一圈。

## 恢复算法

1. 读文件并迁移。JSON 损坏、缺宽高、`NaN` 或邮票大小，都落到居中默认。
2. 解析仍连接的显示器：先匹配 `displayId`，再匹配工作区（2px 容差），
   再走旧的「尺寸仍与某块工作区重叠」检查。都不匹配——外接屏拔掉——就用主屏工作区上的居中默认，并丢掉最大化 / 全屏标记，保证窗口可用。
3. 若保存的矩形铺满了仍连接的工作区（旧文件），按最大化处理，普通尺寸改成内缩默认。`BrowserWindow` 从不会按工作区大小去构造。
4. 把普通矩形钳到所选工作区里。
5. 按普通尺寸创建窗口，并设置 `minWidth` / `minHeight`。若当时存了对应标记，再调用 `maximize()` / `setFullScreen(true)`。

因此绿灯 Zoom 会回到记住的普通尺寸，而不是贴边矩形。

## 最大化 vs 拖满工作区

用户没用 Zoom、只是把窗口拉满工作区时，会话按最大化来存：

- 写入 `isMaximized`。
- 保留上一次**未铺满**的普通尺寸。Electron `getNormalBounds()` 本身未铺满时用它；否则用内存里的 last-good，再否则用内缩默认。
- 恢复时以最大化打开。Zoom 回到那个更小的普通尺寸。

矩形与显示器工作区相差不超过 8 CSS 像素即视为「铺满」。保存和恢复都执行这条规则，旧的 `{x:0,y:38,width:1512,height:851}` 不会再以贴边普通窗口回来。

## 保存时机

主窗口在 `resize` / `move` / `maximize` / `unmaximize` /
`enter-full-screen` / `leave-full-screen` / `close` 时写入。移动和缩放
debounce 300ms，避免拖动中狂写。chrome 事件和关闭立即落盘。

窗口处于最大化、全屏或铺满工作区时，只更新标记和显示器身份，不覆盖上次可用的普通矩形。

## 显示器回退

保存的显示器消失，或尺寸在屏外、为 `NaN`、小于最小值时，在主屏工作区打开居中默认。若 `1440×900` 会铺满该工作区，再内缩，保证回退窗口始终抓得到边。

## 撕下的窗口

撕下的窗口保持一次性：尺寸克制（`min(1100, 普通宽)` × `min(720, 普通高)`），不继承父窗口的 `x/y`，也不继承最大化或全屏。落点仍由 `centerHiddenWindowOnCursor` 决定。关闭撕下窗口不会写 `window-state.json`。

## 标题栏命中

40px 桌面 tab 行仍是 `-webkit-app-region: drag`，空白处可以拖窗口。上 / 左 / 右各留 5px `no-drag` 条（角上自然重叠），让 macOS 能命中原生缩放。红绿灯从 `{x:18,y:13}` 起；tab 距顶 6px。这条内缩不会盖住红绿灯、tab、`+` 或主菜单钮（后两者本来就是 `no-drag`）。

## 如何挡住「拖不动」这一类 bug

| 失败 | 防护 |
|---|---|
| Zoom 或拖满后退出 | 先按更小的普通尺寸创建，再套回 chrome。 |
| 旧的贴边 `{x,y,width,height}` | 迁成最大化 + 内缩默认。 |
| 拔掉外接屏 | 主屏工作区居中默认。 |
| 损坏 / 过小的文件 | 最小尺寸 + 居中默认。 |
| tab 行吞掉上沿 | 5px `no-drag` 内缩。 |
| 关闭撕下窗口 | 撕下窗口不持久化。 |

## 实现状态

已实现：schema v2、旧文件迁移、先恢复普通尺寸再套 chrome、铺满工作区按最大化处理、显示器回退、主窗口 debounce 持久化、撕下窗口一次性尺寸、标题栏缩放内缩，以及 `apps/desktop/scripts/check-window-state.js` 测试。

桌面窗口底色跟随已解析的 Web 主题（`theme-chrome.js`），规则写在 [unification-work.md](unification-work.md)。窗口几何持久化不保存、不恢复背景色。
