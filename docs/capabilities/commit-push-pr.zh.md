# Commit, push, PR（提交、推送、开 PR）

agent 能把做完的活从工作区一路送到可评审的 pull request：从默认分支切出话题分支、只暂存你要的改动、写提交信息、推送、再用 `gh` 开 PR。没有新机制，整条流程就是普通的 `git` 和 `gh` 走 shell，由内置的 `commit-push-pr` skill 引导。

## 怎么用

自然语言说，或者用 slash command：

```
/commit-push-pr
```

会命中的常见说法还有："提交并推送"、"给这些改动开个 PR"、"推上去"、"发个 pull request"。

## 它做什么

1. **先切分支。** 先探测仓库的默认分支（`git symbolic-ref refs/remotes/origin/HEAD`，失败退到 `git remote show origin`，没有 remote 时按 `main`/`master` 算）。如果 HEAD 就在默认分支上，先建话题分支再提交。绝不往默认分支上提交。
2. **按路径暂存。** 先读 `git status --short -uall`，只 add 这次请求涉及的文件。不对没看过的工作区做无差别 `git add -A`；你没提到的文件保持未暂存状态并如实汇报。
3. **写提交信息**用 `/commit-message`：它读暂存区 diff，返回一句祈使式标题，必要时带正文。
4. **从消息文件提交**（`git commit -F`），不用容易被引号折断的行内 `-m`。钩子一律执行，不加 `--no-verify`。pre-commit 钩子改写了文件就 amend 一次然后停下，不进入循环。
5. **推送**用 `git push -u origin <branch>`。被判 non-fast-forward 就 rebase 后重试一次。强推必须你明确要求，而且用 `--force-with-lease`。
6. **开 PR** 用 `gh pr create --base <default> --head <branch> --title ... --body-file ...`，之前先查 `gh auth status`。PR 正文由该分支的提交和相对基线的 diff 生成，分为摘要、改了什么、怎么测三节。

## AI 署名

agent 写的提交带一条 git trailer，把模型记为共同作者：

```
Co-Authored-By: Claude Opus 5 <noreply@openprogram.dev>
```

知道模型显示名就用它，否则用通用身份 `OpenProgram`。这条 trailer 是幂等的：重跑流程不会重复追加，而且遇到已有的 trailer 块（比如 `Signed-off-by:`）会并进那一块，不另起一段。

生成的 PR 正文结尾固定一行：

```
Generated with OpenProgram
```

用 `git.co_author` 设置关掉提交 trailer：

```
openprogram config set git.co_author false
```

关掉之后 OpenProgram 不再往提交里加任何署名 trailer。PR 结尾那行不受这个开关影响。

这些辅助函数可以直接 import，自己复现完全一样的字符串：

```python
from openprogram.commands.commit_message import apply_trailers, co_author_trailer, pr_body
```

## 审批与安全

每一步都是 `bash` 调用，所以 `git push` 和 `gh pr create` 走会话原有的审批档位，默认档位下你会逐条看到并批准。OpenProgram 不会为了让流程安静就把你切到绕过档位。

被 spawn 出来的子 agent 完全拿不到 `bash`，因此无法提交或推送。这条流程属于顶层会话；活派给了子 agent 的话，子 agent 把分支交回来由你来发。

## 什么时候会停

出现下列情况 agent 停下汇报，不自作主张：工作区里有它无法归因到你请求的改动、钩子反复改写文件、推送连续两次被拒、`gh` 缺失或未登录。最后一种情况下分支已经推上去了，你可以在网页上开 PR，或者跑 `gh auth login` 之后再让它来一遍。

## 相关

- [Skills](skills.zh.md)——这条流程所在的 `SKILL.md` 注册表
- [内置工具](tools.zh.md)——每一步都经过的 shell 工具
- [配置项](../reference/config-keys.md)——`git.co_author` 及其他
