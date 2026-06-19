# 调研:agent 执行记录用什么数据模型 —— 选 span

类型:调研 / 路线选择(不是设计文档,不讲怎么实现,只讲"为什么走这条路 + 领域格局")
日期:2026-06-19

## 一句话结论

agent 跑一次任务的执行记录(用户消息、LLM 调用、工具/函数调用、嵌套、循环),
**采用 span 数据模型**(id + parent_id + 起止 + attributes + status,parent_id 连成树)。
这是 observability 领域 15 年的行业共识,且整个 LLM-agent 追踪圈已经收敛到它。
我们现有的 `Call` + `called_by` 本来就是半成品 span,方向对,按 span 规范理顺即可,
**不引入重量级 OTel SDK**,只对齐数据形状 + 属性命名(`gen_ai.*`),保留未来互通。

## 为什么需要调研(我们的问题)

agent 系统本质是"大模型当解释器在跑代码":用户给任务 → 大模型反复调函数/工具 →
函数内部又调大模型(嵌套 + 递归)。要把"这次到底跑了什么"记下来。纠结点:
- **大模型调用必须是一种节点**,不能因为"被用户触发"还是"被函数触发"分裂成两种。
- **调用是嵌套的**(父→子,有返回);**循环是平级的**(同父下的兄弟,不是谁调谁)。
- 既要能画**聊天线**(时间流),又要能画**调用树**(嵌套)。

这正是 observability 领域早就解决的问题——一个请求穿过多个服务,有嵌套调用,要追踪。
形状跟我们的 agent 完全同构。

## 领域格局

### 这是什么领域

**observability(可观测性)**,子领域 **distributed tracing(分布式追踪)**。
三大支柱:metrics(数字统计)/ logs(日志)/ **traces(追踪)**。span 住在 traces 里——
一个 trace = 一棵 span 树,一个 span = 一次有起止的操作。

### 历史:分裂过,然后合并成一个标准

| 时间 | 事件 |
|---|---|
| 2010 | Google **Dapper** 论文,定义 span |
| 2012 | Twitter 开源 Zipkin |
| 2015 | Uber 做 Jaeger;OpenTracing 标准 |
| 2018 | Google/微软又搞 OpenCensus(**两个标准打架**) |
| 2019 | 两者合并成 **OpenTelemetry(OTel)**,标准战结束 |
| 2021 | OTel 追踪规范 v1.0 稳定 |
| 2023-24 | OTel 成 CNCF 第二活跃项目(仅次于 Kubernetes) |

对谨慎选型者的意义:**这个领域已经洗过牌**(曾有竞争标准),活下来的是 OTel。不是新东西、不是赌。

### OTel 是不是真共识 —— 是

CNCF 项目,背后是 **AWS / Google / 微软 / Datadog / Splunk / Honeycomb / Grafana / Dynatrace** 共建。
这些本是互相竞争的商业公司,却一起维护同一个标准——这是"真标准而非炒作"最强的信号。

### 竞品 —— 基本都用 span

| 方案 | 用 span 吗 |
|---|---|
| 商业 APM(Datadog / New Relic / Honeycomb / Lightstep) | 全用,且原生兼容 OTel span;区别只在存储/查询 |
| Chrome Trace / Perfetto(Google 另一套) | 不同血统(浏览器/安卓性能),但**也是 span 那种"带时间的嵌套区间"形状** |
| eBPF 追踪(Pixie / Cilium) | 不同层(内核级);产出的也是 span,是采集手段不是竞争模型 |
| "只用扁平日志,不要 span 树"(Honeycomb 早期 / Stripe) | **唯一真正不同的哲学**,但连主推者后来都转向 span |

**对"嵌套执行"的建模,span 是全行业共识,没有第二个可信模型。**

### 决定性证据:LLM-agent 追踪圈已经收敛到 span

专做 agent 追踪的新工具,全部用 span:

| 工具 | 用什么 |
|---|---|
| **OTel GenAI 规范** | 官方 `gen_ai.*` 属性(模型名、token 数…),给 LLM/工具/agent-step 专门的 span 约定 |
| **Langfuse** | observation 树(span/generation/event),原生吃 OTel span |
| **Arize Phoenix** | 直接建在 OTel 上(OpenInference 约定) |
| **LangSmith**(LangChain) | "run tree"——嵌套 Run 带父子+起止,**就是 span 树**,加了 OTel 互通 |
| **OpenLLMetry / W&B Weave / Braintrust** | 全是 OTel span |

我们要解决的问题,它们已经给出同一个答案:**一次 agent 运行 = 一棵 span 树**。

## span 模型怎么解决我们的纠结

```
span = { id, parent_id, name/kind, start, end, status, attributes, events[] }
```

| 我们的需求 | span 怎么满足 |
|---|---|
| 大模型一种节点不变身 | span 不因"谁调它"分裂——这是 OTel 铁律,HTTP/内部函数/后台任务都是 span,只是 kind/attributes 不同 |
| 调用嵌套 | `parent_id` 指上层,子区间套在父里,返回=span 结束 |
| 循环是平级 | 同一父下的多个兄弟 span,按时间排,兄弟间无父子——正是"循环不是调用" |
| 聊天线 + 调用树 | parent_id 给树;start 时间给时间线;同一份数据两种视图 |
| 上下文引用(reads) | 挂成 span 的 `events[]`,不另开子节点、不污染树 |
| 异步/后台因果 | OTel 的 `links` 边(我们叫 `caused_by`),给非严格嵌套的情况 |

## span 的缺点(诚实记录)

1. **fan-out 开销**:每个小操作一个 span,agent 循环多了 span 会爆,需要采样/聚合。
2. **树假设干净父子**:agent 有共享状态、重试、DAG 流(非严格树)时映射别扭——靠 `links`/`caused_by` 边部分解决,是已知糙点。
3. **token/成本/评估**不是 span 原生,靠 attributes 挂(`gen_ai.*` 就是干这个)。

## 跟现状的距离 —— 很近

| 现状 | span | 差距 |
|---|---|---|
| `Call.id` | span id | 一样 |
| `called_by` | parent_id | 一样(就是它) |
| `role` | name/kind | 类似 |
| `output` | status + attributes | 有 |
| `seq` | start(排序) | 大致 |
| `metadata.parent_id`(对话顺序,藏着) | 兄弟靠 start 排,**不需要这条边** | 多了个该删的 |
| `reads`(未启用) | span events[] | 概念对,实现待补 |

## 路线建议

1. **采用 span 数据模型**(id / parent_id / start-end / attributes / status)。
2. **属性命名往 OTel `gen_ai.*` 靠**(模型、token、成本),保留未来导出到 OTel 的可能。
3. **不上重量级 OTel SDK**——只借数据形状,内部存储自管,避免过早绑死 SDK。
4. 现有 `Call` + `called_by` 按 span 规范理顺:删掉藏在 metadata 的对话边(兄弟靠时间排)、
   理顺 role 的 wire 层、reads 挂成 span events、加一条 `caused_by` 给异步。

> 待核实(引用前确认当前版本):OTel 的 CNCF 毕业状态、GenAI 语义约定的稳定层级——这块迭代快。

## 跟设计文档的关系

本文是**选型调研**(为什么走 span)。具体的数据模型 + 上下文检索 + 两套调用路径合并的实现设计,
在 `docs/design/runtime/history-node-model.md`(权威);调用流程骨架在 `llm-call-unification.md`。
