# 请求录制与回放

> 确定性循环测试需要一个每次都给同样答案的 provider。录制在共享 API provider 边界把一次真实会话写成
> JSONL 录制文件；回放离线返回其中的事件，并在后续运行发生偏离时报出具体字段。

---

## 一、请求边界

框架里每一次模型调用都走 `providers/stream.py`,它按 `model.api` 在 `api_registry` 里查出唯一一个
`ApiProvider`,调它的 `stream()` / `stream_simple()`。该注册项是请求发出、事件流入的最小共享边界,所以录制
和回放都是注册在那里的 `ApiProvider` 实现,任何厂商模块都不需要知道它们的存在。

```
stream_simple(model, context, options)
        │  get_api_provider(model.api)
        ▼
RecordingProvider ──委托──▶ 真实厂商 provider ──▶ 网络
        │ 写 recording.jsonl
        ▼
    事件原样继续往下流

ReplayProvider ──读 recording.jsonl──▶ 事件,没有厂商 provider,不开 socket
```

`RecordingProvider` 包住它替换掉的那个 provider,行为透明:先把每个事件写出去,再原样 yield 出来。
`ReplayProvider` 不包任何东西,也不持有 HTTP 客户端,由它驱动的 agent loop 到不了网络。

provider 包导入与 runtime 激活是两个独立合同。`import openprogram.providers` 只定义和导出 API，不注册厂商
provider、不读取 `record_replay` 配置、不打开录制文件、不安装 registry transform。第一次实际获取 provider
之前由 `initialize_provider_runtime()` 注册 built-ins，并读取一次进程级配置。并发首次调用者等待同一个 READY
或 FAILED 结果。recordings 管理命令直接调用文件和配置函数，不初始化 provider runtime，因此配置的 replay 文件
缺失或损坏时，`status` 和 `off` 仍可运行。

这不是 workflow 录制。Agent loop、Runtime、工具、Session 和 DAG 仍执行当前代码；回放只把真实 LLM provider
替换为录制事件来源。它不恢复历史 Session、不重放工具副作用、不回滚文件，也不定义任务步骤。

## 二、录制文件格式

JSONL,一行一个 JSON 对象,按事件发生顺序写。首行是头:

```json
{"type": "header", "format_version": 1}
```

`format_version` 是一个整数,定义在 `openprogram/providers/recording.py` 的 `RECORDING_FORMAT_VERSION`。回放按相等
比较,不等就拒绝,这样格式改动之前录的录制文件会被拒掉而不是被误读。request/event/call_end 的必需字段或
语义变化时升级版本；header 新增 reader 可忽略的可选元数据不升级版本。

其余行类型:

| `type` | 字段 | 含义 |
| --- | --- | --- |
| `request` | `call_index`、`model`、`context`、`options` | 一次 provider 调用开始;三个负载是参数脱敏后的 `model_dump(mode="json")` |
| `event` | `call_index`、`event_index`、`event` | 一个流式 `AssistantMessageEvent`,以 JSON 存;回来时用 `event.type` 选类 |
| `call_end` | `call_index`、`event_count` | 该次调用的流结束;计数让被截断的录制文件可见 |

`call_index` 在一次录制内按 provider 调用计数,`event_index` 在一次调用内按事件计数。带工具调用的多轮 agent
loop 因此产出 call 0(发工具调用)和 call 1(拿到工具结果后的续写),各有各的事件序列。

## 三、脱敏

脱敏在每个值落盘前执行,没有关闭开关。`recording.py` 的 `remove_secret_values()` 遍历 dump 出来的结构,把
敏感值替换成固定占位符 `[secret removed]`:

- **按字段名** —— 字典键命中 `SECRET_FIELD_NAMES`(大小写不敏感)的,整个值被替换。集合覆盖
  `authorization`、`proxy-authorization`、`api_key`、`x-api-key`、`x-goog-api-key`、`api-key`、`token`、
  `access_token`、`refresh_token`、`id_token`、`cookie`、`set-cookie`、`secret`、`client_secret`、
  `password`、`session_key`。遍历是递归的,嵌套的厂商专有字典同样覆盖到。
- **按值形态** —— 剩下的字符串再扫一遍 `Bearer …` 凭据、`sk-…` 密钥,以及挂在 URL 查询参数上的密钥
  (`?api_key=`、`&access_token=`、`&token=`)。这能抓住被粘进名字看不出端倪的自由文本字段的凭据。

非敏感头保留,录制文件因此仍然可读可调试。回放在比较前对进来的请求做同样的脱敏,所以脱敏过的录制文件依然能和携带
真实密钥的实时请求对上。

## 四、差异报告

`ReplayProvider` 把每个进来的请求和同一 `call_index` 的录制请求比对,在第一条差异处抛 `ReplayMismatch`。
异常带 `call_index`、`field_path`、`recorded`、`incoming`,位置而非内容偏离时还带 `event_index`:

```
replay mismatch at call 1, field context.messages[2].content[0].text:
recorded 'echo:hi', incoming 'echo:bye'
```

`find_first_difference()` 字典按键排序遍历、列表按下标遍历,同一对值永远报同一条路径。调用数超出录制文件末尾抛
同一个异常,`field_path` 为 `call_index`,并说明总共录了多少次调用。

`NON_DETERMINISTIC_FIELD_NAMES` 里的墙钟字段(目前是 `timestamp`)跳过比较:它在录制那次和每次回放之间都
不同,拿它比会让每盘录制文件在第二条消息上就报不一致。

## 五、产品入口与边界

库级构造器继续供定向测试使用：

```python
register_api_provider(api, RecordingProvider(real_provider, recording_path))  # 录制
register_api_provider(api, ReplayProvider(recording_path))                    # 回放
```

产品配置使用 `record_replay.mode=off|record|replay`，按 next-start 生效。CLI 提供
`openprogram recordings status|record|replay|off|list|show|delete|prune`。严格离线只覆盖 LLM provider 层；
tool 和其他子系统继续使用各自的网络与权限规则。凭证脱敏后，录制文件仍可能包含 prompt、模型回复、工具结果、
文件片段和个人信息。

## 实现状态

已实现：严格版本化录制/回放、共享 registry transform、next-start 配置、recordings CLI 和文件管理。
`b6460fdc` 通过临时的导入期环境变量保护管理恢复命令。

已批准但尚未实现：显式 provider runtime 生命周期、provider 包导入无运行副作用、线程安全的单次初始化、
稳定的 FAILED 传播和环境变量保护删除。规范设计与实现证据边界见
[`record-replay.html`](record-replay.html)。
