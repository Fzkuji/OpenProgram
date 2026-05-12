# pdf_figures

从学术 PDF 里抽出每一张 figure，带 VLM 自校验 + 自动重抽。调用方一行进，返回每张图最终的 PNG 路径，不需要人工检查或调参。

## 文件构成

```
programs/applications/pdf_figures/
├── README.md     ← 本文档
├── SKILL.md      ← agent 发现用的 skill 描述
├── __init__.py   ← 公共入口 re-export
├── main.py       ← extract_pdf_figures(@agentic_function) 主入口
└── _heuristic.py ← 私有：纯 pymupdf 的启发式抽取层
```

`_heuristic.py` 是 application 的内部实现，不对外承诺接口。`main.py` 才是公共 API。

## 整体运作逻辑

两层架构：

```
            ┌─────────────────────────────────────────────┐
            │  extract_pdf_figures(@agentic_function)     │
            │     ↓                                       │
            │  1. 调 _heuristic.extract_all_figures       │
            │     ↓                                       │
            │  2. 每张候选 crop → VLM 审 + 修正循环       │
            │     ↓                                       │
            │  3. 返回 list[dict]                         │
            └─────────────────────────────────────────────┘
```

### Step 0 — 入口

```python
extract_pdf_figures(
    pdf_path: str,
    out_dir: str,
    max_retries: int = 3,
    include_tables: bool = False,
    filename_template: str = "fig{number:02d}.png",
    dpi: int = 300,
    runtime: Runtime = None,  # 框架自动注入
) -> list[dict]
```

`runtime` 必须是 VLM-capable provider（Gemini 2.5 Pro / Claude Sonnet / GPT-4o 等）。
`max_retries` 是每张图最多走几轮 VLM 修正。

### Step 1 — 启发式首轮抽取

调 `_heuristic.extract_all_figures(pdf_path, out_dir)`：

1. **扫描 caption**：遍历所有页面、`page.get_text("blocks")` 拿文本块，用正则 `^\s*(Figure|Fig\.|Fig)\s+\d+[:.|]` 找所有 figure caption。如果 `include_tables=True` 也匹配 `Table|Tab\.`。结果是一个 `[CaptionRef]` 列表，含每张图的 `(kind, number, label, prefix, page, caption_bbox)`。

2. **每个 caption 计算 figure bbox**：
   - **找上下边界**：扫同页其他 `Figure N:` caption，分别拿作 `prev_fig_y_max` / `next_fig_y_min`，保证不与邻居 figure 重叠。
   - **找上方 body 块**：从 caption 往上走 text blocks，第一个满足（a）跟 caption 列重叠（b）距 caption ≥ 95pt（c）"看起来像 body 或章节标题"（≥30 字符 + 句末标点 + 占列宽 ≥55%，或 2-12 token 大写标题 + 占列宽 ≥40%）的块就是上边界。跳过 sub-figure caption `(a) ...`、in-figure 标签、narrow legend。
   - **吸收段落续行**：找到 body 块后再往下扫，把同段被 PyMuPDF 切碎的续行合进来（gap ≤8pt、含字母字符、不是 sub-cap）。
   - **fig_top 兜底**：上方找不到 body 块时（figure 在页首），用 caption 上方最高的 in-figure 文本块的 y 作 fig_top，避免出现大段空白。
   - **caption 续行**：往下吸收 caption 的多行延续（gap ≤8pt、列范围内、含小写字母、不跨到下一张 figure）。
   - **渲染 bbox**：`page.get_pixmap(clip=bbox, matrix=Matrix(dpi/72))`，保存 PNG。

3. 返回 `[FigureCrop]`，每张含 `page / bbox / image_path / caption_prefix`，文件名按 `filename_template` 自动生成（默认 `fig01.png` `fig02.png` ...）。

### Step 2 — VLM 自校验 + 重抽循环

对 Step 1 的每张 crop，做以下流程：

```
for attempt in range(max_retries):
    render_full_page(pdf, current.page) → page.png  (144 DPI，缓存复用)
    
    prompt = """
      I'll show you two images: the candidate crop + the full page.
      Page dimensions: {page_w} × {page_h} (PDF 点).
      Current bbox: ({x0},{y0},{x1},{y1}).
      Caption "{label}" at y≈{cap_y0}-{cap_y1}.
      
      正确 crop 必须:
        - 包含完整 figure body (axes / labels / panels 都不能截)
        - 包含完整 caption 文字
        - 不能含 body 段落
        - 不能含同页其他 figure / table
      
      回复 JSON only:
        {"ok": true}                           if 对
        {"ok": false, "bbox": [...]}           if 错，给修正 bbox (PDF 点)
    """
    
    reply = runtime.exec([
        {"type": "image", "image_path": crop.png},
        {"type": "image", "image_path": page.png},
        {"type": "text", "text": prompt},
    ])
    
    verdict = _parse_verdict(reply)  # 宽松 JSON 解析，剥 markdown 围栏
    
    if verdict["ok"]:
        verified = True
        break
    
    new_bbox = verdict.get("bbox")
    if not (isinstance(new_bbox, list) and len(new_bbox) == 4):
        # VLM 说错但没给可用 bbox → 不再 retry
        break
    
    # 用 VLM 给的 bbox 重抽，覆盖原 PNG 文件
    _heuristic.extract_with_bbox(pdf, current.page, new_bbox, crop.image_path)
    current = FigureCrop(page, new_bbox, image_path, caption_prefix)
    retries_used += 1
```

VLM 反复看 + 改的过程在同一个 PNG 文件上覆盖渲染。

**`_parse_verdict` 的容错**：
- VLM 回 markdown 围栏 ```json ... ``` → 自动剥
- 回里夹了解释 prose → 用正则抓首个 JSON 对象
- 解析失败 → 默认 `{"ok": True}`（视为通过，避免死循环）

**何时认怂**：
- 走完 `max_retries` 轮 → 接受最后一版（`verified=False`）
- VLM 说错但 bbox 字段无效 → 立即接受当前版（`verified=False`）

### Step 3 — 返回

```python
[
    {
        "label": "Figure 1",
        "page": 3,
        "bbox": (104.0, 71.0, 508.0, 234.0),
        "image_path": "/path/figures/fig01.png",
        "caption_prefix": "Figure 1:",
        "retries_used": 0,
        "verified": True,
    },
    ...
]
```

`retries_used` = VLM 实际改了几次；`verified` = 最终是否得到 VLM 认可。调用方想审就看 `verified=False` 的那几张。

## 调用示例

```python
from openprogram.programs.applications.pdf_figures import extract_pdf_figures

results = extract_pdf_figures(
    pdf_path="/path/to/paper.pdf",
    out_dir="/path/to/figures/",
    runtime=runtime,
)

for r in results:
    if not r["verified"]:
        print(f"manual check: {r['label']} page {r['page']}")
```

通过 webui 直接选 `extract_pdf_figures` 这个 program 调用也可以，框架自动注入 runtime。

## 私有层 `_heuristic.py` 提供的原语

不通过 application 路径、想直接走纯 pymupdf 启发式（无 LLM 成本）的调用方可以 import：

| 函数 | 用途 |
|---|---|
| `list_captions(pdf)` | 仅发现，返回 `[CaptionRef]` |
| `extract_all_figures(pdf, out_dir)` | 一行抽所有 figure（启发式版，无 VLM） |
| `extract_one_figure(pdf, prefix, out_path)` | 单图按 caption prefix 抽 |
| `extract_figures(pdf, captions, out_dir)` | 批量按 prefix list 抽 |
| `extract_with_bbox(pdf, page, bbox, out_path)` | 手工 bbox 渲染 |
| `render_full_page(pdf, page, out_path)` | 整页渲染（VLM 校验用） |

`research_harness/stages/wiki/_helpers.py::extract_pdf_figure` 走的是 `_heuristic.extract_one_figure`，不付 VLM 费用。

## 启发式层的已知失败模式（VLM 层就是为修这些而存在的）

| 失败模式 | 现象 | VLM 通常怎么修 |
|---|---|---|
| Wrapfigure 布局 | body 绕图流过，启发式 bbox 太矮 | VLM 把 fig_top 往上扩，fig_bottom 往下扩到 caption 底 |
| 页首 figure | fig_top 默认到页顶导致大块空白 | VLM 把 fig_top 收紧到 figure 内容真正起点 |
| 多图同页边界 | 启发式把 Fig 3 区域漏到 Fig 4 顶部 | VLM 把 fig_bottom 收紧到不越界 |
| 章节标题被当 figure 内容 | crop 顶部带"D Causal Tracing..."标题 | VLM 把 fig_top 调到标题下方 |
| caption 字号与 body 同 | 启发式 caption 续行误吃 panel-title 行 | VLM 把 fig_bottom 收紧 |
