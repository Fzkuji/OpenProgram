# 附件处理设计（Web 聊天）

综合 Claude Code / opencode / openclaw 三家做法 + OpenProgram 自身约束的统一设计。
现状基线已提交于 `c29ef3dd`（图片→vision、文档→绝对路径引用、agent 按需读）；本文是它的下一步演进。

## 一句话原则

**materialize once to a path; deliver the best block the active model accepts plus a small head preview; let the agent page the rest with its bounded tools.**

附件字节最多落盘一次，用**一个绝对路径**标识；它的内容**怎么**到达模型，每一轮按 `(文件类型 × 当前模型声明的输入模态)` 重新计算，按 `原生 block → ≤4KB 首部预览 → 路径 + agent 分页读` 逐级降级。**每个文件的 prompt 成本是 O(1)，与文件大小无关**。同一次上传在 codex/gpt-5.5 上现在就能用，将来换 PDF-native 的 Claude/Gemini 也直接生效，**前端零改动**。

三层判断（前两层是基线直觉，第三层是新增）：
1. 是不是图片？→ vision block。
2. 有没有现成的本机路径？上传/远程渠道 = 没有 → 落盘；`@`提及/打路径 = 有 → 原地引用。
3. 能力叠加层：只有当模型声明支持 `document` 时，才把 PDF 的交付升级成原生 document block。

## 决策矩阵（权威，纯文本对齐列，非 markdown 表）

`DELIVER (now)` 基于默认 codex/gpt-5.5：`model.input=["text","image"]`，无 `document`。
某一行的交付方式**只有**当 `model.input` 声明了对应模态时才会翻转。

```
来源          文件类型      落盘?                     DELIVER(现在, codex/gpt-5.5)                  READ 路径
------------  ------------  ------------------------  -------------------------------------------  ----------------------------
upload        image         否 (内存→b64 直发)        ImageContent block (像素)                    模型 vision 原生
upload        text/code     是 attachments/<safe>     [attachment:..@/abs] + ≤4KB 首部预览         read 工具 2000行/200KB 分页
upload        pdf           是 attachments/<safe>     [attachment:..(P页)@/abs] + 第1页首部+大纲   pdf 工具 80KB/页窗口
upload        其它二进制    是 attachments/<safe>     [attachment:..@/abs] 仅提及（无预览）        bash file/strings/xxd
@-mention     image         否 (重读+b64)             ImageContent block                           模型 vision 原生
@-mention     text/code     否 (已在磁盘)             [attachment:..@/abs] + ≤4KB 首部             read 分页
@-mention     pdf           否 (已在磁盘)             [attachment:..(P页)@/abs] + 第1页首部        pdf 分页
@-mention     其它二进制    否 (已在磁盘)             [attachment:..@/abs] 仅提及                  bash
打路径        任意          = @-mention               file-resolve 把裸路径按对应类型同等处理
远程渠道      image         是 attachments/<safe>     ImageContent（从落盘字节重读）               模型 vision 原生
远程渠道      text/pdf      是 attachments/<safe>     [attachment:..@/abs] + 首部预览（同 upload）  read/pdf 分页
远程渠道      其它二进制    是 attachments/<safe>     [attachment:..@/abs] 仅提及                  bash
```

**能力更强的模型上会翻转的格子（单一规则，任意来源）：**

```
pdf, model.input 含 "document", size ≤ NATIVE_DOC_INLINE_CAP(10MB 且 provider 页数上限)
    → DELIVER 变成原生 document content block（整文件 base64，从落盘路径读出来构建）；
      [attachment:..@/abs] 提及保留（驱动 chip + 让 agent 还能再读一段）；
      首部预览被抑制（模型已拿到整文件）。
pdf, 含 "document" 但 size > NATIVE_DOC_INLINE_CAP
    → 留在"现在"那列（路径 + 首部预览）；不构建原生 block（避免炸上下文）。
image, model.input 不含 "image"（退化的 codex 配置）
    → png 存盘 + [attachment:..@/abs — 用 image_analyze 查看]
      （修掉 providers/_shared/openai_responses.py:120-121 在 image 不在 model.input 时静默丢弃 input_image 的 bug）。
```

**轴的纪律**：`来源`轴只决定字节**落在哪**（落盘 vs 原地引用）；`(文件类型 × 能力)` 这一对是**唯一**决定 DELIVER 的东西。

## 与 Claude Code/opencode/openclaw 的关系

- **图片走 vision**：三家 + 我们一致。
- **PDF 原生 document block**：Claude Code/opencode/openclaw 的首选路径。OpenProgram 的能力叠加层让这条路在配置了 doc-capable 模型时**自动生效**——这就是"博采"的核心，把它们最好的那条路接进来但不强求。
- **路径 + 分页工具读**：所有人在 agent **自己任务中途**探索文件时都这么做。OpenProgram 在 codex 上把**用户附件**也走这条，是因为 codex 收不了 document block——但有了 P0 的首部预览，可靠性差距被补上。
- **落盘到管理目录**：openclaw 的 claim-check（入站只有字节没有路径）。我们用 per-session git workdir 而非全局 + TTL，更适合 agentic（就是 agent 的 cwd、每轮 git 提交、可重放）。
- **被否决的做法**：opencode/P3 的"提交时合成一个假的 read() tool_use+tool_result 把内容塞进去"——否决，因为(a)要镜像真实 read/pdf 工具的上限会漂移、(b)一旦换成原生 block 就成死重、(c)增加提交时同步延迟。改用被动的 `<attachment-preview>` 内容片段，常数成本给模型第一眼。

## 大文件保证（no-context-blowup invariant）

后端塞进 prompt 的**只可能**是：(a) 一个 image block，(b) 一次性 ≤4KB 首部预览（仅首轮），(c) 一条约 90 字节的路径提及，或 (d) 同时受"模型能力 + size≤10MB"双重门控的原生 doc block。其它一切只通过 agent 自己的**有界分页工具**逐页进上下文。

实测上限（源码核验）：`pdf` 工具 80KB 字符/次（按页 offset/limit）；`read` 工具 2000 行/次、结果上限 200KB；`file_search.py` 的 256KB 只喂预览、永不喂交付。

十个 30MB PDF 一起拖进来：那一轮约 `10×(90B 提及 + 4KB 预览) ≈ 41KB`，之后为零——**与大小无关**。500 页 PDF 在 codex 上：落盘一次，提及带"500 pages"，预览 = 第1页文本 + 每页首行大纲（截到约 50 条后"…(450 more pages)"），attach 时 prompt 成本 ≤4KB+90B，8MB 本体永不进上下文；agent 用 `pdf(offset=N,limit=20)` 窗口、靠大纲**直接跳到**相关页区，而不是顺序扫。

## 存储 / 去重 / 安全 / 生命周期

- **位置**：per-session `<state_dir>/sessions/<id>/workdir/attachments/<safe-name>`（不变）。它就是 agent 的 cwd、每轮 git 提交——附件成为会话可重放状态的一部分。全局 media store 会破坏这两个不变式。
- **谁落盘**：只有无路径来源（浏览器上传、远程渠道）。`@`提及/打路径已在磁盘，原地引用、零复制。
- **命名**：保持基线 `_safe_attach_name()`——`os.path.basename` + 非 `alnum._- 空格` 替成 `_`、120 字符上限、永不空。人类可读，让 agent 的 `./attachments/spec.pdf` 直觉成立。不用 sha 前缀名。
- **去重（新增）**：写盘前对解码字节 sha256，维护 `attachments/.opdedup.json {sha256: 相对名}`。命中则重新 stat+hash 确认同一文件后**复用**，不写重复。幂等：重复拖同一篇论文、或一轮重试，都是 no-op。**修掉基线 bug**：当前 `-N` no-clobber 循环对同名文件无字节比较，重拖相同文件会产生第二份副本。仅会话内去重（workdir 是独立 git 仓库，不做跨会话）。索引尽力而为：丢失/损坏只会多写一份（无害），绝不会错映射（复用前必校验）。
- **超限（新增）**：硬上限 `MAX_ATTACH_BYTES=32MB`/文件，在 `write_bytes` 前**和** WS intake（base64 过 socket 之前）双重检查。超限：跳过保存，提及改写成"— too large (>32MB), not stored"，**告诉模型**，绝不给死路径。图片 5MB/≤2000px（先降采样）。每轮聚合上限 64MB。注意 b64 ~1.33× 膨胀。
- **安全/逃逸**（源码核验）：上传/远程根本不带源路径（沙箱）+ basename 清洗 → 结构上无法逃逸；`@`/打路径走 `/api/file-resolve` 的 `(cwd/path).resolve()` + `is_relative_to(cwd)` → 越界 400。`.resolve()` 会完整解析符号链接，所以"根内符号链接指向根外"**已经**被拒——P1/P4 担心的 symlink gap 在现有代码里不存在。
- **GC**：附件已 git 提交，删它会破坏重放——所以 GC 是会话级懒回收：删会话 → `rm -rf workdir` 连附件一起带走。无 web 路径 TTL。会话加载时清理 dedup 索引中目标已失踪的条目。openclaw 的 2 分钟入站 TTL 只适用于将来远程渠道落盘前的 staging 区。

## 显示层

- **chip**：解析 `[attachment: name (type, KB[, P pages|L lines]) @ /abs]` → 文件名 + 类型徽章 + 大小 + **新增 scope 徽章**（"500 pages"/"200K lines"）；`@ /abs` 后缀显示时剥掉。`<attachment-preview>…</…>` 片段像提及一样从气泡里剥掉——用户看到 chip,不是 4KB 首部。
- **交付模式子标签**（UX 诚实）：从 `delivery_mode` 派生"read on demand"/"sent inline"/"previewed first N lines",让用户明确知道模型到底拿到了什么,不用猜"它看见我的文件没"。
- **乐观气泡时序**（关键）：前端**永远不知道**落盘后的绝对路径(`@/abs` 是 `_persist_doc_attachments` 在 WS 消息处理后追加的)。所以乐观气泡的 chip 必须用**客户端数据**(file.name/size/type/b64)渲染,而非提及文本;重载时再用最终存储文本的提及解析。两者必须渲染成同一个 chip,对账键 = 客户端算的 sha8。因此前端发的 `[attachment: name (type, KB)]` 是**故意无路径**的,chip 解析器要对**无路径(在途)和有路径(改写后)两种形式都渲染 chip**。
- **预览弹窗**:本地完整解码,永不发送。HUMAN 客户端滚完整文件,MODEL 只看了 4KB 首部——这就是回报。
- **侧边栏标题**:`_title_from_text` 在 50 字截断前剥掉提及(不变);新增也剥掉 `<attachment-preview>`。

## 与基线 c29ef3dd 的 diff

**基线已做（源码核验,不要重建）**:上传字节落盘到 workdir/attachments;`_safe_attach_name` 清洗 + 120 上限;`-N` no-clobber;`[attachment: name (type, KB) @ /abs]` 提及 + 后端补 @path;首轮 workdir 竞态 fallback;image→ImageContent;`@`/打路径零复制 + file-resolve 逃逸检查(含 symlink);`_title_from_text` 截断前剥提及;`user_msg["extra"]` 附件清单;文档在进 dispatcher 前从 req.attachments 剥除;validate 只查 user 消息。

**净新增**:
1. `providers/types.py` Model.input Literal 加 `"document"`。
2. `validate_modalities.py` `_MODALITY_TYPES` 加 `"document"`。
3. 新纯函数 `choose_delivery(file_kind, size, model) → "native_image"|"native_document"|"path_preview"|"path_only"`。
4. dispatcher ~1888 把 image-only 循环换成按 `choose_delivery` 的逐附件 switch。
5. `_persist_doc_attachments` 加:32MB 上限 + "too large" 改写;sha256 会话内去重;页/行数**注入到改写正则捕获的括号组里**(不是括号外追加,保持单 token 不变式 + 各 strip 正则继续匹配);一次性 `<attachment-preview>`(≤4KB 首部,先字节后行截断;二进制→bash 提示无预览);image-on-incapable-model 存盘+analyze 提示。
6. `handle_chat` intake 加 32MB/64MB 上限。
7. `handle_chat` ~307 让"进 dispatcher 前剥文档"**按能力条件化**(模型支持 document 时保留,以便下游构建原生 block)。
8. `/api/file-resolve` 返回里加页/行数 + 截断首部,让 `@`提及预览和上传预览一致(无需改 symlink)。
9. `user-attachments.tsx` 解析 count 成 scope 徽章;剥 `<attachment-preview>`;加交付模式子标签;无路径/有路径两形式都渲染 chip。
10. `use-composer-attachments.ts`/`file-tiles.tsx` 每 chip 状态/错误徽章 + "counting…"乐观占位 + "already attached"去重反馈(键=客户端 sha8);弹窗"模型预览了前 N 行"。
11. `_title_from_text` 扩展 strip 也去掉 `<attachment-preview>`。

**线上保持不变**:`[attachment: … @ /abs]` token(仅在括号内可选加 count)。codex/gpt-5.5 行为与今天逐字节相同,**外加**首部预览 + count;原生 document 分支在配置 doc-capable 模型前休眠。

## 分阶段计划

**P0 — 现在就建（对默认 codex/gpt-5.5 端到端可用，全部相对基线净新增）**
- `_persist_doc_attachments`:32MB 上限 + "too large";sha256 会话内去重;页/行数注入括号组;一次性 `<attachment-preview>`(≤4KB,先字节后行;二进制→bash 提示);image-on-incapable 存盘+analyze。
- `handle_chat`:WS intake 32MB/64MB 上限。
- `choose_delivery()` 纯函数 + 接进 dispatcher switch(native_image 不变;path_preview/path_only 为活分支;native_document 是降级到 path_preview 的守卫桩)。
- `types.py` + `validate_modalities.py` 加 `"document"`(定义接缝;codex 上无害,因为没有模型声明它)。
- `/api/file-resolve` 返回 count + 截断首部。
- 前端:scope 徽章 + 剥 `<attachment-preview>` + 每 chip 状态/错误 + "counting…"乐观 + "already attached" + 无路径/有路径 chip 一致。
- `_title_from_text` 扩展 strip。
- **价值**:中小文档免费拿到首部预览(降延迟、模型有 gist);大文件靠路径+有界工具保持 O(1) prompt 成本;超限被告知-不存储;去重修掉基线相同字节双拷;能力接缝就位。
- **验证**(按 hard self-verify 规则):重启 worker、curl healthz,再用 chrome MCP 在 fresh session 上走真实 WS:小 .txt(预览出现、chip 干净)、500 页 PDF(count 徽章、第1页大纲、不炸上下文)、超限文件("too large" chip)、`@`提及(零复制、count+预览),确认侧边栏标题干净、无 500/build overlay。

**P1 — 推迟（需配置 doc-capable 模型；接缝在 P0 已建）**
- 各 provider 的原生 document block 构建器(Anthropic document / Gemini inline_data application/pdf 线格式),挂在 `choose_delivery=="native_document"` + size 守卫后。
- 让 `chat.py:307` 的"进 dispatcher 前剥文档"按能力条件化。
- 推迟原因:今天没有配置的模型 input 含 document,无法对真实默认端到端测;且需各 provider 请求构建器 + 核实原生 size 上限。

**P2 — 推迟（需远程渠道上线）**
- openclaw 式远程入站 staging 目录 + 2 分钟 TTL GC + claim-check + `media://` 间接 + 渠道适配器(discord download_attachment / wechat)把入站字节接进同一个 `_persist` 保存调用。保存+提及+预览的表示已能容纳"只有字节"的来源,只差入站管道 + staging 生命周期。
- 同时推迟:扫描版/纯图 PDF 的页图 fallback;xlsx/docx 结构化抽取(当二进制→路径);接近 32MB 的分块/可续传上传 + WS 最大帧上调;每会话附件配额提示 UI;跨会话全局去重(故意永不做——per-session-workdir 不变式)。

## 待定（不阻塞 P0）

两个可调常量，都有可辩护的默认、都是单一配置旋钮而非架构分叉：
1. `PREVIEW_CAP`（建议 4KB / ~60 行）。太低给将将超标的小文档多一次 read 往返;太高每次 attach 多漏点正文。先 4KB,可调。
2. `MAX_ATTACH_BYTES`（建议 32MB）。压制 git workdir blob 膨胀(提交进 git 的 blob 在历史里永久,是真实成本)vs 容纳更大真实 PDF。先 32MB,可调。

唯一真正面向产品、可推迟到实际触发时再定的问题:大二进制永久累积在 per-session git 历史里(是"workdir = 自包含已提交状态"不变式的代价)是否可接受,还是将来要一个 git 之外的内容存储——但那会牺牲重放可复现性,所以现在的设计**有意保留**这个不变式,不是 P0 的待决项。
