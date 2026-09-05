# Web UI

工具审批行为及运行中切换请参见[工具权限模式](../capabilities/permissions.zh.md)。

浏览器界面，覆盖 OpenProgram 的全部日常操作：聊天、管理函数与程序、配置 provider 和 MCP、查看记忆与项目。本页按路由逐个说明每个页面的用途，并详细介绍聊天页。

启动：

```bash
openprogram web
```

浏览器打开 `http://localhost:18100`。页面是由本地 FastAPI worker 直接提供的静态导出——`/api`、`/ws` 和 UI 都在同一个端口（默认 18100）。数据全部来自 worker，会话与终端 TUI、CLI 单发共用，见[界面总览](README.md)。改端口用 `openprogram ports --port`。

![聊天页](../images/chat_hero.png)

## 聊天页（/chat、/s/&lt;session-id&gt;）

`/chat` 是聊天主界面；`/s/<session-id>` 是单个会话的直达链接，切换会话不重新加载页面，WebSocket 连接保持不断。

### 消息流式

回复通过 WebSocket 流式到达：发送后立即出现占位回复，text、thinking、工具调用等块按到达顺序增量渲染。多个 agent 写入同一会话时，每条回复消息都带有产出它的 agent 的头像和名字。

### 停止与重新连接

点击输入框中的 **Cancel execution** 停止当前执行。刷新或重新打开会话时，会恢复正在执行的任务及其停止控制，包括审批等待后恢复的执行。没有新输出不代表执行已经结束；系统不会仅因一段时间没有输出就移除运行状态。已经结束的执行不会因为残留的线程注册继续显示为运行中。

### thinking 折叠

模型的思考过程渲染为可折叠块，默认收起，流式期间只显示最新一行，点击展开完整内容。

### 函数调用时间线

每轮回复中的函数 / 工具调用渲染成一条可展开的执行时间线：每步一行，函数调用带参数、输出、错误和耗时，嵌套调用按上下文树递归展示，子 agent 也是时间线中的一步。点击某一步会在右侧栏打开执行详情面板。从 `/programs` 页 Run 对话框手动运行的函数也用同一套时间线渲染。

### 附件

把图片或文本文件拖放到输入框上（粘贴同样可行），会随下一条消息一起发送。

### 会话分支与 DAG 视图

会话历史按 DAG 存储，不是扁平列表：

- 顶栏的分支菜单列出当前会话的所有分支，可切换（checkout）、重命名、删除。
- 右侧栏的 History 视图是会话的实时 mini-DAG：每条消息或函数调用一个节点，按分支着色，merge 和 attach 操作也以节点形式出现。单击节点折叠 / 展开其子树（或把聊天滚动到该步），双击节点或边即切换（checkout）到那条分支。
- mini-DAG 上方的 Branches 面板列出各分支，运行中的分支带运行标记；支持多选合并——平等合并出新分支尖端，或就地合并进选定的基准分支——也支持把另一个会话的分支挂接进来（跨会话 attach）。
- 同一条消息的多个版本用 `< N/M >` 切换器切换，只移动显示位置，不删除历史。

### 回滚

每条消息的操作菜单里有 "Rewind to here"：把会话真正回退到该消息处，被撤销的用户输入会预填回输入框，可修改后重发。输入框斜杠命令 `/rewind` 是同一功能。

## 其他页面

| 路由 | 用途 |
|---|---|
| `/chats` | History 枢纽：会话列表（`/history` 同页）；Projects 和 Memory 是同一页上的 tab |
| `/programs` | Abilities 枢纽：Programs 目录（调用树 / 图）。Plugins、Skills、MCP 是旁边的 tab |
| `/skills` | Abilities → Skills：浏览已装 SKILL.md、发现和新建；每个 skill 有详情页 |
| `/plugins` | Abilities → Plugins：已安装 / 市场 / 错误 |
| `/mcp` | Abilities → MCP：从目录添加、编辑配置、查看状态 |
| `/memory` | History → Memory：wiki、journal 和核心记忆 |
| `/projects` | History → Projects：权限规则、默认设置、关联会话 |
| `/settings` | 设置：providers（模型与凭据）、search、general（含主题、运行版本，以及存在 Electron bridge 时的 Desktop 更新状态）、system、usage、auth、channels |

`/settings` 直接打开会跳到 `/settings/general`。模型凭据仍在 `/settings/providers`，见[配置模型](../models/README.md)。
