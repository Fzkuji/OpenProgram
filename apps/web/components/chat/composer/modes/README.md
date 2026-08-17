# Composer interaction modes

输入框（composer）任一时刻处于一种形态。除了缺省的"普通打字"，每种"输入框
变样"是这里的一个文件夹：

| mode | 文件夹 | 何时 | 触发 → 数据来源 |
|---|---|---|---|
| 填函数参数表单 | `fn-form/` | 用户点一个程序/函数 | store `fnFormFunction`（openFnForm） |
| 回答 runtime.ask | `question/` | 函数调 `runtime.ask`/`confirm` | store `pendingDecisions`（kind ask/confirm） |
| 批准一个工具 | `approval/` | permission=ask 时 gate 工具 | store `pendingDecisions`（kind approval） |

**容器怎么选**：`composer/index.tsx` 按优先级路由——系统决定（question/approval）
> 用户主动开的 fn-form > 普通打字；系统决定撞上 fn-form 会取消 fn-form
（用户主动开的丢弃无所谓）。系统决定之间按 `pendingDecisions` FIFO 队列一次
一个。

**加一种新形态**：在这里建一个文件夹（组件 + 样式），给它一个触发数据源
（store 字段或 pendingDecisions 的新 kind），在 `composer/index.tsx` 的路由里
加一支。

设计与决策：[../../../../../docs/design/ui/composer-interaction-modes.md](../../../../../docs/design/ui/composer-interaction-modes.md)。

> 历史：早先有过一个 `ComposerMode` 注册表对象接口（types.ts/index.ts），
> 但 fn-form 的提交逻辑与高度动画跟容器 ref 深度耦合，硬塞进固定接口要大改
> 已好用的代码、收益只是架构对称，故未采用——实际模式就是上面的"文件夹 +
> 容器按 kind 路由"。把 composer 的 if/else 路由升级成显式状态机 + 模式转换
> 动画是单独的探索（不阻塞功能）。
