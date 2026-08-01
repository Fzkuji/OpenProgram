# cache_control 透传 — 设计

> 调用方标在 content block 上的 `cache_control` 如何原样到达 Anthropic Messages
> API 请求体，使 prompt 缓存断点落在调用方指定的位置，而不是 provider 猜的位置。

## 1. 问题

像 GUI-Agent-Harness 的 screenspot 定位器这样的调用方，清楚自己 prompt 里哪一段是
稳定的：一大段固定规则，后面跟动态文本和截图。它希望缓存断点就打在这段稳定前缀之后。

没有透传时，写进 content dict 的 `cache_control` 会在 OpenProgram 内部被丢弃，
唯一生效的断点是 provider 自动加在最后一块上的那个。而最后一块是图或动态文本，
每次请求都不同，缓存永远不会命中。

适用范围：只对 **anthropic** 一类 provider 有效——原生 Anthropic API、经代理的
Claude Code 订阅、以及任何 anthropic-messages 接口。OpenAI / codex 一类用的是
自动前缀缓存，不读 `cache_control`，这个字段对它们是惰性的。

## 2. 透传路径

`cache_control` 是一个可选字段，原样穿过三层。

**内容类型**（`openprogram/providers/types.py`）。`TextContent` 与 `ImageContent`
各带一个 `cache_control: dict | None = None`。视频和音频没有——目前没有调用方标它们。
该值是一个不透明 dict，如 `{"type": "ephemeral"}`，也可能带 `ttl`。OpenProgram
不解析也不校验其内容；调用方写什么，Anthropic 就收到什么。

**上下文构建**（`openprogram/agentic_programming/runtime.py` 的
`_build_pi_context`）。把调用方的 `content: list[dict]` 转成 `TextContent` /
`ImageContent` 对象时，每块的 `cache_control` 一并复制到对象上。`role == "system"`
的 text block 是例外：它被单独抽成 `system_text`，不带逐块断点，因为 system 的断点
由 Anthropic provider 的 `_build_system` 另行安排。

**wire 构建**（`openprogram/providers/anthropic/anthropic.py` 的 `_build_messages`）。
从内容对象重建 API block 时，对象上带了 `cache_control` 就写进生成的 dict。这只涉及
`UserMessage` 的 list content 分支；字符串 content 分支、`AssistantMessage`、
`ToolResultMessage` 不存在调用方逐块标记，不受影响。

不传 `cache_control` 的调用，生成的请求体与该字段存在之前完全一致，所有现有调用方
零影响。

## 3. 自动断点与调用方断点

provider 仍然会自行在一条消息的最后一块上打断点。协调二者的规则是：

> 一条消息里只要有任意一块带了调用方标的 `cache_control`，provider 就完全不再给
> 这条消息的最后一块自动打断点。

判断方式是 `caller_marked = any("cache_control" in b for b in content_blocks)`。
这比「只是不覆盖最后一块」更彻底。当调用方把断点标在靠前的稳定前缀上时，动态尾块上
的自动断点会白占四个名额中的一个，还会把缓存边界推到每次都变的那段内容之后。
干脆抑制掉，调用方的意图就成了这条消息里唯一的断点。

## 4. 调用方必须遵守的边界

**最小可缓存前缀**。一个断点只有在它之前的内容至少 1024 token（Haiku 是 2048）时
才真的缓存。低于这个量，Anthropic **静默忽略**——不报错，也不命中。标了一段短前缀
的调用方会看到 `cache_read` 一直是 0，然后去排查一个并不存在的透传 bug。

**每请求最多 4 个断点**。OpenProgram 自己已经加了大约两个（system 块和最后一块）。
超过 4 个 Anthropic 直接返回 400。调用方大约只剩两个名额。

**代理是否透传**。claude-code 订阅走 Meridian 代理时，若代理把 body 里的
`cache_control` 剥掉，无论 OpenProgram 发出什么，`cache_read` 都是 0。这是代理层的
性质，需要单独验证；对同一段固定前缀连发两次、看第二次返回的 `cache_read` 是否 > 0，
可以把 OpenProgram 和代理一起验掉。

**其它 provider**。OpenAI 与 codex 的 block 构建是逐字段读的（`openai_completions`
和 `_shared/transform_messages` 里的 `.text` / `.data`），responses 与 codex 路径上
那两处 `model_dump()` dump 的是选项对象而非内容块，所以这个新的可选字段不会泄漏进
它们的请求体。`TextContent.model_dump()` round-trip 也是干净的，持久化不受影响。

## 5. 属于调用方而非 OpenProgram 的部分

把 prompt 拆成「固定规则做第一个 text block 并标记它」是调用方的改动——对 screenspot
而言在 `GUI-Agent-Harness/screenspot_locator.py`。OpenAI / codex 一类的前缀缓存优化
只需要调用方把稳定前缀前置，完全不用改 OpenProgram。

## 附录：实现状态

已在 `providers/types.py`、`runtime._build_pi_context`、`anthropic._build_messages`
三处实现，含 §3 的自动断点抑制规则。测试见
`tests/unit/test_cache_control_passthrough.py`（6 例）：runtime 到 Anthropic body
的全链路透传、图片透传、不传时 body 字节级不变、无调用方断点时自动断点照常生效、
调用方标最后一块时不被覆盖、调用方标靠前块时自动断点被抑制。§4 中「非 Anthropic
provider 零泄漏」这一条已通过阅读 OpenAI 与 codex 的 block 构建路径核实。代理透传是
唯一尚未实测的边界。
