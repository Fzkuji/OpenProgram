# 模型目录动态化:以 models.dev 为源,消灭冻结快照

## 要解决的"乱"

现在模型数据有三处来源,新旧并存、会打架:
1. `_catalog/<provider>.json` —— 很久前从 models.dev 拉的**冻结快照**(会过时,如缺 4.8)
2. Fetch → config.json 的 `custom_models` —— 官方 /v1/models 实时拉
3. enrich —— Fetch 时从 models.dev 补字段

根因:**有一份"冻结的旧 models.dev 快照"在仓库里**。本设计把它干掉,改成
运行时从 models.dev 实时取 + 本地缓存,只留一份"有效数据"。

## 已确认的事实(设计依据)

- models.dev(`https://models.dev/api.json`):145 provider,anthropic 25 模型,
  **含 claude-opus-4-8**,字段全(limit.context/output、cost、modalities、
  reasoning、reasoning_options)。每天更新。
- `sources/models_dev.py` **已有** `_normalise()`:models.dev → 我们的字段
  (context_window/max_tokens/cost/vision/input_modalities)。**转换不用重写。**
- models.dev **没有** api/base_url 字段,也**没有订阅 provider**
  (claude-code/openai-codex/gemini-subscription)。
  - api/base_url 由我们 `providers.py:_default_api_for/_default_base_url_for`
    本地决定(已存在)。
  - 订阅 provider 借用其标准兄弟的数据(claude-code←anthropic,
    openai-codex←openai,gemini-subscription←google)。
- `Model` 必填:id/name/api/provider/base_url(+ 默认值字段)。
- 14 个依赖**同步**读 `MODELS` dict → 加载器必须 import 时同步填满。

## 目标架构

```
models.dev (在线, 每天更新)
   │  启动时拉一次(或缓存过期时)
   ▼
~/.openprogram/models_cache.json   ← 本地缓存(不在 git),已 normalise
   │
   ├─ 标准 provider: 直接用
   ├─ 订阅 provider: 借标准兄弟数据 + 改 provider/api/base_url
   │
   ▼  转 Model + 本地补 api/base_url
MODELS (内存 dict, key="<provider>/<id>")  ← 14 依赖照常同步读
   ▲
   └─ 内置 FALLBACK_SEED(代码里 ~10 行主力模型)
      没网且无缓存时兜底,保证启动不空
```

数据**只有一条有效链**:models.dev → 缓存 → MODELS。不再有"冻结快照"。

## 加载策略(关键:保持同步,不引入异步)

`models_generated.py:_load()` 改为:

```
def _load() -> dict[str, Model]:
    raw = _read_cache()                  # 1. 读本地缓存(快, 同步)
    if raw is None or _cache_stale():    # 2. 缓存没/过期 → 同步拉一次
        raw = _refresh_from_models_dev() # 拉+normalise+写缓存; 失败返回 None
    if not raw:
        raw = FALLBACK_SEED              # 3. 都没有 → 内置兜底
    return _build_models(raw)            # 转 Model + 补 api/base_url + 订阅借用
```

- **同步**:import 时一次把 MODELS 填满,14 依赖无感(它们读到的还是满的 dict)。
- **首启**:无缓存 → 拉一次(~300KB,1-2s)。之后走缓存,秒开。
- **离线**:有缓存用缓存;无缓存用 FALLBACK_SEED(几个主力,够跑)。
- **缓存 TTL**:24h(models.dev 日更);后台/手动可强刷。

## 订阅 provider 借用

models.dev 无 claude-code,但有 anthropic。映射表:

```
_SUBSCRIPTION_BORROW = {
  "claude-code": "anthropic",
  "openai-codex": "openai",
  "gemini-subscription": "google",
}
```

`_build_models` 对每个订阅 provider,取被借 provider 的模型,复制一份,改
`provider`/`api`/`base_url` 为订阅 provider 的(claude-code 用
anthropic-messages + api.anthropic.com)。这样 claude-code 自动有全部
anthropic 模型、且永远最新,不用单独 seed。

## "Fetch models" 怎么办

Fetch 从"写 config custom_models"改成"**强制刷新 models.dev 缓存 + 重建 MODELS**"。
即:Fetch = 立刻拉最新 models.dev(跳过 TTL),覆盖缓存。
- 一个动作、一条链,不再有 custom_models 第二份。
- 手动加模型(rare):仍可留 config custom_models 作"用户覆盖层"(可选,二期)。

## 退役

- `_catalog/<provider>.json`(21 个冻结快照)→ 删(数据改从 models.dev 来)。
- config 的 `custom_models` / `models_fetched` → Fetch 不再写它们(读取兼容保留)。
- `_claude_code_registry` 的代码种子 → 由"订阅借用"取代,删。

## 迁移步骤(每步可验证可回滚, 独立 commit)

1. **缓存层**:新增 models.dev 拉取+normalise+写 `~/.openprogram/models_cache.json`
   + FALLBACK_SEED。单测:拉取/缓存命中/过期/离线兜底。**不接入 MODELS。**
2. **订阅借用 + build**:`_build_models(raw)` 转 Model + 补 api/base_url + 订阅借用。
   验证:产出的 MODELS 与当前(_catalog+种子)逐 provider 比对,差异只该是
   "新增的最新模型"(如多了准确的 4.8/fable),不该丢现有模型。
3. **切加载器**:`_load()` 改用缓存层。验证:全量 823 + 浏览器 provider 列表完整、
   claude-code 模型正确、离线(断网)用缓存仍可启动。
4. **Fetch 改向**:Fetch = 强刷 models.dev 缓存 + 重建。验证:点 Fetch 后 UI 最新。
5. **退役清理**:删 `_catalog/`、`_claude_code_registry` 种子、Meridian 死代码堆。
   全量回归 + 浏览器自查。

## 风险与回滚

- 风险:models.dev 字段/可用性变 → `_normalise` 已隔离,且有缓存+FALLBACK 兜底。
- 风险:首启联网 → 可接受(一次,之后缓存);FALLBACK 保证最差也能跑。
- 回滚:每步独立 commit;切加载器(步3)若坏,revert 即回到 _catalog 文件方案
  (那俩 commit 还在)。

## 不做(范围外)

- 不动 api_registry / wire(发请求逻辑不变)。
- 不动 14 依赖的代码(MODELS 接口不变)。
- 手动加模型的 UI(custom 覆盖层)留二期,先把主链动态化。
