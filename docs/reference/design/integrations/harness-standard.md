# Harness 标准 —— agentic 程序如何接入 OpenProgram

> 状态：标准 / 规范。每个 **harness**（一个作为独立 repo 发布的自包含
> agentic 程序）都必须满足的契约，使得将其克隆进 OpenProgram 的
> `agentics/` 文件夹后即可 **被自动检测并直接使用，无需改动 host**。
> 三个第一方 harness（GUI / Research / Wiki）是参考实现；第三方遵循同样的
> 规则。
> 相关：[`../installing-harnesses.md`](../../installing-harnesses.md)
> （安装流程）、`openprogram/functions/_registry.py`（加载器）、
> `openprogram/functions/_programs.py`（第一方目录）。

## 0. 唯一重要的规则

**harness 就是任何你放进 `<openprogram>/functions/agentics/` 并通过
`<pkg>/agentics/__init__.py` 暴露其函数的东西。** host 在启动时（以及在
开启热重载时，每当该文件夹变化时）遍历该文件夹，找到内层 package，导入
`<pkg>.agentics`，随后 `@agentic_function` 装饰器自行完成注册。除此之外
无需任何东西，也不会改动任何 host 文件。

```
<Harness-Repo>/                    ← 你 git clone 进 agentics/ 的东西
└── <pkg>/                         ← 可导入的 package；文件夹名 == 导入名
    ├── __init__.py                ← 标记其为 package（可再导出一些便利项）
    └── agentics/
        └── __init__.py            ← 入口点 —— 暴露 AGENTIC_FUNCTIONS
```

如果克隆进来的文件夹不符合这个形状，host 会 **静默忽略它**（非 harness
的文件夹绝不能破坏加载）。所以"自动检测" == "符合此契约"。

## 1. 入口点：`<pkg>/agentics/__init__.py`

必须定义 `AGENTIC_FUNCTIONS` —— 一个由 `@agentic_function` 装饰的可调用对象
组成的列表。导入此模块会触发装饰器，将这些函数注册进共享的工具 registry。
该列表同时也是 harness 声明的公开接口面。

**必需模式 —— 优雅降级，绝不让 host 崩溃：**

```python
# <pkg>/agentics/__init__.py
"""Entry point for the Foo harness."""
try:
    from .foo_agent import foo_agent, foo_helper
    AGENTIC_FUNCTIONS = [foo_agent, foo_helper]
except Exception:          # 缺失可选依赖、不支持的平台，等等
    # host 在发现阶段会导入此模块。如果该 harness 无法在本机加载
    #（某个重依赖未安装、本 OS 没有对应 backend，等等），就什么都不
    # 暴露，而不是抛异常 —— 按发现契约："能跑 → 列出，不能跑 → 跳过"。
    # host 会在 OPENPROGRAM_DEBUG_REGISTRY=1 下记录原因。
    AGENTIC_FUNCTIONS = []
```

规则：
- `AGENTIC_FUNCTIONS` **必须始终被定义**（哪怕是 `[]`）。
- `try/except` 是 **强制的** —— 无法在本机运行的 harness 必须产出 `[]`，
  而不是把 `ImportError` 传播出去。正是这一点让"什么都能克隆进来、能跑的
  亮起、其余的保持熄灭"得以成立。
- 不要在导入期做繁重的工作（不下载模型、不联网、不抓取 GUI）。导入必须
  廉价，且除注册外无副作用。

## 2. package：`<pkg>/`

- **文件夹名 == 导入名。** `gui_harness/` 以 `gui_harness` 被导入。host 会把
  harness 根目录放上 `sys.path`，使 harness 自己的绝对导入
  （`from gui_harness.x import y`）能够解析。带连字符的 *repo* 文件夹
  （`GUI-Agent-Harness/`）没问题 —— host 会在其内部找到那个 ascii 标识符
  package。
- 可以内置（vendor）同级 package（例如 GUI harness 在 `gui_harness/` 旁边
  附带 `desktop_env/`）。host 会挑选 **唯一一个** 带有 `agentics/` 子 package
  的 package 作为入口；被内置的同级 package 在发现阶段被忽略，只是随之搭车
  上了 `sys.path`。

## 3. 配置 —— 统一约定

各 harness 在配置内容上千差万别（Research：providers + 工作目录；Wiki：一个
vault 路径；GUI：vision 模型 + 平台 backend）。**标准规定的是机制，而不是
具体参数**：

1. **优先使用按调用传入的参数。** 任何随每次调用而变化的东西，都作为带有
   合理默认值的函数参数：
   ```python
   @agentic_function(name="foo_agent")
   def foo_agent(task: str, max_steps: int = 15, runtime=None) -> dict: ...
   ```
2. **runtime 是被注入的，绝不自行构造。** 需要 LLM 的 harness 函数声明一个
   `runtime` 参数；host 会注入当前活跃的 runtime。对于 in-host 路径，harness
   **绝不能** 自己构建 provider / 自己读取 `ANTHROPIC_API_KEY` —— provider
   选择与鉴权归 host 管。（harness *可以* 保留一个独立的 CLI，调用
   `create_runtime(...)` 以便在 host 之外使用；那与 in-host 入口点是分开的。）
3. **机器级设置使用环境变量**，以 harness 自己的前缀作命名空间，并在 README
   中记录：
   ```
   WAH_VAULT          # Wiki：vault 根目录
   GUI_AGENT_MEMORY   # GUI：已学习组件的存储
   ```
4. **启动不需要任何配置文件。** harness 必须在零配置下可用（具备合理默认
   值）；配置是可选的覆盖项，绝不是一道启动门槛。不得有强制向导 / 首次运行
   提示。
5. **状态 / 临时数据放在 host 状态目录下**，而不是手搓的 home 路径。以
   `openprogram.paths.get_state_dir()`（→ `~/.openprogram/`）为基准；
   **绝不硬编码 `~/.agentic/...`**（该路径已废弃）。约定是为每个 harness 用
   一个子目录：`get_state_dir() / "harnesses" / "<pkg>"`。

## 4. 依赖

- **不要把 `openprogram` 声明为 git 依赖。** host 本身就是导入 harness 的那
  一方，所以它已经安装好了。harness 的 `pyproject.toml` 里的一行
  `openprogram @ git+https://...` 会导致 `pip install <harness>` 重新拉取、
  甚至可能降级 host。只把 openprogram 作为一个有文档说明的 *前提假设* 列出
  （"安装到一个已经有 openprogram 的环境里"），而不是一个硬依赖。
- **声明 harness 自己的第三方依赖**（torch、Jinja2，等等）于其
  `pyproject.toml` / `requirements.txt`。安装这些是 **harness 自己的** 安装
  步骤（在用户安装该 harness 时执行），不归 OpenProgram 管 —— host 绝不
  自动安装 harness 的依赖。
- **重 / 原生依赖放在一个 extra 之后**，让轻量克隆保持轻量：
  ```toml
  [project.optional-dependencies]
  ocr = ["easyocr"]
  ```
- §1 中那个优雅降级的 `try/except` 正是让 harness 能在其依赖装好之前就被
  *克隆* 进来而不破坏 host 的关键 —— 它只是保持熄灭，直到依赖到位。

## 5. 平台支持

- harness 在自己的代码里 *可以* 是平台特定的（GUI harness 驱动桌面；它的
  macOS / Linux backend 不同，Windows 可能尚未实现）。这是被允许的。
- **用 `AGENTIC_FUNCTIONS = []` 来表达"此处不支持"**，通过 §1 的
  `try/except` 或一个显式的 `platform.system()` 检查 —— 绝不要在导入期抛出
  未捕获的 `NotImplementedError`。安装 / 注册始终成功；某个函数是否被 *列出*
  反映的是它能否在本 OS 上运行。
- 在运行时检测 backend（`shutil.which`、`importlib.util.find_spec`），不要
  想当然。在 harness 的 README 中记录各 OS 的设置。

## 6. 发现与热重载（host 端 —— harness 可以依赖的东西）

- **启动：** host 导入 `agentics/` 下每个匹配的文件夹。
- **热重载（计划中）：** host 监视 `agentics/`，当出现一个新文件夹时，对其
  运行同样的发现流程，并广播一个 `programs:changed` 事件，使 web UI 无需重启
  即可列出新 harness。harness 不需要做任何特殊处理 —— 满足 §1 即可。
- **第一方目录：** GUI / Research / Wiki 还被列在 `_programs.py` 中，于是
  `openprogram programs install <name>` 能按名字把它们克隆下来。第三方
  harness 跳过该目录，直接通过克隆进 `agentics/` 来安装；发现流程对它们一视
  同仁。

## 7. 合规清单（供 harness 作者使用）

- [ ] repo 克隆进 `agentics/<Repo-Name>/`；其内部有一个 package
      `<pkg>/`，其文件夹名等于其导入名。
- [ ] `<pkg>/agentics/__init__.py` 定义 `AGENTIC_FUNCTIONS = [...]`。
- [ ] 该模块把其导入包在 `try/except` 中 → 失败时为 `[]`。
- [ ] 导入廉价：导入期不联网 / 不下载模型 / 不抓取 GUI。
- [ ] LLM 访问通过被注入的 `runtime` 参数，而非自建。
- [ ] 无必需配置文件 / 向导；在零配置默认值下可用。
- [ ] 状态置于 `get_state_dir()` 下，绝不用 `~/.agentic` 或其它硬编码的
      home 路径。
- [ ] `pyproject.toml` 中 `openprogram` 不是 git 依赖。
- [ ] 声明了自己的第三方依赖；重 / 原生依赖放在一个 extra 之后。
- [ ] 平台不支持 → `AGENTIC_FUNCTIONS = []`，而非崩溃。

## 8. 三个第一方 harness 当前的状态（差距清单）

来自对当前各 repo 的一次梳理 —— 这些是把它们带到本标准所需的改动（已记录，
留待我们更新它们时处理）：

| Harness | 是否合规？ | 待补齐的差距 |
|---|---|---|
| **Wiki** | ✅ 最接近 | `agentics/__init__.py` + `AGENTIC_FUNCTIONS` + `try/except` 均已具备。需修：默认 vault 路径用了已废弃的 `~/.agentic/memory/wiki` → 移至 `get_state_dir()` 下。去掉 `openprogram` git 依赖。 |
| **GUI** | ⚠ 部分合规 | 暴露了函数，但没有单一标准的、带 `AGENTIC_FUNCTIONS` 的 `<pkg>/agentics/__init__.py`；装饰器散落在多个模块里。补上入口模块。把重依赖保留在一个 extra 之后（已部分完成）。Windows 路径：降级为 `[]`，不要崩溃。去掉 `openprogram` git 依赖。 |
| **Research** | ❌ 不合规 | **根本没有 `agentics/` 子 package** —— 使用它自己的 `registry.py`，因此 host 的自动发现看不到它。补上 `research_harness/agentics/__init__.py` 暴露 `AGENTIC_FUNCTIONS`。去掉 `openprogram` git 依赖。 |

三者目前都把 `openprogram @ git+…` 声明为依赖（违反 §4）—— 去掉它是一个
共通的修复。
