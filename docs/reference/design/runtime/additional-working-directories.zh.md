# 会话多工作目录（Additional Working Directories）设计

一个会话除主工作目录（绑定项目路径）外，可挂任意多个"额外工作目录"。语义对齐 Claude Code 的 "Add another folder"（`additionalWorkingDirectories`）：**额外目录只扩权限围栏和模型认知，不改变主 cwd，不改变会话存储位置**。

两者在"可变程度"上正相反。主目录在会话第一轮定格，此后只为修复磁盘上消失的目录而移动——见 [session/operations.zh.md](session/operations.zh.md)"主项目绑定"。额外目录则在会话生命周期内随时增删，每次改动下一轮生效。

项目路径 → 会话 cwd 的链路由 `project_workdir_for` 承担，围栏部分见 `docs/reference/design/runtime/permission-model.zh.md` §3.5。

---

## 1. 语义（做什么、不做什么）

| 维度 | 主工作目录 | 额外工作目录 |
|---|---|---|
| 模型 cwd（system prompt、`--cd`、工具 ContextVar） | ✅ 项目路径 | ❌ 不变 |
| 会话仓库/产物存储位置 | ✅ `<project>/.openprogram/sessions/` | ❌ 不变 |
| acceptEdits 围栏白名单（`check_path_safety` 的 `working_dirs`） | ✅ | ✅ 加入 |
| system prompt 告知模型 | ✅ "Current working directory" | ✅ 新增一行列出 |
| 存储 | 项目绑定（project_store） | 会话级 `SessionRunConfig.additional_working_dirs`（session meta，schemaless） |
| 会话中途可改 | ❌ 第一轮定格（relocate 只修复目录缺失） | ✅ 自由增删 |

不做（对齐 Claude Code 也不做或本项目无载体）：
- 不从额外目录加载 CLAUDE.md / 项目级 settings —— 权限规则仍只跟主项目走。
- 不做目录级只读/读写分级 —— 白名单是二值的，进了就是可写安全区。
- 不做全局（跨会话）额外目录 —— 载体是会话 meta，跨会话需求用项目解决。

扩展点（未来要做时从哪下手）：条目从 `str` 升级为带属性对象时，只需改 `_as_str_list` 的解析与 `check_path_safety` 的消费端，存储 schemaless 无迁移；MCP roots / 多根 IDE 工作区如需接入，同一字段即是唯一权威。

## 2. 数据链路

`additional_working_dirs` 从会话配置一路流到路径围栏：

```
UI / ws action
   ↓
SessionRunConfig.additional_working_dirs        session_config.py:61（字段）:79（load）:127（save）
   ↓ load_session_run_config
TurnRequest.additional_working_dirs             dispatcher/types.py:112
   ↑ 填充：webui/_execute/chat.py:259、channels/_conversation.py:243
   ↓
_path_is_safe → check_path_safety(path, dirs)   permissions/policy.py → programs/tools/files/file_safety.py
```

`save_session_run_config(..., additional_working_dirs=...)` 接受该参数，传 None 表示不改动，聊天路径因此不会误清已有目录。

## 3. 各环节设计

### 3.1 后端：围栏基准

围栏的工作目录集在 `openprogram/agent/permissions/policy.py` 组装：

```python
from openprogram.worktree.context import current_worktree_path
work_dirs = [current_worktree_path() or os.getcwd(),
             *getattr(req, "additional_working_dirs", [])]
```

基准取 `current_worktree_path()`（dispatcher 每 turn 把真实 cwd——worktree 或项目路径——绑进这个 ContextVar，见 `dispatcher/__init__.py:387-403`），进程 `getcwd` 只作回落。这与 system prompt 的 cwd 同源（`_model_tools.py:322`）——模型被告知的 cwd 和围栏认可的 cwd 永远是同一个目录。若二者不一致，模型改项目内文件会被围栏判为"工作区外"，acceptEdits 不放行、反复弹审批。`worktree.context` 只依赖 stdlib，无循环 import。

### 3.2 后端：ws action `set_working_dirs`

落 `openprogram/webui/ws_actions/session.py`（与其它会话配置 action 同居）。**整表替换**语义（前端算好增删后发完整列表）——幂等、无"重复添加/删不存在"的边界分支：

```python
async def handle_set_working_dirs(ws, cmd: dict):
    """整表替换会话的额外工作目录。dirs 逐条 expanduser + 必须是存在的目录,
    非法条目整帧拒绝(error 帧带原因),不做部分写入。"""
    # 校验通过 → save_session_run_config(session_id, agent_id=..., additional_working_dirs=dirs)
    # → 广播 {"type": "working_dirs", "data": {"session_id", "dirs"}}
```

校验规则：`Path(d).expanduser()` 后 `is_dir()`；存的是 expanduser 后的绝对路径字符串（不 realpath——用户看到自己选的路径，realpath 归一交给 `check_path_safety` 消费端，它本来就做）。

### 3.3 后端：`session_loaded` 回带 + 首条消息携带

- `ws_actions/session.py:676-681` 的 `data.settings` 带上 `additional_working_dirs`——刷新/换端后前端据此恢复列表。
- `ws_actions/chat.py` 的 `handle_chat`：`cmd.get("additional_working_dirs")` 非 None 时传入 `save_session_run_config`。这是草稿会话（尚无 session_id）在首条消息落地目录的唯一通道，与 `permission_mode` 等既有字段同一模式。

### 3.4 后端：system prompt 告知模型

`with_tool_runtime_prompt` 接受可选参 `additional_working_dirs: list[str] | None = None`，dispatcher 调用处传 `req.additional_working_dirs`。在 "Current working directory" 行后列出（有则出现，空则无此行）：

```
- Additional working directories (equally writable): /a, /b
```

`internals/_model_tools.py` 与 `agent/_model_tools.py` 两份副本保持一致，遵循文件头的 "kept in sync" 约定。

### 3.5 前端：项目 chip 右侧的一目录一 chip

composer 的 `envChips` 行从左到右就是会话的目录集合：`<ProjectBadge />` 是定格的主目录，其后是 `<WorkingDirChips />`——每个额外目录一个 chip，末尾一个纯图标的添加按钮（`apps/web/components/chat/top-bar/working-dir-chips.tsx`）。

```
[📁 my-project]  [📂 foo ✕]  [📂 bar ✕]  [＋]
```

- 添加按钮打开选择菜单：最近项目（点一下即挂载该项目路径），以及"选择文件夹…"，后者调 `POST /api/pick-folder`（现成原生选择器，桌面端同样走它）。新列表以 `set_working_dirs` 发出，同时**乐观更新**本地状态（即时反馈原则），`working_dirs` 广播帧到达后以后端为准。
- chip 上的 ✕ 用同一 action 发去掉该项的列表。
- 会话无 id（草稿）时只更新本地状态，首条 chat 帧携带（§3.3）。
- 两个操作在整个会话期内始终可用——主目录定格不牵连它们。

状态放 session-store：`additionalWorkingDirsBySession: Record<string, string[]>`（完整词，不缩写），来源三处——`session_loaded.data.settings`、`working_dirs` 广播、乐观更新。不进 `ComposerSettings`/localStorage：这是服务端持久化的会话数据，不是端上偏好。

旁边的项目 chip 承载主目录的状态，包括它的警示形态。`list_projects` 按项目返回 `path_missing`；当会话自己的项目带上这个标记时，chip 切到橙色警示配色、folder 图标换成 lucide 的 `AlertTriangle`，菜单里出现"定位文件夹…"——即 [session/operations.zh.md](session/operations.zh.md) 描述的 relocate 修复。

### 3.6 测试

跟随既有文件风格：
- `tests/unit/store/test_session_config.py`：`additional_working_dirs` save/load 往返（含 None 不动、`_as_str_list` 清洗）。
- `tests/unit/security/test_permission_rules.py`：`_path_is_safe` 三例——额外目录内放行、目录外拦、ContextVar 绑定的项目 cwd 内放行（monkeypatch `current_worktree_path`）。
- `tests/unit/webui/test_ws_working_dirs.py`：ws action 的合法写入+广播、非目录整帧拒绝、`session_loaded` 回带。
- `tests/unit/store/test_session_main_workdir.py`：主目录那侧的相反规则——定格、目录缺失时的解析、relocate 记录节点——外加把两者串起来的那条：主目录已定格的会话上照样增删额外目录。

## 4. 数据流总览

```
WorkingDirChips ＋ 按钮
   │ POST /api/pick-folder（原生对话框）
   ▼
wsSend set_working_dirs {session_id, dirs}     （草稿会话 → 随首条 chat 帧）
   ▼
handle_set_working_dirs：校验 → save_session_run_config → 广播 working_dirs
   ▼
session meta（schemaless，无迁移）
   ▼ 每 turn load_session_run_config
TurnRequest.additional_working_dirs
   ├─→ _path_is_safe：[current_worktree_path() or getcwd(), *dirs] → check_path_safety
   └─→ with_tool_runtime_prompt：system prompt 列出额外目录
```

## 5. 关键性质（改动时守住）

以下几条是设计的红线，任何改动都要保持：

- 额外目录**只入围栏与提示词**——任何把它接到 cwd 切换、存储位置的改动都违反 §1 语义表。
- 围栏基准与 system prompt 的 cwd 必须**同源**（`current_worktree_path()` 优先）——模型认知与权限判定不一致会造成"模型以为能写、围栏拦下"的循环审批。
- `set_working_dirs` 是整表替换且校验失败整帧拒绝——不存在部分写入的中间态。
- 存储 schemaless（session meta），旧会话读回缺字段 → 空列表，无迁移。
- 额外目录在整个会话期内保持可编辑。定格只属于主目录；把它扩到这份列表上，会话中途就再没有扩围栏的办法了。
- 目录缺失的主项目解析为 `None`，绝不解析成另一个目录。换成默认项目的家目录，等于把一个看着合理、用户却从未选择的 cwd 交给模型。
