# 日常操作

这页覆盖装好之后每天会用到的操作：终端与 web 两个入口、会话的续用与管理、web 界面里的分支与回退。

## 两个入口，同一份会话

- `openprogram` — 终端聊天界面（TUI）。
- `openprogram web` — 浏览器界面，http://localhost:18100。

会话数据统一存在 `~/.openprogram/sessions/`，终端和 web 看到的是同一份历史：在终端开的会话可以在 web 侧栏找到，反过来也一样。

一次性提问不必进入界面：

```bash
openprogram --print "帮我总结这个错误信息：..."
```

后台服务的常用命令：

```bash
openprogram status      # 服务是否在跑（PID、端口、运行时长）
openprogram restart     # 改了代码或配置后重启
openprogram stop        # 停止
```

## 续用会话

```bash
openprogram sessions list          # 列出所有 agent 的所有会话
openprogram --resume <session_id>  # 在终端续上某个会话
```

session id 也可以从 web 侧栏拿到。另有 `openprogram sessions resume`，用于回答一个正在等待用户输入的会话。

## 会话与 channel 绑定

如果你配置了聊天 channel（Telegram / Discord / Slack / WeChat），可以把某个 channel 用户的消息固定路由进一个会话：

```bash
openprogram sessions attach    # 把 channel 用户的消息路由进指定会话
openprogram sessions detach    # 解除绑定，恢复默认路由
openprogram sessions aliases   # 列出所有会话与 channel 用户的绑定
```

## web 界面里的会话操作

会话历史按 git DAG 存储，分支是一等公民。悬停在任意一条消息上会出现操作按钮：

- **复制** — 复制消息内容。
- **从这里重试** — 从这条消息重新生成后续回复。
- **编辑消息** — 修改你发过的消息并重新生成。
- **分支到新会话** — 从这条消息分叉出一个新会话，原线索不受影响。
- **回退到这里** — 把会话回退到这条消息的状态。

编辑或重试之后同一位置会有多个版本，用消息旁的上一个 / 下一个版本箭头切换。顶栏的 branch 菜单用于查看和切换当前会话的分支。

右侧栏是本次会话的 DAG 视图：每个节点是一条用户消息、一次 LLM 调用或一次函数调用，视图随聊天滚动，点击节点会把对话滚动到对应的消息。涉及文件的分支底层运行在独立的 git worktree 中，不同分支上的并发操作不会争抢同一份源码树。
