# Harness

**harness**（一个 *agentic program*）是一个自包含的、由 agentic
function 组成的 git 仓库。每个受支持的 release 已经包含 GUI、Research、Wiki
三项第一方 Program package 与受支持的 runtime 资产。GUI package 不包含
PyTorch、OpenCV 或 EasyOCR，具体边界见下文。`openprogram programs install`
用于额外第三方 Program 或开发者 source overlay，不是补齐 release 安装的步骤。
immutable product runtime 拒绝原地安装、升级和卸载 Program。

> **agent 在哪里读取本文档：** 本文件是规范流程。
> 当用户要求安装某个 agent 尚未拥有的 harness 时，请遵循
> 下面的步骤——它们被写成可逐步执行的形式。

## TL;DR

```bash
# 第一方 Programs 已经存在：
openprogram programs available

# 在可变扩展或开发环境中增加第三方 harness：
openprogram programs install https://github.com/<owner>/<Harness-Name>
openprogram programs install <owner>/<Harness-Name>     # GitHub 简写

# 管理：
openprogram programs available             # 状态，含第三方
openprogram programs uninstall <Harness-Name>   # 第三方：按目录名
openprogram programs install <ref> --upgrade    # git pull + 重新解析依赖

# ……重启 OpenProgram。完成——函数会自行注册。
```

---

# 第一部分 —— 使用 harness

## `programs install` 做了什么

对于第三方 Program 或开发者 source overlay，该命令执行四个步骤：

1. **浅克隆（shallow-clone）** 仓库到
   `openprogram/programs/applications/<Repo-Name>/`——一个真实、可编辑的
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
4. **登记owner批准的来源。** 下次启动时，registry只导入已登记的
   `<package>.agentics`，
   `@agentic_function` 装饰器触发，函数随即出现在
   chat / Programs 页面 / `openprogram programs run` 中。

防护机制：对于已存在的 **dev symlink**，`install`会校验Harness契约并登记该链接，
但不修改链接目标；同名且不是Git克隆的目录仍会被拒绝。对symlink执行`uninstall`
只删除链接，不删除它指向的检出。

## 第一方 Programs（gui / research / wiki）

| Program | Release 状态 | 说明 |
|---|---|---|
| [Research Agent](https://github.com/Fzkuji/Research-Agent-Harness) | 已包含 | product manifest 记录固定 source commit；builder 安装声明的 PDF 依赖，runtime manifest 记录解析后的 distributions。 |
| [Wiki Agent](https://github.com/Fzkuji/Wiki-Agent-Harness) | 已包含 | product manifest 记录固定 source commit；builder 安装声明的依赖，runtime manifest 记录解析后的 distributions。 |
| [GUI Agent](https://github.com/Fzkuji/GUI-Agent-Harness) | 已包含 | Program 会注册，并附带 GPA detector 权重。产品 runtime 不含 PyTorch、OpenCV 或 EasyOCR。 |

release 用户不执行 `programs install all`、首次启动 Program wizard 或 GUI
harness 资产 installer。开发者可以用 editable checkout 替换第一方 Program，
或配置不同的 OCR/Browser backend；这些 overlay 增加开发行为，但不改变 product manifest。

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

`<APPLICATIONS>` 是 OpenProgram 登记的外部 Program 文件夹：

```bash
python -c "import openprogram,os;print(os.path.join(os.path.dirname(openprogram.__file__),'programs','applications'))"
```

```bash
git clone <repo-url> /path/to/Harness-Name
openprogram programs install file:///path/to/Harness-Name
# 重启 OpenProgram
```

自动发现会拾取 `<APPLICATIONS>` 中任何已登记且满足契约的目录——这就是安装命令
所自动化的全部内容。

</details>

## 开发者配置（开发你正在编写的 harness）

把你的工作检出做成 symlink，而不是克隆一份副本：

```bash
ln -s /path/to/your/Harness-Checkout "<APPLICATIONS>/Harness-Checkout"
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

- **受支持的 CLI/server release 主机包括 macOS、Linux 和 Windows x86_64/arm64。**
  Windows Desktop 同时支持两种架构；Windows 沙箱命令执行使用可选的 WSL2 与 bubblewrap。
- **这些 Program 命令需要可变环境。** source-development checkout 可以使用；
  CLI release 仅在对应 release notes 明确支持 Program mutation 时使用。
  packaged desktop 在 Program 拥有隔离的外部环境前会拒绝这些修改命令。
- **受支持的可变环境无需 symlink**——安装器默认会把真实 checkout 登记到 `<APPLICATIONS>`。
- **harness 在自身代码中仍可以是平台相关的**（例如，桌面 GUI
  harness 可能只实现 macOS / Linux 后端）。
  在受支持主机上能否安装、每个函数能否运行，取决于 harness 声明的依赖和
  平台支持；需要检查它的 README。
- **编码 / 路径：** OpenProgram 自身的工具链全程基于 UTF-8 和
  `os.path`；一个表现良好的 harness 也应如此。

## 故障排查

| 现象 | 原因 / 修复 |
|---|---|
| 重启后 harness 函数没有出现 | 文件夹不匹配契约——确认 `<pkg>/agentics/__init__.py` 存在并导出 `AGENTIC_FUNCTIONS`。用 `OPENPROGRAM_DEBUG_REGISTRY=1` 运行。 |
| 安装时出现 `[!] … no package with an agentics/__init__.py was found` | 同上——该仓库不满足契约（第二部分）。 |
| harness 自身依赖出现 `ModuleNotFoundError` | Program 环境准备失败——重新执行 `openprogram programs install <source>` 并检查错误。 |
| harness 内部的导入失败（`from <pkg>.x import y`） | package 目录的命名与导入根不一致，或缺少 `__init__.py`。package 文件夹名必须等于导入名。 |
| 现有dev symlink没有加载 | 运行一次`openprogram programs install <Git来源>`完成校验与登记；安装器不会修改链接目标。 |
| harness 在 Windows 上无法安装或运行 | 检查 harness README 与 dependency marker。OpenProgram CLI/server 受支持，但单个 harness 仍可能只提供 macOS/Linux backend。 |

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
