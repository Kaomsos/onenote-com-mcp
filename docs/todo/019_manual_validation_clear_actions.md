# 019：Manual Validation 受控 Clear Actions

> ID：019
> 状态：已完成
> 优先级：P2
> 类型：验证基础设施 / 受控本地清理
> 更新日期：2026-08-12

## 实施进展（2026-08-12）

已完成实现、纯合同与文档同步：

- 公开 CLI 为唯一 `clear` 分组及 `runs`、`cache`、`all` 三个子 action；旧的连字符顶层名称未注册；
- maintenance runtime 独立于 Scenario registry/`all`，不会启动 MCP、调用 mutation/close 或创建 run directory；
- dry-run 零 managed write/delete，并用一次只读 OneNote hierarchy 调用规范化当前已打开 Notebook 的 ID、file URI、`.onetoc2` 与实际目录；snapshot 不完整时所有目标 fail closed；
- 真实执行只允许交互式前台 stdin，安全计划完成后在后续提示中要求用户现场输入 `CLEAR-RUNS`、`CLEAR-CACHE`、`CLEAR-ALL` 动作绑定确认值，CLI 不接受 confirmation 参数；执行期间持有短时 open lock，目标逐项验证固定 root、ownership、plain tree、无 reparse point和实际路径未打开，先写 pending receipt，再精确删除并写 final receipt/summary；
- run 清理不假定 Notebook directory 数量；cache 清理核对 marker、index、typed identity、entry metadata、全部 role template path 与 byte inventory，不读取 run lease；legacy working-leases 和 owned staging 作为独立精确目标处理；
- partial result 保留独立的 deleted/refused/failed 明细；成功 receipt 只有在完整 target evidence 已进入 durable summary 后才自动收敛，pending/failed/unbound receipt 保留；cache clear 同时移除无 payload tombstone index 项并逐层清理可证明为空的 typed scaffold；
- 聚焦 maintenance/cache/lifecycle/CLI 合同通过；三个真实目录 dry-run 均获得完整 OneNote open-path snapshot，分别规划 84 个 run、28 个 cache 目标和合计 112 个目标，均无 refused/failed。Agent 未执行任何真实 clear；
- 用户随后本人在交互式前台终端执行 `clear runs` 与 `clear cache`：两份 root-level summary 分别记录 84/84 与 28/28 目标删除成功，均 `ok=true`、`refused=0`、`failed=0`。这满足 human-gated destructive 验收；其后观察到的 112 个成功 receipt、56 层空 cache scaffold 和 27 条 tombstone index 残留推动了上述自动收敛合同。

代码、文档、纯合同、只读真实 dry-run 与用户真实 clear 证据均已闭合，本 TODO 标记为已完成。

## 背景

`tests/manual_validation/run.py` 当前只有具名验证 Scenario 和特殊批处理入口 `all`，没有清理 `.local-validation/` 中历史 run artifact 与 fixture cache 的顶层维护动作。长时间真实验证后，本地会同时积累：

- `run-*` 下的 evidence、lifecycle lease、working Notebook directory 与 Notebook Copy directory；
- `fixture-cache/` 下按 `(fingerprint, template_instance_id)` 管理的 immutable template entry、index 和 tombstone；旧版本遗留的 `working-leases/` 只作为待清理的 legacy metadata，不再被 runtime 读取；
- cold build 或 interactive bootstrap 同一 run 中已关闭的构建源 bundle，以及发布后重新 materialize 的 working bundle。

维护实现不得假定“一个 run 只有一个 Notebook directory”。当前单 role cache hit 通常只有一个 working directory；单 role cold build/bootstrap 可能同时保留构建源和 materialized working 两个目录；双 role cold build 可能有四个。它们都属于精确 run root，不应通过 Notebook 数量、名称猜测或 run–cache fingerprint 关系决定是否可清理。

现有 Runner 的正常 cache 路径不会直接打开 template：cache runtime 只 opaque-copy 已关闭的 disposable Notebook bytes，lifecycle 只打开新的 run-scoped working path，并回读证明实际路径不等于任何 template path。不过，用户仍可能在 Runner 外手动打开目录；清理动作必须以当前 OneNote 实际打开路径为最终真相，不能只相信历史 `opened_template=false`、发布前 Notebook ID 或 lease state。

## 已确定的产品决策

新增一个 `clear` 顶层 maintenance 分组，且仅新增三个子 action：

```text
clear runs
clear cache
clear all
```

三者不是 Scenario，不进入 `SCENARIO_REGISTRY` 或 `all`，不创建 run directory、不创建 Notebook、不启动 scenario MCP，也不执行 OneNote mutation。它们只允许使用窄、只读的 OneNote hierarchy/path probe 判断受管 Notebook directory 是否仍被打开；真实清理只能由用户在前台终端显式启动，Agent、pytest、CI、hook、timer、watcher 和后台任务只能执行 `--dry-run`。

本 TODO 采用放宽后的清理口径：

- run 完成后不会被 Runner 重新打开；run 与 cache entry 不维持永久所有权或生命周期依赖；
- cache template 与 materialized working directory 是物理独立副本；显式纯清理不因历史 fingerprint/instance lease 关系永久阻塞；
- active working identity 完全属于各 run 的 `lifecycle-lease*.json`；cache 不创建、读取、绑定或释放 working lease；
- 跨 run 冲突保护由短时全局 open lock 串行化；打开前后各捕获一次当前 OneNote ID/path snapshot，全部 run-local lifecycle lease 只与 snapshot 做内存核对，不得逐 lease 重复访问 COM。它不形成 run→cache 生命周期关系；
- 即使某个 working run 仍打开，只要实际打开路径不位于 cache template 下，`clear cache` 也可以删除独立 template entry；它不得关闭、修改或删除该 working run；
- 如果 template 本身被 Runner 外部手工打开，实际路径检查必须拒绝删除对应 entry，无论其历史 ID 和 `opened_template` evidence 如何；
- 不再使用发布前保存的 `source_notebook.id` 作为 template-open 的充分判断。open guard 必须比较全部当前已打开 Notebook 的规范化实际目录路径，并可将 ID 仅作为补充证据。

这一决定需要在实现提交中同步修改根 `AGENTS.md` 与 `tests/manual_validation/AGENTS.md`：授权范围只覆盖用户显式启动的三个 maintenance action 对受管 validation root 下精确目标的清理，不扩展普通 Scenario、生产 MCP、用户 Notebook 或任意外部路径的删除权限。

## 公共 CLI 合同

三个动作共同支持：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py clear runs --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py clear cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py clear all --dry-run --json
```

真实执行必须由用户本人在交互式前台终端启动；命令本身不携带确认值：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py clear runs
.venv\Scripts\python.exe tests\manual_validation\run.py clear cache
.venv\Scripts\python.exe tests\manual_validation\run.py clear all
```

完成只读安全计划后，Runner 分别在后续提示中要求用户现场输入 `CLEAR-RUNS`、`CLEAR-CACHE` 或 `CLEAR-ALL`。确认值不得出现在 CLI 参数、环境变量或可管道化输入中；stdin 非交互、输入不匹配或 EOF 时必须在创建 marker/receipt 或删除任何目标前拒绝。为冻结计划而获取的短时 open lock 不属于清理 payload。

公共约束：

- `--dry-run` 不询问确认，只读取受管目录 metadata 和只读 OneNote open-path snapshot，不删除或改写任何文件；
- `--json` 输出稳定 schema，至少包含 action、managed roots、discovered/deleted/refused counts、逐目标 reason/checks、open-path snapshot 状态、tombstone 路径和 `ok`；
- 不提供任意 `--path`、fingerprint、instance、run ID glob、`--force`、`--ignore-open` 或绕过 containment/ownership 的参数；
- 默认根固定为仓库 `.local-validation/`，cache 固定为其 `fixture-cache/`；不得接受用户 Notebook 路径；
- 三个动作都拒绝 filesystem root、workspace root、validation root 本身、reparse point、无法解析的目标、未知目录形状或 ownership 不充分的目标；
- 清理只移除 managed payload；`.local-validation/` 根、cache managed marker、root-level summary tombstone 与必要的 ownership/index reconciliation metadata 保留。已完整嵌入 durable summary 的成功 receipt 可压缩删除；pending/failed/unbound receipt 必须保留；
- 任一实际删除都先将意图与全部安全检查写入 root-level pending receipt，再删除目标，最后原子更新为 deleted/failed；目标内部 evidence 不能作为唯一清理记录；
- 文件系统部分失败必须返回非零、记录已删除与未删除目标，不得谎报原子成功，也不得通过宽泛重试扩大范围。

## `clear runs`

`clear runs` 只枚举 `.local-validation/run-*` 的直接子目录。每个候选 run 必须由受支持的 run metadata 证明属于本 Runner，例如合法 `run-state.json`、`run-result.json`/`run-failure.json`、run identity 与路径一致；仅名称匹配不构成 ownership。

执行前一次性获取当前 OneNote 已打开 Notebook 的 ID 与规范化实际目录路径。每个 run 的判断规则：

1. resolved target 必须是 validation root 的直接 `run-*` 子目录，且不是 validation/cache/workspace root；
2. run tree 及其全部后代不得含 symlink/junction/reparse point；
3. 枚举 run 下全部已记录和实际存在的 Notebook/Notebook Copy directory，不假定数量为 1；
4. 只要任一当前打开 Notebook 的实际路径等于目标目录或位于 run root 内，该 run 记为 `refused_open` 并完整保留；
5. run-local lifecycle lease 的 `active` 只触发实际 probe；实际路径证明关闭后，不再阻止删除；
6. passed、failed、partial、keep-worksite 与普通 evidence 在用户显式执行 `clear runs` 时采用相同路径安全门。该动作本身就是删除这些受管历史证据的明确授权；不得把此授权外推到 Scenario 自动 cleanup；
7. 删除整个精确 run root 不需要更新 cache；两者之间不存在生命周期 metadata。旧 `fixture-cache/working-leases/` 由 `clear cache` 作为整体 legacy managed metadata 单独处理，不据此阻止任何 run 清理。

开放中的 run 不阻止其他安全 run 被清理。最终返回 `deleted` 与 `refused` 明细；只要存在 refused 或删除失败，顶层返回非零 partial result，方便用户关闭精确 Notebook 后重试。

## `clear cache`

`clear cache` 只处理带合法 managed marker 的 `.local-validation/fixture-cache/`。它枚举 index 与磁盘中可相互证明的 exact `(fingerprint, template_instance_id)` entry，并逐项复用或提取现有 containment、ownership、plain-tree、inventory 与 tombstone 安全检查。

放宽后的规则：

1. 对所有 role 的 `template-notebook` 目录执行当前 OneNote 实际路径比较；任一 template 路径被打开则拒绝该 entry；
2. cache 不查询 run-local lifecycle lease；独立 run 是否打开不决定 template 是否可删，对应 run 与 lifecycle evidence 保持不变；
3. 删除 entry 后不更新任何 run。历史 `fixture-cache/working-leases/` 不再具有运行时语义，可在 marker、plain-tree、root containment 和无 reparse point全部证明后作为 legacy metadata 清理，并在 root tombstone 记录；
4. 每个 entry 仍以单个精确目标调用安全删除，不得对 fingerprint root、`instances/`、cache root 或通配结果直接递归删除；
5. 所有 entry 删除完成后原子重建 index，只保留仍存在/被拒绝 entry；已无 payload 的 tombstone 项由 summary 审计替代并移出 index。孤儿 staging 只有在 marker、命名、plain-tree、无打开路径和 pending receipt 全部验证后才能作为独立精确目标清理；
6. 删除 entry 后，canonical fingerprint 下可证明为空的 `instances` 与 fingerprint scaffold 只用逐层 `rmdir` 清理；非空、含 lock、未知形状或 reparse point 时不碰。Marker、summary、quarantine/recovery history 默认保留，使“cache 已清空”表示没有可命中的 template payload，而不是抹除清理审计。

`BundleCacheStore` 的清理探针使用 `NotebookLifecycleWrapper.any_cache_template_open`：比较当前已打开 Notebook 的实际规范化目录与所有 role 的 exact template path，不再使用发布前 source Notebook ID，并有外部手工打开和多 role 正负测试。

## `clear all`

`clear all` 在一次只读 OneNote open-path snapshot 和一次 managed-root ownership检查下组合前两项：

1. 发现全部 exact run 与 cache entry；
2. 为每个目标计算与单独 action 相同的检查和计划；
3. 清理所有安全 run；
4. 清理所有安全 cache entry；
5. 清理 legacy cache working-lease metadata并重建 index；
6. 写入一个包含两类结果的 root-level summary tombstone。

它不是删除 `.local-validation/` 根的别名，也不得删除 marker/tombstone。开放中的 run 或 template 只拒绝对应目标，不应阻止其他独立、安全目标；最终若有任一 refused/failed target 则返回非零 partial result。

## 实现结构

- 在 runner 顶层注册一个 `clear` maintenance parser，并只注册 `runs`、`cache`、`all` 三个子 parser；它与 Scenario registry 和特殊批处理 `all` 分离；
- maintenance runtime 独立放在 `tests/manual_validation/maintenance/` 或同等清晰作用域，不伪装成 `Scenario`；
- 抽取只读 `OpenNotebookPathSnapshot`，一次枚举当前打开 Notebook，并用现有 COM hierarchy/path helper 规范化 `.onetoc2`、file URI 与目录路径；
- 抽取 `ManagedCleanupTarget`/`CleanupAssessment`，统一 root containment、ownership、reparse point、actual-open-path 与 receipt 逻辑；
- cache entry 删除继续复用 `BundleCacheStore` 的 exact typed identity，不允许 maintenance runtime自行拼接未验证路径；
- run ownership 需要新增 root marker/receipt 方案，并为历史已有 run 提供严格、可审查的 metadata 识别；无法证明的历史目录只报告 `refused_unowned`；
- 维护动作不得 import 或调用 Scenario mutation/fixture/restore runtime，不启动 MCP child，也不修改 OneNote 内容或关闭 Notebook。

## 文档同步

实现时必须同步修正以下已识别的过期或过宽表述：

- `tests/manual_validation/README.md` 当前“Runner 永不删除本地 Notebook 文件或目录”应改为：普通 Scenario 永不删除 run-scoped Notebook/普通 artifact；只有用户显式执行 maintenance action 才能按本 TODO 清理受管 run/cache payload；
- TODO 014 中“实施前必须形成项目级安全决策”的未来时态应更新为已经由原 cache exact-entry 例外和本 TODO maintenance 决策共同覆盖；
- 根 `AGENTS.md` 与 manual-validation `AGENTS.md` 增加三个 action 的精确授权、Agent 仅 dry-run 边界和 actual-path 真相；
- runner `--help`、manual-validation README、开发验证文档说明 maintenance action 不属于 `all`、不会触发 Scenario、不会关闭 Notebook；
- 明确 cache hit 与 cold build/bootstrap 的物理副本数量差异，避免再次写成“每个 run 恒有一个 Notebook directory”。

## 自动化验证

- parser/help/dispatch 覆盖三个 action、确认值、`--dry-run --json`，并证明它们不进入 Scenario registry 或 `all`；
- sentinel 证明 dry-run 零 mkdir/write/delete、零 stdin、零 scenario MCP、零 OneNote mutation/close；
- 临时文件系统覆盖 0/1/2/4 Notebook directory 的 run、单/双 role cache entry、合法/伪造 run metadata、路径逃逸、workspace/root 目标和 reparse point；
- actual-open-path 覆盖 run directory、Notebook Copy directory、template directory、ID 重建、file URI、`.onetoc2`、同名前缀但不同路径及多 role；
- run-local lifecycle lease 覆盖 active 但实际已关闭、active 且 working path仍打开、closed 与 run 已删除；证明它只参与 run open-path 判断，不参与 cache cleanup；
- `clear runs` 证明开放目标拒绝、其他目标可删且 cache entry 不受影响；
- `clear cache` 证明外部打开 template 时拒绝、打开独立 working run 时仍可删除 template、run/lifecycle evidence 不受影响、index/tombstone 正确；
- `clear all` 覆盖混合 deleted/refused/failed 的稳定 partial schema，并证明不删除 validation root、marker 或 tombstone；
- 删除失败、receipt 写失败、index 更新失败均 fail closed，保留足够 root-level recovery evidence；
- 运行 manual-validation 纯测试、完整 pytest、三个 action 的 `--dry-run --json` 和 `git diff --check`；Agent 不执行任何真实 clear action。

## 完成定义

- 一个 `clear` maintenance 分组及其 `runs`、`cache`、`all` 三个子 action 已实现，且没有其他公开清理入口；
- 实际 OneNote 打开路径是 run/template 删除的最终门；runtime 不再存在 run–cache lease 关系，legacy metadata 不参与判断；
- `clear runs` 能处理单/双 role、fresh/hit/cold/bootstrap、成功/失败/keep-worksite 的精确 run root，不假定 Notebook directory 数量；
- `clear cache` 能在不触碰独立 working run 的前提下逐个清理未打开的 exact entry，并拒绝 Runner 外手工打开的 template；
- `clear all` 组合两类清理但保留 validation root、managed marker 和审计 tombstone；
- 所有 destructive 路径均要求用户动作绑定确认、root containment、ownership、plain-tree、actual-open-path 与 pending/final receipt；无 `--force` 绕过；
- 普通 Scenario、生产 MCP、用户 Notebook 和任意外部目录的删除权限没有扩大；
- AGENTS、manual-validation README、开发文档、TODO 014 和 CLI help 与新合同一致；
- 纯合同、全量 pytest、三个 dry-run 与 diff check 通过；真实 clear action 仅由用户执行并确认结果后，本 TODO 才可标记已完成。
