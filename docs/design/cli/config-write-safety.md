# 配置写入安全 —— 原子化的 `update_config`

状态：**面向 web 的写入已落地（步骤 1–3）** · 步骤 4 遗留 · 负责人：core/config · 创建于：2026-06-04

优化路线图第 5 项。承接此前的配置 IO 收口工作：该工作让 webui 委托给
`setup._read_config`/`_write_config` 并强制 0o600。

## 1. 问题

`config.json` 由分离的 `_read_config()` … `_write_config()` 调用进行修改，
中间夹着改动 —— 一次非原子的 read-modify-write —— 来自多个位置，
彼此之间**没有共享锁**：

- `config_schema.set_setting`（`config_schema.py:253,288`）—— TUI `/config` + web
  System 标签页 + `openprogram config`。
- `routes/config.py:save_config` —— web 端的 "Save API keys" 表单（读取 config、
  合并 `api_keys`、写回）。
- `setup.py:set_ui_ports` / `write_search_default_provider`（`135,176`）。
- `_setup_sections/*` —— `openprogram setup` 向导。

`storage.py` 已经用一个模块级 `threading.Lock`（`_cache_lock`）对它的
**providers** 段写入做了串行化，但该锁是该模块私有的 ——
上面那些写入方并不会去拿它。

于是两个并发写入方发生竞争，后写的那次覆盖掉先写的那次：
- 进程内：一次 TUI 工具开关（`set_setting`）和一次 web api-key 保存
  （`save_config`）都跑在 **worker** 进程里；没有共享锁，其中一个就会
  覆盖另一个。
- 跨进程：`openprogram config` / `openprogram setup` 是**独立的进程**，
  在 worker 写同一个文件时也在写它 —— `threading` 锁无法跨进程感知。

## 2. 设计

在 `setup.py` 中提供一个原子入口：

```python
_config_write_lock = threading.Lock()          # 进程内（worker 线程）

def update_config(mutator: Callable[[dict], None]) -> dict:
    """对 config.json 做原子的 read-modify-write。同时持有一个进程内锁和一个
    跨进程文件锁（config.json.lock，经由 filelock），读取当前 config，原地
    应用 mutator(cfg)，写回（0o600），并返回它。这是修改 config 某一部分的
    唯一正确方式 —— 绝不要分开调用 read_config() + write_config()，那样会竞争。"""
    with _config_write_lock:
        with FileLock(str(get_config_path()) + ".lock", timeout=10):
            cfg = _read_config()
            mutator(cfg)
            _write_config(cfg)
            return cfg
```

- `filelock`（3.16.1，已是依赖项）提供跨进程锁；`threading.Lock` 提供进程内
  锁（filelock 在单个进程内是可重入的，但线程锁让 read-modify-write 这段临界区
  在 worker 的各线程之间也保持原子）。
- `_read_config` / `_write_config` 仍保留给只读 / 整体替换使用；只有
  read-modify-write 迁移到 `update_config`。

## 3. 迁移

1. **（已完成，935685c4）** 给 `setup.py` 加上 `update_config` 加一个单元测试
   （两个“并发”的 mutator 被串行化；结果同时反映两者）。
2. **（已完成，1c21d43d）** 把两个面向 web 的竞争方 ——
   `config_schema.set_setting`（`_set_at` 分支和 `tools.disabled` 分支都算）
   以及 `routes/config.py:save_config`（api_keys 合并）—— 迁移到 `update_config`。
3. **（已完成，0cc67aed）** 把 `setup.py` 自己的 `set_ui_ports` /
   `write_search_default_provider` 迁移到 `update_config`。（面向 web 的 config
   写入路径现已全部原子化。）
4. **（遗留）** 迁移 `_setup_sections/*` 中 `openprogram setup` 向导的写入方，
   并让 `storage.py` 的 providers 段写入也走 `update_config`，从而做到跨进程安全
   （它们今天在进程内是安全的，靠的是私有的 `_cache_lock`；缺口在于并发的
   CLI/向导写入）。这两者都偏 CLI 侧 / 概率低于上面已经关闭的 web 竞争。

每一步：重启 worker、`/healthz`、从 web 端保存一个设置 + 一个 api key、
确认两者都持久化（没有被覆盖）、测试通过。

## 4. 非目标

不是 config schema/校验方面的改动（那是 `config_schema` 的事）；也不是要弃用
JSON。只是让每一次写入都原子且互斥。
