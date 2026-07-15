# API key / 凭据解析统一

状态：**核心已落地（步骤 1–5）** · 步骤 6 暂缓（见 §4） · 负责人：providers · 创建于：2026-06-04

属于 2026-06 优化路线图的一部分（审计项 #3 —— provider 配置碎片化主题的根因）。承接 [credential-validation-unification](credential-validation-unification.md)：那篇文档统一了"这个 key 是否*有效*"；本篇统一"这个 key *是什么*，以及 provider *是否已配置*"。

## 1. 问题

"provider X 用的是哪个 API key，X 是否已配置？"这个问题至少由**四张 env-var 映射表**和**三个解析器**回答，各自掌握着不同的知识：

映射表（provider → env var）：
- `providers/env_api_keys.py:10` `PROVIDER_ENV_VARS` —— 20 个 provider；`google → GEMINI_API_KEY`。
- `webui/_model_catalog/providers.py:97` `_ENV_API_KEYS` —— 19 个 provider；`google → GOOGLE_GENERATIVE_AI_API_KEY`、`anthropic → ANTHROPIC_API_KEY`。
- `webui/_model_catalog/credentials.py` `provider_id_for_env_var` —— 内联的反向别名。
- `webui/_model_catalog/storage.py:_resolve_api_key` —— 内联的 Google 多名称特例。

解析器：
- `env_api_keys.get_env_api_key(provider_id)` —— **运行时**路径（被
  `providers/stream.py:62,105` 以及每个 provider adapter 使用）。**只读 env var，不读
  config.json。** 掌握着最广的特例知识：GitHub Copilot（3 个
  token）、Anthropic（`ANTHROPIC_OAUTH_TOKEN` > `ANTHROPIC_API_KEY`）、Amazon
  Bedrock + Google Vertex（返回 `"<authenticated>"` 哨兵值）。
- `storage._resolve_api_key(provider_id)` —— **webui/model-catalog** 路径
  （validate、fetcher、test）。使用 `_env_var_for`（*另一张*映射表）+ 一个
  config.json `api_keys` 兜底 + 我的 Google 多名称兜底。它**不**
  知道 Anthropic OAuth 的优先级、Bedrock/Vertex 或 Copilot。
- `server._get_api_key(env_var)` —— 以 env-var *名称*（而非 provider id）为键；
  env > config.json。在 `check_providers` 中用于"是否已配置"检查。

### 后果

1. **同一个 provider 在一个界面读到"已配置"、在另一个界面读到"缺失"。**
   `google` 在不同路径下以不同的 env-var 名称解析。Anthropic 在运行时解析
   为 `ANTHROPIC_OAUTH_TOKEN`，但在 webui 中解析为 `ANTHROPIC_API_KEY`。
2. **潜伏的运行时 bug。** 启动时没有任何代码把 config.json 的 `api_keys` 注水进
   `os.environ` —— 只有 `routes/config.py:87` 会做，且*只在保存时、在该
   进程内*生效。运行时解析器 `get_env_api_key` 是**只读 env 的**。所以一个纯粹
   通过 web UI 保存的 key 会存在于 config.json + 当前活跃进程的 env 中，但
   在 **worker 重启**之后它就从 env 里消失了，运行时 LLM 调用即便 config.json 里有它也
   找不到。（webui 路径掩盖了这一点，因为
   `_resolve_api_key` *确实*会读 config.json —— 于是连通性检查通过，
   而实际聊天却失败。）
3. **`"<authenticated>"` 哨兵值**把"已配置"和"这就是 key"混为一谈：
   Bedrock/Vertex 返回一个假字符串，任何写 Bearer header 的代码都会原样
   发出去。如今它们的运行时 adapter 走的是 AWS/ADC SDK 链，只把它
   当作真值标志，但这是个隐患。

## 2. 目标

一个规范的凭据模块 —— `providers/env_api_keys.py`（它已经拥有
最广的特例知识，且位于 `providers/` 下，运行时和 webui 都能 import 且无循环
依赖）。其余每个解析器/映射表都变成它之上的薄封装。统一到一个**带
config.json 兜底的解析器上同时也修掉了重启 bug**，所以这是正确性工作，
而不只是去重。

最佳设计标准：解析器必须是唯一一处知道任何
provider 的凭据如何被找到的地方，分层（env → config → 云凭据链），
在热路径上有缓存，且可反向映射 —— 这样新增一个 provider 只需一条记录。

## 3. 规范 API（位于 `providers/env_api_keys.py`）

```python
def env_vars_for(provider_id: str) -> list[str]:
    """该 provider 接受的 env-var 名称，按优先级排序。
    google -> [GEMINI_API_KEY, GOOGLE_API_KEY, GOOGLE_GENERATIVE_AI_API_KEY]
    anthropic -> [ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_API_KEY]
    github-copilot -> [COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN]
    替代 PROVIDER_ENV_VARS、_ENV_API_KEYS 和 _env_var_for。"""

def resolve_api_key(provider_id: str, *, allow_config: bool = True) -> str | None:
    """真实可用的 key/token，或 None。
    1. 按 env_vars_for() 中的每个 env var 逐一尝试，第一个命中者胜出；
    2. 若 allow_config：对每个名称查 config.json api_keys[<name>]（带缓存）；
    3. 云凭据 provider（bedrock/vertex）-> 这里返回 None（没有 bearer key）；
       它们的状态由 is_configured() 表达，而不是一个 key。
    替代 get_env_api_key 和 storage._resolve_api_key。"""

def is_configured(provider_id: str) -> bool:
    """当 provider 拥有可用凭据时为 True，包括云凭据
    链：resolve_api_key() 不为 None，或者 bedrock AWS 链 / vertex ADC
    被满足（即过去用来生成 '<authenticated>' 哨兵值的那套逻辑）。
    替代散落各处的 bool(_get_api_key(env)) / _is_configured 检查。"""

def provider_id_for_env_var(env_var: str) -> str | None:
    """env_vars_for 的反向映射，供只知道 env-var
    名称的 save-key verify 路径使用。从 credentials.py 迁移到此处。"""
```

`"<authenticated>"` 哨兵值被**删除**：对云凭据 provider，`resolve_api_key` 返回 `None`
（它们的 adapter 从来没把它当真正的 key 用过），而
`is_configured` 承载"是的，已配置"这个答案。一个轻量的模块级缓存
（按 mtime 缓存的 config dict）让 `resolve_api_key` 在每次 stream 的
热路径上不去碰文件系统。

## 4. 迁移（每一步都可独立 commit + 独立验证）

状态：步骤 1–5 **已落地**（commit f4fec73d、9d4d55dc、62e78e3c、d3ce990d、
5c4d6aa6）。步骤 6 **暂缓** —— 见列表后的说明。

1. **（已完成）** 把规范函数（`env_vars_for`、`resolve_api_key`、
   `is_configured`、`provider_id_for_env_var`）加入 `env_api_keys.py`，附带
   合并后的 env-var 表 + 按 mtime 缓存的 config 读取。未改动任何调用方。新增了
   `tests/unit/test_api_key_resolution.py`。
2. **（已完成）** `storage._resolve_api_key` 委托给 `resolve_api_key`（已知
   provider）；community/models.dev 类 provider 保留 env-var 兜底。
3. **（已完成）** `get_env_api_key`（运行时）委托给 `resolve_api_key` —— 获得
   config.json 兜底（即重启 bug 的修复）；Bedrock/Vertex 为其 adapter 保留
   哨兵值。
4. **（已完成）** `_model_catalog/providers.py:_is_configured` 的 key 分支与
   `providers/registry.py:check_providers`（后者原本只读 env —— 同样的
   重启 bug，现已感知 config）委托给规范的 `is_configured`。
   `server.py` 的 provider 表 + `routes/providers.py:45` 保留 `_get_api_key`
   —— 它已经同时感知 env+config（属于重复，但无 bug），原样保留。
5. **（已完成）** `credentials.provider_id_for_env_var` 重新导出规范实现。
6. **（暂缓）** 合并那些遗留的扁平映射表。*这不是一次安全的机械改动：*
   `_env_var_for`/`_ENV_API_KEYS` 是**展示首选**名称（在 key 表单中
   anthropic → `ANTHROPIC_API_KEY`），这与
   `env_vars_for` 的**解析优先级**列表（anthropic → OAuth 优先）是不同的概念，所以
   `_env_var_for` *并不是* `env_vars_for(pid)[0]`。而扁平的 `PROVIDER_ENV_VARS`
   仍被 `auth/cli.py` + `auth/interactive.py` 当作
   "带 env-key 的 provider" 列表来消费，其成员**有意排除了**
   anthropic/copilot（曾经的特例）—— 从规范
   表派生这份列表会改变该列表的成员构成以及 auth-CLI 登录流程。要收尾
   此项，得先把 auth-CLI 的语义理顺；作为未来工作跟踪。

向后兼容：`get_env_api_key`、`_resolve_api_key`、`_env_var_for` 保留各自
名称作为薄封装，这样那约 30 处调用点不会被搅动。

## 5. 验证

- 单元测试：`tests/unit/test_api_key_resolution.py` —— 优先级、config 兜底、
  无 key 时云凭据 is_configured 为 True、反向映射、Anthropic OAuth>key、
  Google 三名称、`resolve_api_key` 从不返回 `<authenticated>`。
- 跨界面：对用户已配置的每个 provider（anthropic、openai、
  google、deepseek、openrouter……）确认运行时路径与 webui 路径
  得到**相同**的解析结果。
- 重启 bug 复现：通过 `/api/config` POST 一个 key，重启 worker，断言
  `resolve_api_key` 能从 config.json 找到它（env 已清空）。
- 每个迁移步骤：重启 worker、`/healthz`、`/api/providers/auth-status`
  不变，某个真实 provider 的 validate 仍为 `valid`。

## 6. 待解问题

- 运行时的 config 兜底应当始终开启，还是放在 `allow_config`
  之后、默认为 True 并给纯 env 部署留一个关闭的途径？（建议：
  始终开启；config.json 里的 key 无论 env 如何都是用户的意图。）
- config 读取缓存：每次调用做一次 mtime-stat 很廉价；TTL 更简单。建议
  用 mtime，这样刚保存的 key 无需重启即可被立即拾取。
- 更长远：一个 `Provider` 元数据 dataclass（id、env_vars、kind、base_url、
  default_api），把 [credential-validation-unification](credential-validation-unification.md) 的
  KIND 表和 `_PROVIDER_DEFAULT_API` 折叠进去 —— 每个 provider 一个 registry。本文
  范围之外；本篇只统一 key 解析。
