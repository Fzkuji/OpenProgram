# Harness

**harness**（一个 *agentic program*）是一个自包含的、由 agentic
function 组成的 git 仓库——OpenProgram 在
`openprogram/functions/agentics/` 下发现它，其函数会像内置函数一样注册。
这是一套**通用机制**：第一方程序（gui / research / wiki）与任何第三方仓库
的安装方式完全相同。跨平台（macOS / Linux / Windows）；无需 symlink。

> **agent 在哪里读取本文档：** 本文件是规范流程。
> 当用户要求安装某个 agent 尚未拥有的 harness 时，请遵循
> 下面的步骤——它们被写成可逐步执行的形式。

## TL;DR

```bash
# 第一方程序——按名称：
openprogram programs install research      # 轻量（无额外依赖）
openprogram programs install gui           # 重量级（拉取 torch/opencv）

# 任意第三方 harness——同一条命令，按 git 来源：
openprogram programs install https://github.com/<owner>/<Harness-Name>
openprogram programs install <owner>/<Harness-Name>     # GitHub 简写

# 管理：
openprogram programs available             # 状态，含第三方
openprogram programs uninstall research    # 第一方：按名称
openprogram programs uninstall <Harness-Name>   # 第三方：按目录名
openprogram programs install <ref> --upgrade    # git pull + 重新解析依赖

# ……重启 OpenProgram。完成——函数会自行注册。
```

---

# 第一部分 —— 使用 harness

## `programs install` 做了什么

第一方和第三方都是相同的四个步骤：

1. **浅克隆（shallow-clone）** 仓库到
   `openprogram/functions/agentics/<Repo-Name>/`——一个真实、可编辑的
   目录（不是 site-packages）。该克隆被 OpenProgram 加入 git-ignore，
   因此它始终是一份独立的检出（checkout），你可以 `git pull`
   或就地编辑。
2. **安装 harness 自身声明的依赖**——harness 是自描述的：会安装其
   `pyproject.toml`/`setup.py`（优先）或
   `requirements.txt`。OpenProgram 不携带任何按 harness 维护的
   依赖清单。
3. **校验契约（contract）**——克隆中必须包含一个带有
   `agentics/__init__.py` 的 package（见第二部分）。不匹配的仓库会被
   报告并直接不注册；它绝不会破坏加载过程。
4. 在下次启动时，registry 导入 `<package>.agentics`，
   `@agentic_function` 装饰器触发，函数随即出现在
   chat / Functions 页面 / `openprogram programs run` 中。

防护机制：`install` 拒绝触碰已存在的 **dev symlink**
（那是你的，见下文）或同名但不是 git 克隆的目录。对 symlink 执行
`uninstall` 只会删除该链接——绝不会删除它指向的检出。

## 第一方程序（gui / research / wiki）

| 程序 | 安装 | 说明 |
|---|---|---|
| [Research Agent](https://github.com/Fzkuji/Research-Agent-Harness) | `openprogram programs install research` | 无额外依赖 |
| [Wiki Agent](https://github.com/Fzkuji/Wiki-Agent-Harness) | `openprogram programs install wiki` | Jinja2 + PyYAML（极小） |
| [GUI Agent](https://github.com/Fzkuji/GUI-Agent-Harness) | `openprogram programs install gui` | 重量级：通过 ultralytics 引入 PyTorch + OpenCV。在无 GPU 的 Linux 上会自动选择 CPU 版 torch wheel（约 200 MB），而非约 3 GB 的 CUDA 构建。 |

`openprogram programs install all` 会安装这三个；首次运行的安装向导
中的 “Agent programs” 步骤会以交互方式提供相同的选择。

> **GUI agent —— 一个额外步骤。** 除了 pip 依赖，`gui_agent` 还需要
> 一份 YOLO 检测器权重 + OCR 模型，它们不在 PyPI 上。安装完成后，
> 运行 harness 自带的安装器来获取它们（由于你已经有了 host，它会
> 跳过 host）：
> `openprogram/functions/agentics/GUI-Agent-Harness/scripts/install.sh --no-host`
> （Windows：`…\scripts\install.ps1 -NoHost`）。参见
> [GUI 安装指南](https://github.com/Fzkuji/GUI-Agent-Harness#1-install)。

## 第三方 harness

任何人的 harness 仓库都用同一条命令安装——无需编辑目录清单，
任何地方都无需注册步骤：

```bash
openprogram programs install https://github.com/<owner>/<Harness-Name>
openprogram programs install <owner>/<Harness-Name>   # GitHub 简写
openprogram programs install file:///path/to/checkout # 本地 git 来源
```

`openprogram programs available` 会列出已安装的第三方 harness
及其契约状态；`openprogram programs uninstall
<Harness-Name>` 按克隆目录名移除其中一个。

<details>
<summary>手动等价方式（镜像 / 无法访问 GitHub）</summary>

`<AGENTICS>` 是 OpenProgram 的内置函数文件夹：

```bash
python -c "import openprogram,os;print(os.path.join(os.path.dirname(openprogram.__file__),'functions','agentics'))"
```

```bash
git clone <repo-url> "<AGENTICS>/<Harness-Name>"
pip install "<AGENTICS>/<Harness-Name>"        # 或其 requirements.txt
# 重启 OpenProgram
```

自动发现会拾取 `<AGENTICS>` 中任何满足契约的目录——这就是安装命令
所自动化的全部内容。

</details>

## 开发者配置（开发你正在编写的 harness）

把你的工作检出做成 symlink，而不是克隆一份副本：

```bash
ln -s /path/to/your/Harness-Checkout "<AGENTICS>/Harness-Checkout"
```

编辑会在下次重启时生效；`programs install` 会拒绝覆盖该链接，
而 `programs uninstall <name>` 只会移除该链接。（Windows 注意：
symlink 需要开发者模式——在那里受支持的路径是克隆一个真实目录。）

## 校验一次安装

```bash
openprogram programs available     # 安装状态（第一方与第三方）
openprogram programs list          # 所有已注册的函数
```

要查看一个存在但损坏的 harness 为何未加载：

```bash
OPENPROGRAM_DEBUG_REGISTRY=1 openprogram programs list
```
（Windows PowerShell：`$env:OPENPROGRAM_DEBUG_REGISTRY=1; openprogram programs list`）

然后就可以使用它——harness 的函数像任何内置函数一样可调用
（在 chat 中，或 `openprogram programs run <fn> -a key=value`）。

## 平台说明

- **基础安装在每个操作系统上都是一条命令：** 克隆 OpenProgram 并运行
  `./scripts/install.sh`（Windows：`.\scripts\install.ps1`）。
- **无需 symlink**——把一个真实目录克隆到 `<AGENTICS>` 是受支持的
  路径，因此不存在 Windows 管理员/开发者模式的门槛。
- **harness 在自身代码中仍可以是平台相关的**（例如，桌面 GUI
  harness 可能只实现 macOS / Linux 后端）。
  安装总能成功；每个函数是否能在你的操作系统上*运行*则是
  harness 自己的事情——查看它的 README。
- **编码 / 路径：** OpenProgram 自身的工具链全程基于 UTF-8 和
  `os.path`；一个表现良好的 harness 也应如此。

## 故障排查

| 现象 | 原因 / 修复 |
|---|---|
| 重启后 harness 函数没有出现 | 文件夹不匹配契约——确认 `<pkg>/agentics/__init__.py` 存在并导出 `AGENTIC_FUNCTIONS`。用 `OPENPROGRAM_DEBUG_REGISTRY=1` 运行。 |
| 安装时出现 `[!] … no package with an agentics/__init__.py was found` | 同上——该仓库不满足契约（第二部分）。 |
| harness 自身依赖出现 `ModuleNotFoundError` | 依赖安装步骤失败——对该克隆（或其 requirements.txt）执行 `pip install` 并检查错误。 |
| harness 内部的导入失败（`from <pkg>.x import y`） | package 目录的命名与导入根不一致，或缺少 `__init__.py`。package 文件夹名必须等于导入名。 |
| 安装时出现 `[skip] … is a dev symlink` | 这是有意为之：安装器绝不会触碰你链接的检出。如果你确实想要一份克隆，先移除该链接。 |
| 函数能加载，但在 Windows 上*运行*时报错 | harness 自身代码是平台相关的——这是它的事情，不是安装的事情。查看它的 README。 |

---

# 第二部分 —— 编写你自己的可安装 harness

任何满足某一布局契约的仓库，都会成为每个 OpenProgram 用户的
一键安装项。

## 契约

```
<Harness-Name>/                      ← 仓库（任意名称）
├── pyproject.toml                   ← 只声明 harness 自身的依赖
└── <package>/                       ← 一个可导入的 package（ascii 名称）
    ├── __init__.py                  ← 保持依赖轻量
    └── agentics/
        └── __init__.py              ← 暴露 AGENTIC_FUNCTIONS = [...]
```

注册的入口点是 **`agentics` 子 package**——在启动时
OpenProgram 导入 `<package>.agentics`；该次导入会触发
`@agentic_function` 装饰器，它们自行注册到共享的
registry 中。harness 根目录也可以附带（vendor）其他 package——
发现机制会找到带有 `agentics/` 子 package 的那一个，并将 harness 根
放到 `sys.path` 上，于是 harness 自身的绝对导入
（`from <package>.foo import bar`）就能解析。

## 最小可用模板

```python
# <package>/agentics/__init__.py
from openprogram.agentic_programming.function import agentic_function


@agentic_function
def my_tool(text: str = "") -> str:
    "一行：说明它做什么（会显示在目录中）。"
    return text.upper()


AGENTIC_FUNCTIONS = [my_tool]
```

```python
# <package>/__init__.py
"""我的 harness —— 保持本导入轻量（见硬性规则 2）。"""
```

```toml
# pyproject.toml
[project]
name = "my-harness"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []          # harness 自身的依赖——绝不要写 openprogram
```

这就是一个完整的可安装 harness。

## 两条硬性规则

1. **绝不要把 `openprogram` 声明为依赖**（无论在 `pyproject.toml`
   *还是* `requirements.txt` 中）。harness 在一个已存在的
   OpenProgram 安装内运行；一条声明的 `openprogram @ git+…` 会让 pip
   从 git 重新安装 host，从而覆盖用户本地的（通常是可编辑的）安装。
2. **保持顶层 `<package>/__init__.py` 依赖轻量，并在
   `agentics/__init__.py` 中为重量级导入做保护。** 发现机制在每次启动
   时都会导入 `<package>.agentics`，包括在那些尚未安装你的可选/重量级
   依赖的机器上——顶层导入 cv2/torch/等会破坏整个 registry 的加载。
   把重量级模块在函数体内做惰性导入，并为入口导入做保护：

   ```python
   # agentics/__init__.py —— 缺少依赖的机器不能破坏加载
   try:
       from my_package.main import my_tool
       AGENTIC_FUNCTIONS = [my_tool]
   except ImportError:
       AGENTIC_FUNCTIONS = []
   ```

三个第一方 harness 都遵循这一确切形态——把它们中的任何一个
当作可用模板来阅读。

## 发布前在本地测试

安装命令接受 `file://` 来源，因此可以针对你的本地检出测试完整的
用户流程：

```bash
cd /path/to/My-Harness && git add -A && git commit -m wip
openprogram programs install file:///path/to/My-Harness
openprogram programs available        # 应显示：My-Harness [ok] (package: …)
OPENPROGRAM_DEBUG_REGISTRY=1 openprogram programs list   # 函数是否出现？
openprogram programs run my_tool -a text=hello           # 冒烟测试
openprogram programs uninstall My-Harness                # 清理
```

发布前的检查清单：

- [ ] `<package>/agentics/__init__.py` 暴露了 `AGENTIC_FUNCTIONS`
- [ ] pyproject/requirements 中没有 `openprogram`（硬性规则 1）
- [ ] 在只安装了 OpenProgram 的纯净 venv 中
      `python -c "import <package>.agentics"` 成功（硬性规则 2）
- [ ] 上面的 `file://` 安装往返测试通过

## 发布

推送到 GitHub。用户用以下命令安装：

```bash
openprogram programs install <owner>/<Harness-Name>
```

任何地方都无需注册——仓库 URL *就是*分发形式。
