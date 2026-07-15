# 多实例与 profile

这页说明如何在同一台机器上并行运行多个互不干扰的 OpenProgram 实例——常见场景是一个稳定实例日常用，一个开发实例改代码。

## profile：隔离状态目录

默认所有状态（config / sessions / logs / memory）都在 `~/.openprogram/`。指定 profile 后改存 `~/.openprogram-<name>/`，两个实例互不共享任何数据：

```bash
openprogram --profile dev            # CLI 全局参数
OPENPROGRAM_PROFILE=dev openprogram  # 或环境变量，二者等价（环境变量优先）
```

`--profile` 是全局参数，放在子命令前，对所有子命令生效：`openprogram --profile dev sessions list`、`openprogram --profile dev restart` 等。

## 端口：每个实例一对

默认前端 18100、后端 18109。改端口有三种方式：

```bash
# 1. 持久化到该 profile 的配置（推荐，之后启动不用再带参数）
openprogram --profile dev ports --backend 18209 --frontend 18200

# 2. 环境变量，覆盖本次运行
OPENPROGRAM_BACKEND_PORT=18209 OPENPROGRAM_WEB_PORT=18200 openprogram web

# 3. 命令行参数，只对这一次 `openprogram web` 生效
openprogram web --port 18209 --web-port 18200
```

`ports` 的设置写进当前 profile 的 config，所以每个 profile 记住自己的端口。

## 示例：稳定 + 开发双实例

稳定实例用默认 profile 和默认端口，开发实例用 `dev` profile 和 18200/18209：

```bash
# 稳定实例（日常使用）
openprogram web                        # http://localhost:18100

# 开发实例：先把端口写进 dev profile（一次性）
openprogram --profile dev ports --backend 18209 --frontend 18200

# 之后每次这样启动
openprogram --profile dev web          # http://localhost:18200
```

两个实例各有自己的会话、配置、日志和后台 worker；`openprogram status` 看的是默认实例，`openprogram --profile dev status` 看开发实例。
