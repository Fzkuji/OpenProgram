# 三条样板规则

前面都在讲机制，这篇给三条**真规则**，从头到尾走通。它们既是第一版要做的，也是你以后写新
规则的样板——照着改就行。读这篇前先读完 `overview.md` 和 `execution-model.md`。

## 为什么先只做三条

不是因为时间不够，是**故意的**。主动打扰系统的经典死法不是"功能太少"，而是"规则太多、
误报太多，用户三天就学会无视所有提示"（Clippy、UAC 弹窗就是这么死的）。

所以这层不提供"配置文件 / DSL"让人十分钟写一条规则——规则必须是 Python 类、得过 code
review。先做三条把机制跑通、把每条都打磨到真有用，再加第四条。三条各自验证一种不同的出手
方式：一条挡路、两条旁观（其中一条还演示"先做功课再开口"）。

| 规则 | 类型 | 一句话 |
|---|---|---|
| DangerousCommandGuard | 挡路 | 危险命令执行前拦下来问用户 |
| TestGapWatcher | 旁观（先做功课） | 改了核心代码没补测试，后台核实后提醒 |
| UnvalidatedCompletionNudge | 旁观（先提醒模型） | 模型说完成了却没验证，先悄悄让模型自己去验 |

---

## 1. DangerousCommandGuard（挡路）

**做什么**：工具即将执行时，如果是危险的 shell 命令，拦下来问用户。

```python
class DangerousCommandGuard:
    on = {"tool.before"}
    lane = "gate"            # 挡路：在命令真正跑之前拦
    cooldown_s = 0

    def evaluate(self, event, state):
        if event.payload["工具"] != "bash":
            return None
        命令 = event.payload["命令"]
        if 是危险命令(命令):
            return Gate.ask(f"这条命令有风险：{命令}\n确认执行吗？")
        return None
```

**关键：判断要看到参数，不能只匹配关键词。** 否则误报会多到用户习惯性秒点确认，护栏就废了
（这正是 UAC 弹窗的死法）。要区分：

| 危险 | 不危险（别误报） |
|---|---|
| `rm -rf /` `rm -rf ~/项目` | `rm -rf /tmp/xxx`、`rm -rf node_modules`（日常操作） |
| `git push --force origin main`（推保护分支） | `git push --force` 到自己的 feature 分支（很常见） |
| `kubectl delete namespace`（删整个命名空间） | `kubectl delete pod xxx`（日常） |

所以 `是危险命令()` 要解析路径白名单、判断分支名、区分资源类型，不是 `"rm -rf" in 命令`
这么粗。

**诚实地说它能做到哪**：它防的是**手滑、误操作**，不是防恶意对手。聪明的绕过方式（把命令
base64 编码、写进脚本再执行、用别的工具代替 bash）它都拦不住。这版就把它定位成"防手滑的
护栏"，不假装它是安全边界。真要防对手得上沙箱，那是另一回事（归档的 threat-model 里）。

**和现有批准机制的关系**：OpenProgram 已经有工具批准弹窗。别搞成同一条命令弹两次——
让这条规则的判断作为"风险标注"挂到现有批准流程上，合并成一次确认。

---

## 2. TestGapWatcher（旁观 + 先做功课）

**做什么**：用户/模型表示这轮要收尾了（要提交了、说做完了），如果改了核心代码却没动测试，
提醒一下。

```python
class TestGapWatcher:
    on = {"model.response_completed"}    # 模型说完一轮话时检查
    lane = "observer"                    # 旁观：不挡路
    cooldown_s = 1800                    # 同一情况半小时内不重复提

    def evaluate(self, event, state):
        # 只在"要收尾了"的时候才检查，不是每次改文件都查
        if not 是收尾信号(event, state):
            return None
        if state.改了核心代码 and not state.动了测试:
            # 不直接提醒——先派个只读后台任务去核实
            return Prepare(任务="看看这个改动到底缺不缺测试、值不值得提醒")
        return None
```

**为什么"先做功课"（Prepare）**：一看到"改了代码没测试"就提醒，误报会很多——改个注释、
重命名、改配置、升级依赖、改前端（前端目录常常根本没测试文化）全会命中。所以不直接开口，
先起个**只读**后台小任务，让它真去看看这个改动到底缺不缺测试、缺得值不值得提。它返回一个
判断，**够有把握才 Notify**，没把握就咽回去，不打扰用户。

这条最容易变成讨人嫌的 Clippy。两个约束：
- **触发收窄**：只在"要收尾"时查（要提交、说做完），不是每次文件变动都查。
- **花销要看得见**：后台任务是要花钱的（可能调 LLM）。在界面上给个小账单——这层今天跑了
  几次后台核实、花了多少、几次最后没提醒——让用户知道它在花钱、能关掉。

**提醒里给一键操作**：Notify 不只是一句话，给个"帮我补测试"的按钮，点了就起个起草测试的任务。

---

## 3. UnvalidatedCompletionNudge（旁观 + 先提醒模型）

**做什么**：模型说"完成了"，但这一轮根本没跑过测试/没验证，那多半是没真验。

```python
class UnvalidatedCompletionNudge:
    on = {"model.response_completed"}
    lane = "observer"
    cooldown_s = 900

    def evaluate(self, event, state):
        if event.payload["声称完成"] and state.本轮有文件改动 \
                and not state.本轮有验证动作:
            # 默认不打扰用户——先悄悄提醒模型自己去验
            return Inject("你说完成了，但这一轮没有验证动作。请先验证再下结论。")
        return None
```

**为什么默认用 Inject（提醒模型）而不是 Notify（提醒用户）**：这个 repo 的规范本来就要求
模型改完自己验证。模型守规矩，这条永远不触发；模型偷懒，**更省事的办法是悄悄推模型一把**，
让它自己回去验，而不是把球踢给用户点确认。只有模型被推了还是不验，才升级成提醒用户。
这也呼应 overview 的动作表：Inject 是"给模型注入一句话，不打扰用户"。

**一个判断细节**："本轮有验证动作"得算全——跑测试算，用浏览器（MCP）实际打开页面看效果
也算（前端改动常这么验）。漏算了浏览器验证，前端改动就会被频繁误判成"没验证"。

---

## 写新规则时回头看这三条

| 你想做的规则 | 照哪条抄 |
|---|---|
| 在某动作发生前拦住 | DangerousCommandGuard（挡路、看眼前、要快） |
| 攒够条件后提醒，但怕误报 | TestGapWatcher（旁观、先 Prepare 做功课、再 Notify） |
| 想纠正模型行为、不想打扰用户 | UnvalidatedCompletionNudge（旁观、用 Inject 推模型） |

每条都是：定 `on`/`lane`/`cooldown_s` + 写 `evaluate`。不碰框架内核。
