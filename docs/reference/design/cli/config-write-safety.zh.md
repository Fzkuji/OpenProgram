# 配置写入安全 —— 设计

> 本文是 `config.json` 如何被安全修改的权威设计：唯一的原子入口、它背后的
> 两把锁，以及哪些写入方必须经由它。关于「一个设置项是什么」，见
> [`cli-redesign.zh.md`](cli-redesign.zh.md)。

## 1. 危险所在

`config.json` 是单个 JSON 文件，被分散在多个界面、多个进程中的写入方修改：

- `config_schema.set_setting` —— TUI 的 `/config` 面板、web 的 System 标签页，
  以及 `openprogram config`。
- `routes/config.py:save_config` —— web 端的 "Save API keys" 表单。
- `setup.py:set_ui_ports` 与 `write_search_default_provider`。
- `_setup_sections/*` —— `openprogram setup` 向导。
- `storage.py` —— providers 段。

对共享文件做 read-modify-write，只有在一把覆盖整个序列的锁之下才是正确的。
没有这把锁，两个写入方会各自读到相同的起始状态、各自应用自己的改动，后写的
那次丢弃先写的那次。这里两种并发范围都会出现：

- **进程内。** TUI 的工具开关与 web 的 api-key 保存都跑在 worker 进程里，
  位于不同线程。
- **跨进程。** `openprogram config` 与 `openprogram setup` 是独立进程，会在
  worker 写同一个文件的同时写它。`threading` 锁跨进程不可见，帮不上忙。

模块私有的锁同样解决不了问题：`storage.py` 用 `_cache_lock` 把自己的
providers 写入串行化，但没有别的写入方会去拿这把锁，所以它只能保护该模块
不与自身冲突，仅此而已。

## 2. 唯一的原子入口

`setup.update_config` 是修改配置局部的唯一正确方式。它接收一个 mutator，
在整个 read-modify-write 期间持有两把锁，并返回修改后的配置：

```python
_config_write_lock = threading.Lock()          # in-process (worker threads)

def update_config(mutator: Callable[[dict], None]) -> dict:
    """Atomic read-modify-write of config.json. Holds an in-process lock AND a
    cross-process file lock (config.json.lock, via filelock), reads the current
    config, applies mutator(cfg) in place, writes it back (0o600), returns it.
    The ONLY correct way to change part of the config — never read_config() +
    write_config() separately, which races."""
    with _config_write_lock:
        with FileLock(str(get_config_path()) + ".lock", timeout=10):
            cfg = _read_config()
            mutator(cfg)
            _write_config(cfg)
            return cfg
```

两把锁都是必需的，没有一把是多余的。`filelock` 覆盖跨进程的情形。
`threading.Lock` 覆盖 worker 自己的线程：`filelock` 在同一进程内是可重入的，
仅靠它会让两个 worker 线程在临界区内交错。

mutator 这种形式正是让该 API 难以被误用的原因。调用方拿不到跨越锁边界的
配置 dict，因此无法在稍后把它写回去——它在 mutator 之外从不接收这样一个 dict。

`_read_config` 与 `_write_config` 保留下来，用于只读访问与整体替换。
只有 read-modify-write 需要经由 `update_config`。

## 3. 范围

本设计只涉及写入的原子性。schema 定义与取值校验属于 `config_schema`
（[`cli-redesign.zh.md`](cli-redesign.zh.md) 第 3 节），存储格式仍是 JSON。
这里要确立的性质是：每一次写入都是原子的，且与其他任何写入互斥。

## 附录：实现状态

`update_config` 已存在于 `setup.py`，并有一个单元测试断言两个并发 mutator
会串行执行、且结果同时反映两者的改动。

已迁移：`config_schema.set_setting`（`_set_at` 与 `tools.disabled` 两个分支）、
`routes/config.py:save_config`（api_keys 合并），以及 `setup.py` 自身的
`set_ui_ports` / `write_search_default_provider`。所有面向 web 的配置写入路径
都已原子化。

尚未迁移：`_setup_sections/*` 向导的写入方，以及 `storage.py` 的 providers 段
写入。两者目前在进程内是安全的（后者依靠 `_cache_lock`）；未闭合的缺口是来自
另一个进程的 CLI 或向导并发写入。
