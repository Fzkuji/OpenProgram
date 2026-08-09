# Distill（经验蒸馏）

蒸馏是把一次做成了的对话变成下次能直接跑的东西。你指一个会话——当前这个，或者过去某个——agent 把里面的经验写成一份 [skill](skills.zh.md) 或一个 [agentic function](agentic-programming/writing-functions/agentic-function.zh.md)，或者合并进先前蒸馏出的那份，下次遇到同类任务，流程已经在那里了。

不会引入新机制。蒸馏出的 skill 落在原有的 skill 目录里，由原有的加载器发现；蒸馏出的函数就是一个普通的 `@agentic_function`。

## 怎么用

自然语言说，或者用 slash command：

```
/distill
```

两种都会触发内置的 `distill` skill。会命中的常见说法还有："把这次总结成一个技能"、"存成可复用的流程"、"让这件事以后能重复做"、"把那次对话里的做法提取出来"。

**当前对话**不需要任何参数——agent 手里已经有了。

**过去的会话**用 `read_conversation` 工具读。你可以直接给会话 id，也可以描述它（"配代理那次"），让 agent 用 `list_agents` 去找。找到后它会先跟你确认再动手。

## 产出什么

agent 提取的是：目标、前置条件、步骤、决策点（当时凭什么选了这条路而不是那条）、以及路上踩的坑。然后选一种形态：

| 形态 | 什么时候写这个 | 落到哪 |
|---|---|---|
| skill（`SKILL.md`） | 执行流程需要判断——步骤要看输出长什么样再决定往哪走 | `~/.openprogram/skills/<name>/` 或 `<cwd>/skills/<name>/` |
| agentic function | 流程是机械的——输入输出固定的一串步骤 | 按 [agentic-programming](agentic-programming/README.zh.md) 的约定放 |

步骤里点到当前仓库内的路径时默认放项目级（`<cwd>/skills/`）；流程跟着你跨项目走就放用户级（`~/.openprogram/skills/`）。agent 会告诉你它选了哪个、写在哪。

如果这个会话里根本没有可复用的流程，agent 会直说，而不是硬写一个空壳 skill。

## 蒸馏出来的东西怎么用

蒸馏出的 skill 立刻生效——skill 目录是热重载的，不用重启。它以两条路径找到你：

- **模型自己加载**：你的任务和 skill 的 `description` 那一句匹配上就会加载。触发时机完全由那句话决定，所以 skill 触发得太频繁或者根本不触发，要调的就是它。
- **你直接调用**：输入 `/<name>`，因为每个被发现的 skill 都会投射进 slash command 注册表。

`openprogram skills list` 里能看到它，和其他 skill 并列。之后要改它就是改文件——格式和查找路径见 [Skills](skills.zh.md)。

## 修订既有技能

蒸馏也是蒸馏出的 skill 变好的方式。哪份 skill 用起来不对，直接说——"这个 skill 不好用，按这次的经验改一下"——agent 会修订既有文件，而不是在旁边再写一份。

匹配按主题，不按名字：既有 skill 的目标和适用条件与这次做的事重合，才算同一个。agent 读旧正文，保留经受住检验的步骤，替换这次被证伪的，把新的决策点和坑合并进流程里。正文保持干净——不留版本说明、不留变更记录；文件的历史在 git 里。函数同样处理：先前蒸馏出的 `@agentic_function` 原地修改，不复制新函数。

修订后的 skill 文件一写完就生效，和新写的一样。这就是循环里的 refine 一环：记录一次会话，用蒸馏出的 skill 重放，重放教会了什么，教训就回到文件里。

## 自己读一个会话

蒸馏用的那个工具也可以单独用。`read_conversation` 把一个会话的分支渲染成纯文本：每一轮的内容，加上这一轮发起的工具调用——参数、结果，失败的会标出来。

```
--- [2] assistant ---
Building the site first, then rsyncing.
  [call] bash -> ok
    args: {"command": "python -m tools.docs_site.build"}
    result:
      wrote 214 pages to docs/_site
  [call] bash -> FAILED
    args: {"command": "python -m tools.docs_site.checklinks"}
    result:
      2 dead links: capabilities/distill.md
```

默认读当前会话的活跃分支。读别的会话传 `session_id`（id 用 `list_agents` 找）；读旁支而不是活跃分支传 `head_id`（分支端点用 `list_agents` 找）。传`start_turn`/`end_turn`读一段轮次范围（1起、含端点，负数从尾部数，`start_turn=-10`读最后10轮）。输出太长会切在最后一个完整轮次上，并写明第一个被丢的轮号；从那里用`start_turn`接着读。

用话说就行——"把那个会话的记录给我看看"——或者让 agent 在蒸馏过程中自己去调。

## 相关

- [Skills](skills.zh.md)——`SKILL.md` 格式、五个查找来源、管理 CLI
- [Agentic Programming](agentic-programming/README.zh.md)——函数形态，适合运行时不需要判断的流程
- [Agentic workflows](workflows/README.zh.md)——预置的完整 agent 程序，和蒸馏出的流程不是一回事
