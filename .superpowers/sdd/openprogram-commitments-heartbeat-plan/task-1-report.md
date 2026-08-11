# Task 1 report: commitments + heartbeat

## 状态

`DONE_WITH_CONCERNS`

实现提交：`ab54b270c790c2b519fafc11f4d60e815381f2b0`
（`feat(memory): add commitment heartbeat lifecycle`）。
本报告作为后续 report-only commit 提交；由于提交对象不能在自身内容中记录自身
SHA，最终 report commit SHA 以任务最终回复为准。

## 实现范围

- 在现有 memory writer 内增加 `record_commitments`，不增加第二套提取服务。LLM
  只提供义务/关闭语义、Source ref 和 exact quote；Runtime 校验可信当前批次
  Source、quote、日期、speaker、ID 和 transition provenance。
- 使用 `commitments.jsonl` 持久化 `open`/`done`/`dismissed` 生命周期、通知档位、
  closure Source/quote 和 Runtime transition timestamp。文件随现有 memory staged
  transaction、workspace lock、atomic replace 和 Git candidate 提交。
- ID 由 `Source ref + exact source_quote` 派生，不依赖 LLM 改写、due 解释或输入
  顺序；覆盖同一 Source 的多条不同证据、重复提取、重排和已关闭记录去重。
- tolerant read 让 status/heartbeat 隔离非法 JSONL 行并计入 `invalid`；heartbeat
  更新合法通知状态时原样保留非法行。upsert/transition 使用 strict read，发现
  非法行即拒绝变更，避免静默数据丢失。
- 在现有 `cron-worker` tick 中增加 deterministic heartbeat：`daily` 默认本地
  09:00、`hourly` 每小时 00 分、`off`；quiet hours 默认本地 23:00-08:00；
  invalid config 跳过；due 和 overdue:7 各成功发送一次；失败不消费档位。
- 从 Source 的来源 session 经现有 `SessionDB` channel binding 解析 channel、
  account、peer；binding 变化在发送时生效；同 target 聚合；无 target 记录保留
  可见；测试全部替换 outbound，不发送真实消息或使用凭据。
- `memory_status`、CLI status、owner API 和 Memory Web 展示 bounded projection。
  model-facing 投影不包含 workspace path、channel identity、任意 JSONL 扩展字段或
  exact quotes，只保留可审计 Source refs。Memory Web 增加 owner-only confirmed
  done/dismissed 控件，并复用同一 revision-checked `MemoryWorkspace.update`；stale
  revision 返回 409，malformed/non-object JSON 返回结构化 400。
- 更新 config/CLI/memory 文档和英文、中文、HTML 三份设计文档；三份设计文档均按
  “OpenProgram 当前现状 → 其他项目如何设计 → OpenProgram 后续计划”排列，并对
  OpenClaw Heartbeat、已退役 OpenClaw inferred commitments、LangMem、LangSmith
  cron jobs 明确记录采用、修改、拒绝及理由，且仅链接官方文档或官方仓库。
- 更新 feature matrix 的 commitments/heartbeat、session archive、bundled
  commit-push-pr skill、Co-Authored-By trailer、agent depth/fanout 状态；修正候选
  批次、快照边界和证据说明，并机械重算全部计数和分数。

## 关键设计决定

1. 语义与信任边界分离：LLM 不生成 ID、speaker、可信 Source、日期格式、cadence、
   quiet hours、路由、权限、通知状态或写入时序。
2. 稳定证据而非 LLM 文本作为去重锚点：Runtime 要求 exact source substring，
   以 Source ref + quote hash 派生 ID；同一证据的不同摘要不会新增记录。
3. writer closure 与 owner manual closure 使用同一存储函数但不同 provenance：
   writer 必须提供当前批次可信 closure evidence；owner path 由 Runtime 固定写入
   `owner/manual`，客户端不能选择 provenance。
4. 不新增 scheduler、storage、task manager 或 delivery journal。Heartbeat 复用
   cron worker、SessionDB binding 和 outbound；状态复用 memory workspace。
5. 外部发送与本地原子状态之间维持诚实的 at-least-once 边界。所有 outbound
   adapter 没有统一 idempotency key 前，不声称 exactly-once。
6. 非法持久行对读和写采用不同策略：读隔离以保持其余提醒可用，写严格拒绝；
   heartbeat 的合法行局部替换保留非法原始行。
7. `commitments.jsonl` 是新增 additive capability；文件缺失等价于空集合，因此无
   一次性 migration。优先级、子任务、项目、自主执行、timezone selector、可配
   overdue interval 明确不在当前功能边界。

## 修改文件

- Runtime/config/cron：`openprogram/config_schema.py`、
  `openprogram/functions/tools/cron/worker.py`、
  `openprogram/proactive/heartbeat.py`、
  `openprogram/memory/runtime/commitments.py`、
  `openprogram/memory/runtime/state.py`
- memory writer/transaction/status：
  `openprogram/functions/tools/memory/memory.py`、
  `openprogram/memory/management/api.py`、
  `openprogram/memory/management/block_views.py`、
  `openprogram/memory/management/tools.py`、
  `openprogram/memory/management/workspace.py`、
  `openprogram/memory/prompts/write.py`、
  `openprogram/memory/retrieval/inspect.py`、
  `openprogram/memory/writing.py`、`openprogram/memory/README.md`
- owner Web/API：`openprogram/webui/routes/memory.py`、
  `web/components/memory/index.tsx`、
  `web/components/memory/memory-page.module.css`、
  `web/components/memory/status.ts`、`web/components/memory/types.ts`、
  `web/scripts/check-memory-status.mjs`
- tests：`tests/unit/test_memory_commitments.py`、
  `tests/unit/test_memory_routes.py`、
  `tests/unit/test_memory_runtime_config.py`
- docs：`docs/reference/config.md`、`docs/reference/config.zh.md`、
  `docs/reference/cli.md`、`docs/reference/cli.zh.md`、
  `docs/reference/design/feature-matrix.html`、
  `docs/reference/design/proactive/commitments-and-heartbeat.md`、
  `docs/reference/design/proactive/commitments-and-heartbeat.zh.md`、
  `docs/reference/design/proactive/commitments-and-heartbeat.html`

## 测试与检查记录

### TDD 证据

- `python -m pytest tests/unit/test_memory_commitments.py -q`（接手基线）：
  `15 passed`。
- 初始新增 backend tests：`5 failed, 24 passed`，失败分别确认 status、cron route、
  invalid config、Git candidate、config defaults 尚未集成。
- `node --no-warnings --experimental-strip-types web/scripts/check-memory-status.mjs`
  初始 RED：commitment helper import 不存在。
- 初始实现后同组 backend：`29 passed`；Node script：passed。
- owner Web transition RED：route tests `2 failed`（404）；Node static check 因 endpoint
  wiring 缺失失败。实现后 route tests `2 passed`，Node static check passed。
- 稳定证据/provenance/schema 首轮 RED：
  `python -m pytest tests/unit/test_memory_commitments.py tests/unit/test_memory_routes.py -q`
  得到 `9 failed, 42 passed`；首轮实现后 `51 passed`。
- tolerant/strict read、projection、malformed Web payload 回归 RED：5 个测试全部失败；
  实现后该组 `5 passed`。
- 最终 focused：
  `python -m pytest tests/unit/test_memory_commitments.py tests/unit/test_memory_routes.py -q`
  → `54 passed, 6 warnings`。

### 最终验证

- `python -m pytest tests/unit/test_memory_commitments.py tests/unit/test_memory_runtime_config.py tests/unit/test_cron_command.py tests/unit/test_channel_questions.py tests/unit/test_memory_writer_status.py tests/unit/test_memory_routes.py tests/unit/test_memory_scope.py tests/unit/test_memory_writing.py tests/unit/test_memory_stage_cleanup.py -q`
  → `175 passed, 6 warnings`。
- `python -m pytest tests/unit -q`
  → `2723 passed, 8 skipped, 10 warnings in 165.15s`。
- `python -m ruff check openprogram/config_schema.py openprogram/functions/tools/cron/worker.py openprogram/functions/tools/memory/memory.py openprogram/memory/management/api.py openprogram/memory/management/block_views.py openprogram/memory/management/tools.py openprogram/memory/management/workspace.py openprogram/memory/prompts/write.py openprogram/memory/retrieval/inspect.py openprogram/memory/runtime/commitments.py openprogram/memory/runtime/state.py openprogram/memory/writing.py openprogram/proactive/heartbeat.py openprogram/webui/routes/memory.py tests/unit/test_memory_commitments.py tests/unit/test_memory_routes.py tests/unit/test_memory_runtime_config.py`
  → `All checks passed!`。
- `python -m ruff format openprogram/memory/runtime/commitments.py openprogram/proactive/heartbeat.py tests/unit/test_memory_commitments.py`
  → `2 files reformatted, 1 file left unchanged`；仅格式化本任务新增文件。
- `python -m ruff format --check <全部 touched Python files>` 的早期检查：
  `14 files would be reformatted`。这些是仓库既有 tracked 文件的基线格式差异；
  未保留整文件机械格式化，最终以 Ruff lint、focused/full tests 和
  `git diff --check` 验证最小语义 diff。
- `npm run lint -- --file components/memory/index.tsx --file components/memory/status.ts --file components/memory/types.ts`
  → `No ESLint warnings or errors`。
- `npm run check:memory-status`
  → `memory writer status checks passed`。
- `npm run build`
  → Next.js production build compiled successfully，27/27 static pages generated。
- `npm run lint`（full Web）→ failed on pre-existing unrelated files，包含
  `components/animated-icons/index.tsx` unused imports、
  `components/center-tabs/file-tab-pane.tsx` unused variable、provider settings
  `no-explicit-any` 等；本任务 targeted files 无错误。
- `python -m tools.docs_site.checklinks` → `0 broken link(s)`。
- `python -m tools.docs_site.checklang` → failed with
  `61 Chinese line(s) in default-English pages`，全部位于既有
  `capabilities/goal.md` 和 `superpowers/plans|specs`，不在本任务三份设计文档。
- feature matrix mechanical parser →
  `{'rows': 160, 'columns_ok': True, 'openprogram_score': 79.0, 'gaps': 72, 'only': 7}`。
- stale matrix text check：未发现 `76/13/63`、旧 score、`414aa4de`、把已实现
  commit-push-pr/archive 继续列为候选的旧文本。
- design heading order check：Markdown、中文 Markdown、HTML 均严格为
  current state → comparable projects → follow-up plan。
- `git diff --check` → passed，无 whitespace error。
- `npm ci` → installed 447 packages；npm 对既有 lockfile dependency graph 报告
  8 个 high severity audit findings，未修改 lockfile。

## 未解决 concern

1. Delivery 是 at-least-once。channel 成功、notification step 原子写入前进程退出
   可能导致后续重复；现有 outbound adapters 没有统一 idempotency-key contract。
2. 仓库 full Web lint 仍被上述既有、非本任务文件错误阻塞；本任务 targeted lint
   和 production build 均通过。
3. docs language check 仍被 61 条既有 default-English page 中文内容阻塞；本任务
   English design document 全英文，link check 通过。
4. npm 对既有依赖图报告 8 个 high severity findings；本任务未扩大依赖范围，未
   修改 package/lockfile。
5. tracked Python 基线不是 Ruff-format clean；本任务未把无关整文件格式化混入
   功能提交，Ruff lint、diff check 和全部单元测试通过。
