# 模型目录 — 最终版

## 用户定的四条职责

1. **models.dev 定期自动更新** —— 当通用模型目录(价格/能力/通用上下文),
   隔段时间(TTL)自动刷新。
2. **fetch = 拉 provider 官方模型列表**(如 Anthropic /v1/models),**不是**
   刷 models.dev。两件独立的事。
3. **fetch 结果覆盖式保存,每 provider 单独存** —— fetch anthropic 就覆盖
   anthropic 那一份。
4. models.dev + fetch 结果怎么结合 → 见下;其余死代码删除。

## 两个源的天然分工(已实测字段)

| | 官方 /v1/models(fetch) | models.dev |
| --- | --- | --- |
| 有哪些模型(这号能用啥) | ✅ 权威 | 通用,不分订阅 |
| 上下文/输出上限 | ✅ | ✅ |
| 价格 cost | ❌ 无 | ✅ |
| modalities/reasoning_options 细节 | 粗(capabilities) | ✅ 细 |

**分工**:fetch 定"有哪些 + 上下文"(权威),models.dev 补"价格 + 能力细节"。

## 数据布局

```
~/.openprogram/models/
├── models_dev.json              ← models.dev 全量缓存(定期自动刷, 通用底料)
├── fetched/
│   ├── anthropic.json           ← fetch anthropic 官方列表(覆盖式)
│   ├── openai.json
│   └── <provider>.json          ← 每 provider 一份, fetch 即覆盖
└── (无别的)
```

全部在 `~/.openprogram/`,**不进 git**。仓库里关于模型数据 0 文件。

## 结合逻辑(构建 MODELS)

```
def build_MODELS():
    md = read(models_dev.json)            # 通用底料(价格/能力/上下文)
    merged = {}
    for provider in 所有已知 provider:
        fetched = read(fetched/<provider>.json)   # 该号官方真列表(可能没有)
        if fetched 存在:
            # fetch 决定"有哪些 + 上下文",models.dev 补"价格/能力"
            for m in fetched:
                base = md_lookup(provider, m.id)   # 从 models.dev 找同 id 补字段
                merged[f"{provider}/{m.id}"] = 合并(权威=fetched, 补充=base)
        else:
            # 没 fetch 过 → 直接用 models.dev 的该 provider 全部模型
            for m in md[provider]:
                merged[f"{provider}/{m.id}"] = from_models_dev(m)
        # 本地补 api/base_url(models.dev 没这俩),订阅 provider 借用兄弟
    return merged
```

要点:
- **fetch 过的 provider** → 以官方列表为准(有哪些/上下文),models.dev 补价格能力。
  完美对应"fetch 覆盖" + "结合"。
- **没 fetch 的 provider** → 退回 models.dev 全量(开箱即用,不用每个都 fetch)。
- **订阅 provider**(claude-code/openai-codex):models.dev 无 → 借兄弟
  (claude-code←anthropic) 的 models.dev 数据 + 改 api/base_url;fetch 时
  存 fetched/claude-code.json(它有自己的官方列表)。

## 刷新时机

- **models_dev.json**:TTL 24h。启动时若过期→后台/惰性刷一次。无网→用旧缓存,
  再无→报错(本工具离线本就不可用,无需写死兜底)。
- **fetched/<provider>.json**:仅用户点"Fetch models"时刷该 provider,覆盖写。

## MODELS 加载(保持同步, 14 依赖零改)

`models_generated.py:_load()` → 调 build_MODELS()。import 时同步填满,
接口不变,14 依赖无感。Fetch 后热重建 MODELS(重新 build)。

## 退役 / 删除(死代码)

确认删:
- 静态快照:`providers/_catalog/*.json`(被 models.dev 缓存取代)
- 代码种子:`_claude_code_registry._SEED`(被"订阅借用"取代)
- config 的 `custom_models`/`models_fetched`(被 fetched/<p>.json 取代)
- Meridian 死代码堆:`_max_proxy_runtime.py`、`_meridian_cli.py`、
  `_claude_max_proxy_registry.py`、`cli_runtime.py`、`cli_backend.py`、
  `claude_models.py`、`claude_models.json`
  (先确认 webui/auth 无活跃引用——_meridian_cli 还被 accounts/login 引用?
   实现时逐个 grep 确认再删)

## 迁移步骤(每步独立 commit, 可验证可回滚)

1. **清死代码**:删 Meridian 堆 + claude_models.* + cli_runtime/backend。
   逐个 grep 确认无活跃引用。全量回归绿。(低风险, 先做)
2. **models.dev 缓存落盘**:models_dev.json 拉取+落盘+TTL+读取。单测。不接 MODELS。
3. **build_MODELS 结合逻辑**:fetched + models.dev 合并 + 订阅借用 + 补 api/base_url。
   验证产出与现状逐 provider 比对(只该多最新模型, 不该丢)。
4. **切加载器 + fetch 改向**:_load 用 build_MODELS;fetch 写 fetched/<p>.json
   覆盖式。验证 UI 列表正确、claude-code 模型对、断网用缓存。
5. **删 _catalog + 种子 + custom_models**:退役旧源。全量回归 + 浏览器自查。

## 风险与回滚

- 每步独立 commit;切加载器(步4)若坏 revert 回 _catalog 方案(commit 还在)。
- models.dev 字段变 → _normalise 已隔离。
- 不动 api_registry/wire/14 依赖代码。
