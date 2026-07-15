# 评估

这层将来要支撑一篇论文。本文是评估骨架：贡献怎么锚定才能与已发表工作区分、用什么实验、
什么 baseline、什么指标、数据集怎么发布。

## 1. 贡献重锚

原 PRL 是一份**未发表的内部设计**，"我们删掉了它的 YAML/14-intent/CapabilityManifest"
不构成贡献——审稿人无法验证一个他看不到的对照物。所以 PRL 降为附录里的设计动机，不作
baseline。贡献重新锚定在三个能与已发表工作区分的点：

| 贡献 | 是什么 | 已有工作为何没有 |
|---|---|---|
| **C1** git-as-truth 事件溯源 + replay-as-policy-test | 决策是事件流的纯 fold，回放历史 = 离线测 policy（`replay.md`） | Claude Code hooks / AgentSpec 是即时拦截，无事件溯源、无离线回放测试 |
| **C2** 框架强制打扰预算 + dismiss 熔断闭环 | 预算/熔断在框架层强制，acceptance 反馈自动回流（`execution-model.md` §5） | hooks 是开环——拦截后无反馈回路、无自我静音 |
| **C3** lazy L2 推断作为 derived event 写回 | 语义状态按需推断、结果写回事件流保证回放确定（`events-and-state.md` §4） | 别家无"推断即数据、写回即可复现"的机制 |

**双通道（同步 gate / 异步 observer）不作为贡献**——它就是 K8s admission webhook vs
controller、servlet filter vs event listener，系统会审稿人一句话驳掉 novelty。论文里把它写成
**设计决策 + 与 admission webhook 的显式类比**，价值只来自 agent 场景的具体刻画（10ms p99
实测、critical fail-closed 事故分析、gate 穿透 subagent bypass），那是 evidence 不是 claim。

## 2. Related work（必须 engage，否则一搜即中）

| 工作 | 关系 |
|---|---|
| Claude Code hooks `PreToolUse`（allow/ask/deny） | gate lane 在外部观察者眼里就是它的重实现 → gate-only baseline 必须对标它 |
| AgentSpec（arXiv 2503.18666）类 runtime enforcement | 覆盖 gate 语义；C1/C2 是其没有的 |
| Horvitz 1999 mixed-initiative + interruption-cost 文献 | 打扰预算与 acceptance 反馈的原则化前身；熔断阈值要与 expected-utility-of-interruption 对比 |
| ProactiveBench（arXiv 2410.12361） | 现成 proactive 评测，含事件→是否该介入的标注；论文必须用它/扩展它/论证不适用 |
| levels-of-automation 文献 | 对应被删的 0-8 ladder；论文需一段论证"为什么自主度阶梯不是我们的贡献面" |

## 3. 实验设计

N=1 自用两周是 anecdote 不是实验（作者既写 policy 又当被试 = Hawthorne + 循环论证；20-50 个
事件无统计意义）。换成：

1. **大规模回放**：公开 agent trajectory 语料（SWE-bench / SWE-agent / OpenHands 的数千条
   trajectories）映射到 Event schema 后回放，**≥2 名标注者**标 would-have-fired，报
   **precision + Cohen's kappa**。
2. **recall**：另行标注一组 should-have-fired 样本估 recall——would-have-fired 报告结构上
   看不见 false negative（`replay.md` §5），recall 必须独立来。
3. **真人部署**：n=8-12 开发者各 1-2 周，替代 N=1 自用。统计功效从预期事件率倒推所需
   session 数。

## 4. 必备 baseline

| baseline | 是什么 | 为什么关键 |
|---|---|---|
| **prompt-only** | 把三条 policy 意图直接写进 system prompt（"完成前若没跑测试请提醒"），零 runtime 成本 | **最致命对照**——若它达到相近 acceptance，整个 runtime layer 的存在性论证崩塌。必须证明 runtime 拦截/状态/预算带来 prompt 给不了的东西 |
| **no-proactive** | 关掉本层 | 下界 |
| **gate-only** | 只留 gate lane（≈ Claude Code hooks 重实现） | 隔离 observer/反馈闭环的增量贡献 |

不拿"原 PRL 全量管线"做 baseline——它没有实现，强行比较反坐实 straw man。

## 5. 指标

**主指标结果型**，不是 accept/dismiss：

- TestGapWatcher：触发的模块后续**真补测试率** / 后续真出 bug 率。
- UnvalidatedCompletionNudge：accept 后**真发现回归**的比例。

accept/dismiss 是混淆变量大杂烩（dismiss 混"建议错/时机错/早知道/flow 中无脑关"），**降为
辅助信号**。熔断阈值给**敏感性分析**：N∈{2,3,5} 下的静音率/漏报率曲线，并在 related work 里
说明为何选简单计数器而非 expected-utility（可辩护理由：冷启动无标定数据）。

## 6. 配套评估（回应自我批判）

我们批判 PRL"把状态推断质量当已解决模块"——不能把同一问题往下挪一层。所以：

- **L1/L2 推断独立评估**：路径前缀→touched_modules（monorepo 下易错）、claimed_completion
  等各建 100-200 条标注集，**单独报 accuracy**。
- **错误归因分解**：policy precision 差时，拆成 **state 错误 vs 决策错误**——否则无法判断是
  `evaluate` 逻辑差还是状态推断错。
- **ablation**（系统论文必备）：去熔断 / 去双通道（全同步 or 全异步）/ 去 L2 只留 L1，各掉
  多少 precision、加多少延迟。其中"observer 型检查放进同步路径导致 turn 延迟 +X 秒"是双通道
  设计决策的直接证据。
- **L2 成本核算**：每 turn ≤2 次推断的 $ / 延迟，与 prompt-only baseline 的成本对比。

## 7. 数据集发布

把"公开 trajectory 回放 + 标注集"本身做成 **dataset 贡献**：机会分类法 + ≥2 标注者 +
fire/no-fire 实例 + should-have-fired 集。发布前过脱敏 schema（`threat-model.md` §5——events
含密钥/私有代码，原始 payload 不可直接发布）。augmented 模式的 L2 缓存 + model 指纹随集发布，
使他人复现标注集而非推断过程（`replay.md` §3）。
