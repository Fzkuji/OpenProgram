# CLI 命名规范

每个 `openprogram` 子命令都遵循相同的结构，这样用户就能从已知命令
推断出新命令。

## 规则

```
openprogram <noun> [<noun> ...] <verb> [<arg> ...]
```

- **每个命令恰好一个动词。** 它始终是位置参数之前的最后一个词。
  命令绝不能有两个动词，也绝不能把动词混进名词栈的中间。
- **名词在前，动词在后。** 额外的名词堆叠在动词前面，以收窄
  命名空间。
- **名词可以用复数。** 当命名空间表示一个集合时使用复数
  （`providers`、`profiles`、`models`、`channels`）。仅当只有
  恰好一个事物且它永远不可能有同级兄弟时才用单数（罕见）。
- **动词用一般现在时，无后缀。** `list`、`status`、`add`、
  `remove`、`login`、`logout`、`set`、`get`、`discover`、`adopt`、
  `doctor`、`setup`。不是 `listing`，不是 `lists`，不是 `added`。
- **位置参数跟在动词后面。** `openprogram providers
  auth login codex` —— `codex` 是 `login` 动词的目标。
- **标志使用双横线 kebab-case。** `--profile`、`--display-name`、
  `--max-poll-seconds`。绝不用 camelCase，绝不用下划线。

## 示例（当前与未来）

```
openprogram providers login <prov>               ✓
openprogram providers list                       ✓
openprogram providers status <prov>              ✓
openprogram providers profiles list              ✓  (nouns stack: providers > profiles)
openprogram providers profiles create <n>        ✓
openprogram providers doctor                     ✓
openprogram providers setup                      ✓  (interactive wizard)

openprogram providers models list                (future, same pattern)
openprogram providers aliases add <from> <to>    (future, nouns stack)
openprogram channels login discord               (future, same pattern in different domain)
openprogram tools login github                   (future)
```

## 何时增加一层命名空间

只有当父级名词确实需要拆分成*多个*同级子组时，才增加一个中间名词
（例如用 `providers auth login` 而非 `providers login`）。如果父级
始终只涉及一个子组，就折叠这一层 —— 没有同级兄弟的中间名词
是累赘。

例如，OpenClaw 保留 `openclaw models auth login`，因为
`models` 还有 `aliases`、`list` 以及其他同级兄弟。我们让
`providers login` 保持扁平，因为 `providers` 上的每个动词都
与 auth 相邻。

## 为什么采用这条规则

1. 可发现性 —— 输入 `openprogram providers auth <TAB>` 会列出
   该命名空间上可用的每个操作。无需翻找。
2. 可扩展性 —— 新领域可以在任意层级作为同级名词嵌入，
   不会冲突。`providers models list` 不会与
   `providers auth list` 冲突。
3. 与成熟 CLI 殊途同归的做法一致：
   - `openclaw models auth login`、`openclaw models aliases add`
   - `gh auth login`、`gh repo create`
   - `docker container ls`、`docker image prune`
   - `kubectl get pods`、`kubectl delete service <name>`

## 反模式 —— 不要这样做

- ❌ `openprogram login` —— 动词放在顶层，没有命名空间，一旦出现
  第二个登录目标就会冲突。
- ❌ `openprogram providerAuth login` —— camelCase 命名，违反
  名词栈规则（应为两个词：`providers auth`）。
- ❌ `openprogram list-providers` —— 带连字符的动名复合词，
  把动词锁死在名词里。应用 `providers list`。
- ❌ `openprogram providers listing` —— 错误的动词形式。

## 如何添加新命令

1. 选定该命令所属的最深一层名词命名空间。如果还没有，
   就创建一个 —— 但只要该命令是现有命令的同级兄弟，
   就复用现有的命名空间。
2. 选定动词。优先选用 CLI 中其他地方已用过的动词
   （`list`、`add`、`remove`、`set`、`status`），而非发明新词。
3. 将其挂接到合适的 `argparse` 子解析器树下，遵循
   同样的双文件布局：
   - 命令元数据 + argparse 接线：CLI 入口
   - 逻辑：一个专门的模块（`cli.py` 中不放逻辑）
