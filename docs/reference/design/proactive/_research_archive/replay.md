# 回放即 policy 测试

## 1. 为什么回放是测试框架

policy 的 `evaluate(event, state)` 是纯函数；`state` 是 `events.jsonl` 的纯 fold
（见 `events-and-state.md` §4）。两个输入都来自落盘的事件流，所以**把历史事件流喂回去重放
一条 policy，就等于在真实负载上测试它**——不需要造 mock，不需要跑活的 agent。

```
openprogram proactive replay --policy TestGapWatcher --sessions <ids|all>
```

两个用途，互为表里：

- **工程**：任何新 policy 启用前**必跑**，看它在历史会话上 would-have-fired 多少次、命中
  哪些事件。把"先发明再上线才发现 precision 不行"（Clippy 的死法）提前到离线。
- **论文**：would-have-fired 报告是 precision 人工标注的样本来源（见 `evaluation.md`）。

回放工具应在决策引擎**之前**就位——评估优先于功能（`overview.md` §5）。

## 2. 确定性难题：新 policy 首次回放历史

回放的卖点是"确定性 = 可测"。但有一个场景它天然不成立，必须诚实处理：

> 新 policy 依赖的 L2 推断，在历史会话里**从未发生过**——`state.inferred` derived event
> 不在那段历史的 `events.jsonl` 里（`events-and-state.md` §4）。

对"已经跑过的"policy 回放是确定的（直接读历史里的 derived event）。但回放工具最核心的用例
恰恰是"新 policy 首次跑历史"，这时 L2 不存在。两种诚实的应对，做成两个模式：

| 模式 | 允许的 state | 确定性 | 用途 |
|---|---|---|---|
| **strict** | 仅 L0/L1 + 已落盘 `state.inferred` | 完全确定，逐位可复现 | L0/L1 policy 的回归测试；论文里可复现的硬数字 |
| **augmented** | strict + **现场补算 L2** | 见 §3 | 依赖 L2 的新 policy（如 UnvalidatedCompletionNudge）首次评估 |

MVP 三条里 DangerousCommandGuard、TestGapWatcher 的 L1 部分走 strict；
UnvalidatedCompletionNudge 和 TestGapWatcher 的收尾信号判定走 augmented。

## 3. augmented 模式的可复现性

现场补 L2 = 现场调 LLM，天然非确定（采样、模型版本漂移）。把它约束到"可复现的非确定"：

- 固定 `model + version + temperature=0`。
- 补出的 L2 推断**缓存落盘**，并把 `model 指纹`随报告一起发布。
- 复现语义不是"复现推断过程"，而是"**复现你的标注集**"——别人拿你发布的缓存 + 指纹，得到
  和你完全相同的 L2 值，从而复现你报告的 precision 数字，无需自己再调一次会漂移的模型。
- 同一 policy 对同一会话**二次回放确定**（第二次读缓存）。

Prepare 类 policy 的额外边界：回放**只能到 would-have-prepared**——无法确定性地重跑 reviewer
subagent。所以 TestGapWatcher 真正用户可见的那一步（prepared 置信度过阈值才 Notify）**回放
验证不到**，其 Notify 精度必须靠在线 A/B，报告里明确标注"Notify precision: online-only"。

## 4. 三个回放必须做对的细节

**冷却闭环**：新 policy 回放历史时没有自己的动作历史，`cooldown` 记录（来自"动作已发生"的
事件）对它是空的。若忽略自身冷却，则每次命中都报，would-have-fired 数被稀释、precision 失真。
解法：回放引擎把自己模拟出的 would-fire 决策**回灌为虚拟 cooldown 事件**，让后续命中按真实
上线时的冷却行为被压制。

**时钟注入**：`cooldown_s`、15 分钟冷却、任务段预算这类时间逻辑，回放时的"现在"必须是**事件
的 `ts`**，不是 wall-clock。所以 policy **禁止触碰 `time.time()`**——框架向 `evaluate` 注入
一个由当前事件 `ts` 驱动的时钟。否则在 2024 年的历史会话上用 2026 年的"现在"算冷却，全错。

**分支折叠**：session 是 git DAG，可 rewind/分叉，而 `events.jsonl` 是线性文件。回放必须按
`node_id` 沿"当前节点到根"的 DAG 路径**重建**后折叠（`events-and-state.md` §5），不是按文件
顺序。否则把多个被 rewind 掉的分支当成一条连续历史，state 失真、would-have-fired 报告作废。
例外：cooldown/熔断这类打扰预算声明为跨分支全局。

## 5. recall 的结构性盲点

would-have-fired 报告只列"policy 会触发的地方"——它**结构上看不见 false negative**（该触发
却没触发的地方）。所以 precision 能从回放直接读，**recall 不能**。recall 必须靠另外标注一组
should-have-fired 样本（人工在历史会话里标"这里本该提醒"），见 `evaluation.md`。回放报告自身
不声称 recall。

## 6. 报告输出结构

```
replay-report {
  policy: "TestGapWatcher"
  mode: "augmented"                      # strict | augmented
  model_fingerprint: "claude-…@2026-06"  # augmented 才有
  sessions_scanned: 214
  events_scanned: 51_320
  fired: 38
  per_session: [
    { session_id, node_path: [...],      # 折叠用的 DAG 路径
      fires: [
        { event_ref, state_snapshot_ref, # 可点开查看触发上下文（脱敏后，见 threat-model.md）
          action: "Prepare→Notify",
          cooldown_suppressed: false,    # 冷却闭环是否压制了它
          notes: "Notify precision: online-only" }
      ] }
  ]
  invariant_checks: { loop_free: pass, breaker_exempt: pass, ... }   # 见 invariants.md §5
}
```

每个 fire 都带 `event_ref` + `state_snapshot_ref`，标注者据此判 would-have-fired 是否正确
（precision 标注）。报告同时把四条不变式（`invariants.md`）作为断言一并跑过——回放既测 policy
质量，也守框架不变式。
