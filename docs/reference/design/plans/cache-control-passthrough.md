# 改动规划：让 content block 上的 cache_control 透传到 Anthropic API

## 目标

让 OpenProgram 的调用方（如 GUI-Agent-Harness 的 screenspot 定位器）能在
`runtime.exec(content=[...])` 的某个 content block 上显式标记
`"cache_control": {"type": "ephemeral"}`，并保证这个标记原样到达 Anthropic
Messages API 请求体，从而在「调用方指定的那个 block」之后设置 prompt cache 断点。

当前问题：调用方在 content dict 里写的 `cache_control` 会在 OpenProgram 内部
被丢弃，导致无法在自定义位置打缓存断点。现在只有 provider 自动加的「最后一块」
断点生效，而最后一块（图 / 动态文本）每次请求都不同，缓存命中率为 0。

适用范围：只对 **anthropic** 一类 provider（原生 Anthropic API、Claude Code
订阅经代理、任何 anthropic-messages 接口）有效。OpenAI / codex 一类是自动前缀
缓存、不读 cache_control，本规划不涉及它们。

## 约束

- 三处改动全部是「新增可选字段 + 条件保留」，**不传 cache_control 时行为与现在
  完全一致**，对所有现有调用方零回归。
- 不要动 provider 已有的「在最后一块自动打断点」逻辑（`is_last and cache_control`
  那两段），只是额外让调用方显式标记的断点也能保留。
- cache_control 的值是一个 dict（如 `{"type": "ephemeral"}`，或带 `ttl`），原样
  透传，不在 OpenProgram 内解析或校验其内容。

## 改动清单（共 3 处）

### 改动 1 — `openprogram/providers/types.py`：数据类加可选字段

在 `TextContent` 和 `ImageContent` 两个 pydantic 模型上各加一个可选字段
`cache_control`，给缓存标记一个存放槽位。

`TextContent`（当前约 153-156 行）：
```python
class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None
    cache_control: dict | None = None        # 新增
```

`ImageContent`（当前约 173-176 行）：
```python
class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str  # base64 encoded
    mime_type: str  # e.g. "image/jpeg"
    cache_control: dict | None = None        # 新增
```

说明：只需 Text 和 Image 两类（screenspot 的固定规则前缀是 text，图是 image）。
Video/Audio 暂不需要，可不动。

### 改动 2 — `openprogram/agentic_programming/runtime.py`：`_build_pi_context` 带上字段

`_build_pi_context`（当前约 1343-1405 行）把调用方的 `content: list[dict]` 转成
`TextContent` / `ImageContent` 对象时，目前只取 text / data / mime，丢掉了 dict 里
的 `cache_control`。改成把它一起带过去。

当前（约 1388-1392 行）：
```python
        if btype == "text":
            parts.append(TextContent(type="text", text=block["text"]))
        elif btype == "image":
            data, mime = _load_media(block, _media_defaults["image"])
            parts.append(ImageContent(type="image", data=data, mime_type=mime))
```

改为：
```python
        if btype == "text":
            parts.append(TextContent(
                type="text",
                text=block["text"],
                cache_control=block.get("cache_control"),
            ))
        elif btype == "image":
            data, mime = _load_media(block, _media_defaults["image"])
            parts.append(ImageContent(
                type="image",
                data=data,
                mime_type=mime,
                cache_control=block.get("cache_control"),
            ))
```

注意：`role == "system"` 的 text block 在 1381-1386 行被单独抽成 system_text，
那条分支不涉及缓存断点（system 的断点由 anthropic provider 的 _build_system
另行处理），保持不动。

### 改动 3 — `openprogram/providers/anthropic/anthropic.py`：`_build_messages` 保留字段

`_build_messages`（当前约 304-398 行）在从 `TextContent` / `ImageContent` 重建
发给 Anthropic 的 API block 时，目前只写 type/text/source，把对象上的
`cache_control` 又丢了。改成：对象上带了就写进生成的 block。

当前（约 332-344 行，UserMessage 的 list content 分支）：
```python
                for block in msg.content:
                    if isinstance(block, TextContent):
                        text = sanitize_surrogates(block.text)
                        if text.strip():
                            content_blocks.append({"type": "text", "text": text})
                    elif isinstance(block, ImageContent):
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data,
                            },
                        })
```

改为：
```python
                for block in msg.content:
                    if isinstance(block, TextContent):
                        text = sanitize_surrogates(block.text)
                        if text.strip():
                            b: dict[str, Any] = {"type": "text", "text": text}
                            if getattr(block, "cache_control", None):
                                b["cache_control"] = block.cache_control
                            content_blocks.append(b)
                    elif isinstance(block, ImageContent):
                        b = {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type,
                                "data": block.data,
                            },
                        }
                        if getattr(block, "cache_control", None):
                            b["cache_control"] = block.cache_control
                        content_blocks.append(b)
```

保持不变：紧随其后的 `if is_last and cache_control and content_blocks:`（约 345-346
行）那段「自动给最后一块打断点」逻辑不要删。它和「调用方显式标记」可以共存：
Anthropic 允许一个请求里多个 cache_control 断点（上限 4 个）。

不需要改的同名分支：322-328 行的「content 是 str」分支不涉及调用方按 block 标记，
不动；350 行起的 AssistantMessage / 383 行起的 ToolResultMessage 不动。

## 验证（执行方做完后自检）

1. 静态：不传 cache_control 的调用，生成的请求体与改动前逐字段一致（可对一个
   已有单测的请求 body 做快照对比）。
2. 透传：构造一个 `content=[{"type":"text","text":"X","cache_control":{"type":"ephemeral"}}, ...]`
   的 exec，断言最终发往 Anthropic 的 messages[...]['content'][0] 里带有
   `"cache_control": {"type": "ephemeral"}`。
3. 命中：对同一段固定前缀连发两次真实请求（经实际使用的 Anthropic 端点 / 代理），
   第二次返回的 usage 里 `cache_read` > 0、`cache_creation` 仅第一次 > 0。
   —— 这一步同时验证「所用代理是否透传 body」：若代理丢弃了 cache_control，
   cache_read 会一直是 0，说明代理不透传，需另行处理代理层。

## 不在本规划内（由 harness 侧另做）

- 把 screenspot 各处 prompt 的固定规则段拆成「第一个 text block + 打 cache_control」，
  动态内容和图排在其后。这是调用方（GUI-Agent-Harness/screenspot_locator.py）的改动，
  不属于 OpenProgram。
- OpenAI / codex 一类的前缀缓存优化（只需 harness 前缀前置，不碰 OpenProgram）。

## 实现状态（已落地）

三处改动都已实现（commit `2f253405`），并补了单测（`tests/unit/test_cache_control_passthrough.py`，6 例）：

- `types.py`：`TextContent` / `ImageContent` 各加 `cache_control: dict | None = None`。
- `runtime._build_pi_context`：把 `block.get("cache_control")` 带到 `TextContent` /
  `ImageContent` 上。
- `anthropic._build_messages`：从对象上把 `cache_control` 写进生成的 API block。
- 自动断点的「不覆盖调用方」做得比原规划更稳：用
  `caller_marked = any("cache_control" in b for b in content_blocks)` 判断——**只要这条
  消息里任意一块被调用方标了断点，就完全不再自动给最后一块打断点**（不只是不覆盖最后一块）。
  这样调用方标在靠前的稳定前缀块时，也不会白白多占一个断点槽、也不会把缓存命中点移到动态尾块。

测试覆盖：全链路透传（runtime→anthropic body）、图片透传、不传时 body 字节级不变、
无调用方断点时自动断点照常生效、调用方标最后一块时不被覆盖、调用方标靠前块时自动断点被抑制。

### 验证结论 / 已确认的边界

- **非 Anthropic provider 零泄漏**（核实过):OpenAI/codex 的 block 构建是逐字段读
  `.text` / `.data`(`openai_completions:96`、`_shared/transform_messages`),那两处
  `model_dump()`(responses/codex)dump 的是**选项对象**不是内容块,所以新增的可选字段
  对它们是惰性的;`TextContent.model_dump()` round-trip 也干净(持久化安全)。
- **Anthropic 最小可缓存 token 数**:一个断点只有在其前缀 ≥ 1024 token(Haiku 2048)时才真的
  缓存,否则**静默忽略、不报错也不命中**。调用方(screenspot)必须保证被标的固定前缀够长,
  否则加了断点 `cache_read` 仍为 0。并进上面验证第 3 步一起看。
- **最多 4 个断点**:OpenProgram 已自动加 ~2 个(system 块 + 最后一块)。调用方自己的断点 +
  这些加起来超过 4 个,Anthropic 直接 400。调用方大约只剩 ~2 个名额。
- **代理透传**(规划已提):claude-code 订阅走 Meridian 代理时,若代理吞掉 cache_control,
  `cache_read` 永远是 0——这是代理层的事,需单独验。
