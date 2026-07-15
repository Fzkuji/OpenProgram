# Web UI

浏览器界面，覆盖 OpenProgram 的全部日常操作：聊天、管理函数与程序、配置 provider 和 MCP、查看记忆与项目。本页按路由逐个说明每个页面的用途，并详细介绍聊天页。

启动：

```bash
openprogram web
```

浏览器打开 `http://localhost:18100`。前端是 Next.js（默认 18100 端口），它把 `/api` 和 `/ws` 代理到本地 FastAPI 后端（默认 18109 端口）。数据全部来自后端，会话与终端 TUI、CLI 单发共用，见[界面总览](README.md)。改端口用 `openprogram ports --frontend / --backend`。

![聊天页](../images/chat_hero.png)

## 聊天页（/chat、/s/&lt;session-id&gt;）

`/chat` 是聊天主界面；`/s/<session-id>` 是单个会话的直达链接，切换会话不重新加载页面，WebSocket 连接保持不断。

### 消息流式

回复通过 WebSocket 流式到达：发送后立即出现占位回复，text、thinking、工具调用等块按到达顺序增量渲染。

### thinking 折叠

模型的思考过程渲染为可折叠块，默认收起，流式期间只显示最新一行，点击展开完整内容。

### 函数调用时间线

每轮回复中的函数 / 工具调用渲染成一条可展开的执行时间线：每步一行，函数调用带参数、输出、错误和耗时，嵌套调用按上下文树递归展示，子 agent 也是时间线中的一步。点击某一步会在右侧栏打开执行详情面板。手动 `/run` 运行函数也用同一套时间线渲染。

### 会话分支与 DAG 视图

会话历史按 DAG 存储，不是扁平列表：

- 顶栏的分支菜单列出当前会话的所有分支，可切换（checkout）、重命名、删除。
- 右侧栏的 Branches 面板提供 DAG 视图：分支按泳道着色，正在运行的分支有动画标记；支持多选合并（merge）和跨会话挂接（attach）。
- 同一条消息的多个版本用 `< N/M >` 切换器切换，只移动显示位置，不删除历史。

### 回滚

每条消息的操作菜单里有 "Rewind to here"：把会话真正回退到该消息处，被撤销的用户输入会预填回输入框，可修改后重发。输入框斜杠命令 `/rewind` 是同一功能。

## 其他页面

| 路由 | 用途 |
|---|---|
| `/chats` | 历史会话列表：搜索、按时间和渠道过滤、新建会话 |
| `/functions` | 函数目录：收藏、自定义文件夹（拖拽整理）、搜索排序、网格 / 列表视图 |
| `/programs` | agentic 程序目录：带自己界面的 LLM 程序，直接启动运行 |
| `/skills` | SKILL.md 管理：浏览已装 skill、发现新 skill、新建 skill；每个 skill 有详情页 |
| `/plugins` | 插件管理：已安装 / 市场 / 错误三个标签页 |
| `/mcp` | MCP server 管理：从目录添加、编辑配置、查看每个 server 的状态 |
| `/memory` | 持久记忆：wiki、journal 和核心记忆的浏览与编辑，支持 markdown 和 wikilink |
| `/projects` | 项目管理：每个项目的权限规则、默认设置、关联会话 |
| `/settings` | 设置：providers（模型与凭据）、search、general（含明暗主题）、system、usage、auth、channels |

`/settings` 直接打开会跳到 `/settings/providers`，模型配置入口见[配置模型](../models/README.md)。
