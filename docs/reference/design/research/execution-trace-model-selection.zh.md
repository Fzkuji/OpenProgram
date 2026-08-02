# 执行记录数据模型 —— 选择 span

> 本文说明 agent 执行记录为什么采用 span 数据模型：这个模型是什么、有哪些备选、
> 为什么选它。

## 结论

agent 跑一次任务的执行记录（用户消息、LLM 调用、工具/函数调用、嵌套、循环）
采用 **span 数据模型**：id + parent_id + 起止 + attributes + status，parent_id
连成树。span 是 observability 领域十五年的共识，LLM-agent 追踪圈也已收敛到它。
现有的 `Call` + `called_by` 除了命名之外就是 span，所以要做的是按 span 规范对齐，
而不是重写。不引入重量级 OTel SDK，只对齐数据形状和属性命名（`gen_ai.*`），
保留互通能力。

## 问题

agent 系统是大模型把代码当解释器在跑：用户给任务，大模型反复调函数和工具，
函数内部又调大模型（嵌套加递归）。记录要如实反映这次跑了什么。三条要求约束了模型：

- **大模型调用是一种节点。** 不能因为"被用户触发"还是"被函数触发"分裂成两种。
- **调用是嵌套的**（父到子，有返回）；**循环是平级的**（同一父下的兄弟，不是谁调谁）。
- 同一份记录既要能画**聊天线**（时间流），又要能画**调用树**（嵌套）。

observability 早就解决过这个问题：一个请求穿过多个服务，有嵌套调用，需要追踪。
形状与 agent 的情况同构。

## 领域格局

### 这是什么领域

**observability（可观测性）**，子领域 **distributed tracing（分布式追踪）**。
三大支柱是 metrics（数字统计）、logs（日志）、**traces（追踪）**。span 住在
traces 里：一个 trace 是一棵 span 树，一个 span 是一次有起止的操作。

### 历史：先分裂，后统一

| 时间 | 事件 |
|---|---|
| 2010 | Google **Dapper** 论文定义 span |
| 2012 | Twitter 开源 Zipkin |
| 2015 | Uber 做 Jaeger；OpenTracing 标准 |
| 2018 | Google/微软推出 OpenCensus（两个标准并存竞争） |
| 2019 | 两者合并成 **OpenTelemetry（OTel）**，标准之争结束 |
| 2021 | OTel 追踪规范 v1.0 稳定 |
| 2023-24 | OTel 成为 CNCF 第二活跃项目，仅次于 Kubernetes |

这对谨慎选型有意义：这个领域已经完成过一轮淘汰，包括标准之间的竞争，活下来的是
OTel。它既不新，也不是一次押注。

### OTel 是真共识

CNCF 项目，由 **AWS、Google、微软、Datadog、Splunk、Honeycomb、Grafana、
Dynatrace** 共建。互相竞争的公司一起维护同一个标准，这是"真标准而非炒作"最强的信号。

### 备选方案 —— 基本都用 span

| 方案 | 用 span 吗 |
|---|---|
| 商业 APM（Datadog / New Relic / Honeycomb / Lightstep） | 全部使用，且原生兼容 OTel span；差别只在存储和查询 |
| Chrome Trace / Perfetto | 不同血统（浏览器与安卓性能），但也是 span 那种带时间的嵌套区间形状 |
| eBPF 追踪（Pixie / Cilium） | 不同层（内核级）；产出的也是 span，属于采集手段而非竞争模型 |
| 只用扁平日志、不要 span 树（Honeycomb 早期、Stripe） | 唯一真正不同的哲学，但主要提出者后来也转向 span |

对嵌套执行的建模，span 是全行业共识，没有第二个可信模型。

### LLM-agent 追踪圈已经收敛到 span

专做 agent 追踪的工具全部使用 span：

| 工具 | 模型 |
|---|---|
| **OTel GenAI 规范** | 官方 `gen_ai.*` 属性（模型名、token 数），并为 LLM、工具、agent-step 提供专门的 span 约定 |
| **Langfuse** | observation 树（span/generation/event），原生接收 OTel span |
| **Arize Phoenix** | 直接建在 OTel 上（OpenInference 约定） |
| **LangSmith**（LangChain） | run tree —— 嵌套 Run 带父子关系与起止，即 span 树，并支持 OTel 互通 |
| **OpenLLMetry / W&B Weave / Braintrust** | OTel span |

对同一个问题，它们给出同一个答案：一次 agent 运行是一棵 span 树。

## span 如何满足这些要求

```
span = { id, parent_id, name/kind, start, end, status, attributes, events[] }
```

| 要求 | span 如何满足 |
|---|---|
| 大模型节点保持一种形态 | span 不因"谁调它"而分裂，这是 OTel 的规则；HTTP、内部函数、后台任务都是 span，只是 kind 和 attributes 不同 |
| 调用嵌套 | `parent_id` 指向父节点，子区间套在父区间内，返回即 span 结束 |
| 循环是平级 | 同一父下的多个兄弟 span 按时间排序，兄弟之间没有父子关系，正是"循环不是调用" |
| 聊天线加调用树 | parent_id 给出树，start 时间给出时间线；同一份数据两种视图 |
| 上下文引用（reads） | 挂成 span 的 `events[]`，不另开子节点，树保持干净 |
| 异步与后台因果 | OTel 的 `links` 边（这里叫 `caused_by`），用于非严格嵌套的情况 |

## span 的缺点

1. **fan-out 开销。** 每个小操作一个 span，agent 循环一长 span 数量就很大，需要采样或聚合。
2. **树假设干净的父子结构。** 共享状态、重试、DAG 流映射到严格树上会别扭。
   `links`/`caused_by` 边解决一部分，其余是已知的粗糙之处。
3. **token、成本、评估数据不是 span 原生的。** 它们以 attributes 挂载，
   `gen_ai.*` 就是做这件事的。

## 与当前模型的距离

| 当前 | span | 差距 |
|---|---|---|
| `Call.id` | span id | 一致 |
| `called_by` | parent_id | 同一条边 |
| `role` | name/kind | 相似 |
| `output` | status + attributes | 已有 |
| `seq` | start（排序） | 大致对应 |
| `metadata.parent_id`（对话顺序，存在 metadata 里） | 兄弟按 start 排序，不需要这条边 | 多出一条应删除的边 |
| `reads`（未启用） | span events[] | 概念一致，实现待补 |

## 路线

1. **采用 span 数据模型**：id、parent_id、起止、attributes、status。
2. **属性命名向 OTel `gen_ai.*` 对齐**（模型、token、成本），保留后续导出到 OTel 的可能。
3. **不引入重量级 OTel SDK。** 只借用数据形状，内部存储自行管理，避免过早绑定 SDK。
4. 把 `Call` + `called_by` 按 span 规范对齐：删掉存在 metadata 里的对话边
   （兄弟按时间排序）、理顺 `role` 的 wire 层、把 reads 挂成 span events、
   为异步加一条 `caused_by` 边。

> 引用前需要核实，因为这块迭代较快：OTel 的 CNCF 毕业状态，以及 GenAI
> 语义约定的稳定层级。

## 与设计文档的关系

本文是选型调研，讲为什么选 span。具体的数据模型、上下文检索，以及两套调用路径
合并的设计在 [`../runtime/dag/overview.md`](../runtime/dag/overview.md)
（权威）；调用流程骨架在 `agent-call-flow.md`。
