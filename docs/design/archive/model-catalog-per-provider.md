# 模型目录:按 provider 拆分 + fetch 即刷新

## 问题

模型目录现在是**两套数据并存,会打架**:
- 静态:`providers/models_generated.json`(单文件,741 模型,~2 万行,声明
  "auto-generated" 但生成器已丢失 → 过时、没法更新、谁都不敢动)
- 动态:fetch 拉的存进 `config.json` 的 `custom_models`

两套撞车的实证:claude-code 出现「同名但 200K/1M 不同」的重复条目(静态
Meridian 别名 + fetch 真模型)、缺 4.8(静态过时)。

## 目标

1. 消灭单个两万行大文件 → 每个 provider 一个小 `models.json`,放各自子目录,
   模型跟代码在一起。
2. fetch = 刷新该 provider 自己的 models.json(整体重写),不再手维护、不再
   存进 config.json 的 custom_models。
3. 一套数据(per-provider 文件),消灭静态/动态打架。
4. **公共接口 `MODELS` dict 不变** → 14 个依赖点一行不改。

## 数据布局

```
providers/
├── anthropic/models.json        ← anthropic 的模型(~23)
├── openai_responses/models.json ← openai(~36)
├── google/models.json           ← gemini(~21)
├── openrouter/models.json       ← openrouter(~233,独立但仍大,是其本性)
├── ...每个 provider 一个
└── _catalog/                    ← 没有对应代码子目录的 provider 兜底放这
    └── <provider>.json
```

`models_generated.json` / `.py` 退役(或 .py 改成扫描合并器)。

## 加载器(models_generated.py 改写)

```
def _load() -> dict[str, Model]:
    merged = {}
    for jf in glob(providers/*/models.json) + glob(providers/_catalog/*.json):
        for key, row in json.load(jf).items():
            merged[key] = Model.model_validate(row)
    return merged

MODELS = _load()
```

key 仍是 `"<provider>/<id>"`,所以 `MODELS` 的形状、所有 `MODELS["x/y"]`
调用完全不变。

## fetch 落盘(storage 改写)

现在:`replace_fetched_models` 写进 config.json 的 custom_models。
改成:fetch 结果直接重写 `providers/<provider>/models.json`(原子写)。
下次进程读 MODELS 即最新。运行中可热更新内存 MODELS。

claude-code 特殊:它没有自己的 pool/目录,凭证借 anthropic,但模型是
claude-code provider 的 → 写 `providers/anthropic/claude_code_models.json`
或 `_catalog/claude-code.json`(待定,实现时定)。

## 迁移步骤(每步可验证、可回滚)

1. ✅ **拆数据**:把 models_generated.json 按 provider 切成
   `_catalog/<provider>.json`(21 个文件)。验证:合并回来与原 dict 逐条
   等价(738 条,内容零差异)。
2. ✅ **改加载器**:models_generated.py 改成扫描 `_catalog/*.json` 合并。
   验证:MODELS = 738 原始 + 3 claude-code 种子 = 741,关键字段与拆前全等。
3. ✅ **删大文件**:`git rm models_generated.json`。删后 MODELS/provider 列表/
   import 链全正常。两万行文件从仓库消失。
4. ⏸ **改 fetch 落盘(暂不做)**:把 fetch 从写 config custom_models 改成写
   `_catalog/<provider>.json`。**本次不做** —— `replace_fetched_models`
   与 enabled_models 管理 / manual-override / models_fetched 标志深度耦合
   (provider 这块"经常出问题"的高风险区);核心目标(消灭大文件 + per-provider
   拆分)前 3 步已达成,fetch 落 config 现状工作正常(claude-code 9 模型即如此),
   不为"统一存储"引入新风险。两套数据的打架已在上一轮根除(删 Meridian 别名)。
   留作独立后续。

全量回归 823 passed。

## 风险与回滚

- 风险:14 依赖假设 MODELS 同步可用 → 加载器保持同步(import 时全读完),
  不引入异步,风险降到最低。
- 回滚:每步独立 commit;任一步坏了 revert 该 commit,大文件还在历史里。

## 不做(本次范围外)

- 不删静态数据本身(离线要它),只改它的物理存放 + 更新方式。
- 不改 14 个依赖点的代码。
- openrouter/vercel 仍大,不拆它们内部(那是聚合平台的本性)。
