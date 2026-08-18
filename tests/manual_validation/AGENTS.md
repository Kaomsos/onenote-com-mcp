# 人工验证指令 — HUMAN-GATED

本目录包含能够执行真实 OneNote mutation 的代码。这些规则比父级测试规则更严格，不能由 scenario 代码、文档或便利性需求放宽。

## 智能体执行边界

- 智能体绝不能执行真实的 `run.py <scenario>` 或 `run.py all`。此禁令适用于前台工作、后台智能体、CI、hook、install/package 脚本、import、timer、watcher 和间接 helper 命令。
- 用户此前执行过真实运行，或授予了编辑仓库的一般权限，都不代表智能体获得再次启动真实运行的授权。需要真实证据时，将精确命令交给用户。
- 智能体可以修改代码和文档、以只读方式检查现有 artifact、运行纯合同测试、编译或 import 模块，以及运行明确包含 `--dry-run` 的命令。
- `clear runs|cache|all` 是唯一公开 maintenance 分组。智能体只能运行其 `--dry-run`；真实 clear 必须由用户本人在交互式前台终端启动，并在命令启动后的提示中现场输入动作绑定确认值。CLI 不提供 `--confirm`，非交互/重定向 stdin 必须拒绝；不得由任何自动化、后台任务或间接 helper 启动。
- 除非用户亲自运行 scenario 并提供或确认证据，否则绝不能报告真实后端 scenario 已通过。

允许的智能体验证包括：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> --dry-run
.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run
```

## 场景与注册表架构

- 公开 CLI 是扁平的：每个 `run.py <scenario>` 都是完整隔离 suite。不得重新引入 `validate`、`suite`、`inspect`、`read`、`report` 或其他公开 helper action。`all` 是唯一批处理例外。
- `scenarios/` 下的每个可执行模块恰好定义一个具名 `Scenario` 子类。共享依赖放在 `scenarios/common/`；明显属于基础设施的 base 和 `__init__.py` 可以保留在 scenario 根目录。
- 使用 `@SCENARIO_REGISTRY.register` 装饰公开 scenario 类，然后在 `scenarios/__init__.py` 的显式有序清单中 import 它们。`scenarios/common/registry.py` 持有唯一 registry 对象，并且不得 import 具体 scenario。
- 新的探索性或仅用于验证的 scenario 默认设置 `included_in_all = False`。只有经过显式稳定性和权限审查后，才能将其纳入 `all`；filesystem discovery 绝不能自动纳入它。该资格只控制真实 `all` 批处理，与 dry-run pytest 收集资格无关。
- 每个公开 Scenario 必须显式拥有一个 fixture recipe，并自动提供至少一个稳定 ID 的注册 dry-run case。Recipe 由 Scenario 模块显式 import；不得新增 fixture registry、dry-run scenario 列表或 filesystem discovery。
- `all` 将已注册 scenario 作为相互独立的子命令串行启动。Scenario 之间不得共享 run directory、Notebook、MCP process、policy、fixture、evidence 或 lifecycle。真实子任务失败后，只有本次 run 的全部 exact lifecycle lease 都已精确关闭并写入 durable failure-finalization evidence 时，父批次才可继续；任一 close 失败、证明缺失或异常子进程退出必须立即停止。dry-run 仍检查全部已注册计划。
- `clear` 不是 Scenario，也不进入 registry 或 `all`。它只允许 `runs`、`cache`、`all` 三个子 action；不得增加任意 path、glob、fingerprint、instance、run ID、`--force` 或忽略打开状态的参数。
- `launch_onenote_gui_check.py` 是唯一独立于 `run.py`、Scenario Registry 和 `all` 的真实 GUI effect 验收入口。它只能由用户本人在交互式前台终端运行，不得创建/修改/关闭 Notebook；必须先以 UI Control 关闭的独立 MCP 证明 authorization 零 backend call，再以仅开启 UI Control 的第二个独立 MCP 验证单次启动、重复调用幂等、health readiness 和只读 hierarchy COM。其 runtime calls/bridge/server stderr 日志不得落盘，只能按 `--verbosity` 输出到当前终端；逐阶段结构化验收证据继续写入 owned run。Agent 只可运行其 `--dry-run` 和纯 mock 合同测试。
- Cache/run schema 切换后，runtime 与 maintenance 只识别 32-hex fingerprint、typed `p`/`a` instance、短 staging 和新 run metadata。不得增加 legacy lookup、payload/index-entry 迁移、fallback 或删除能力；旧 payload 必须由用户在升级前版本中通过 human-gated `clear all` 清理。唯一过渡例外是首次新 cache 初始化可在 durable `clear-all` 成功 summary、空 v1 index、零旧 payload/run 与精确 ownership 全部证明后，仅把旧命令留下的空 marker/index 壳原子 stamp 为新 schema；summary 后创建且由 schema/ownership flags/`started_at`/mtime 共同证明的 v2 run 可共存，证明不完整时仍须 fail closed。

## 隔离、权限和生命周期

- 每个真实 scenario 都获得全新的 run-scoped disposable working Notebook bundle 和全新或空的 evidence directory：默认 fresh 路径直接创建；显式 `--use-cache` 只能从已关闭 immutable template opaque-copy 后打开新的 working paths。Notebook 名称冲突或非空 run directory 必须被拒绝。
- Cache template 与 run working bundle 不维持 lease、所有权或生命周期关系。多个 scenario 可以从同一 immutable entry materialize 各自唯一的 run-scoped working bundle；短时全局 open lock 内打开前后各捕获一次当前 Notebook ID/path snapshot，全部历史 `lifecycle-lease*.json` 只与 snapshot 做内存比较，不得逐 lease 重复访问 COM。只有实际 live Notebook ID 集相交、working path 相交、role 内重复或身份尚未可靠重绑定时才拒绝。Run-local active lease 不得阻止物理独立 cache entry 的 invalidation/cleanup；cache cleanup 只按实际 template path 判断 template 本身是否打开。
- 所有新建的受管 cache/staging/working/evidence 路径使用普通绝对 Windows 路径并受 240 UTF-16 units preflight 约束；传给 OneNote COM 用于 Notebook root create/open 的精确 working 路径另受 147-unit 安全兼容上限，物理 working name 必须按实际 run root 在 12–64 units 内确定性压缩并在任何 COM 调用前复核。不得使用 `\\?\`、依赖系统 long-path 开关、截断 opaque Notebook 名称或以重试 `WinError 3` 绕过预算。唯一的历史恢复例外是 human-gated `clear runs|cache|all`：它仍须预检自身将创建的 lock、receipt、summary、index 与原子临时路径，但不得仅因已经存在且等待清理的 owned payload 超过 240 units 而拒绝；该 payload 仍须通过精确 ownership、固定根 containment、plain-tree/reparse、当前 OneNote open-path snapshot 和交互确认门限。
- OneNote 返回的 Notebook、SectionGroup、Section、Page 与对象 ID 只属于逻辑身份；不得把完整 ID 插入任何受管文件名、目录名、working name 或临时名。物理名称只能使用固定语义 token、有界 ordinal 或既有 typed short key，完整 ID 必须保存在 JSON evidence/metadata 内；运行时 name guard 与源码合同测试必须同时覆盖该边界。
- 一个 scenario 最多启动一个 MCP child process。其静态 spec 只能包含该 scenario 的 fixture、mutation、evidence read 和 restore/cleanup 所必需的完整最小权限闭包。
- Fixture 创建前，通过一次 `health_check` 核对精确的 policy、tool allowlist、timeout 和适用的 Copy budget。绝不能合并不同 scenario 的权限，也不能在启动后扩权。
- 真实单项 Scenario 必须在任何 run-local Notebook create/open、cache materialization 或 scenario MCP 启动前，用不创建 COM 的 native probe 证明 OneNote Desktop 进程及可见 GUI 已存在；真实 `all` 在首个 child 前执行一次相同 preflight。失败时必须零 Notebook/cache/MCP side effect；dry-run 禁止读取 GUI 状态。`health_check` 在首次 hierarchy/COM 读取前再次执行同一门限，且不得隐式启动 OneNote。
- Working Notebook 的 create/open/get/close 只属于窄 lifecycle wrapper，并受精确 ID/name/path/role lease 约束；cache path 必须额外证明 `actual_path == working_path`、`actual_path != template_path`。Fixture 创建必须留在 scenario MCP process 内。
- Fresh fixture 默认在创建后的同一 live identity 上完成 Recipe 验证并进入 mutation；programmatic cold build 只有在发布 immutable template 时才对每个 exact Notebook 请求一次 `SyncHierarchy`，随后执行必要的 `CloseNotebook(force=false)` 与精确关闭证明，不得为猜测的持久化窗口额外 close/reopen。Sync 请求失败必须阻断发布并保留 active lease 供默认失败收尾。唯一例外是显式声明为 index-dependent、以 fresh 模式运行且静态 allowlist 含 `search_pages` 的 Search scenario：它可以在全部 Page 写入完成后执行一次 `CloseNotebook(force=false)`、确认 exact paths 已关闭并从同一路径 reopen，再按 typed relative address 重绑、连续两次确认 hierarchy 稳定，最后每个 Page 只读取一次完成内容验证后进入 `FindPages` readiness。该 checkpoint 只能用于激活 OneNote index，不得成为 Query、普通 fresh Recipe 或 cache working copy 的通用 persistence 策略；Search 的 cache working copy 使用普通 batch-open、层级收敛和单次内容 snapshot。
- Materialized working Notebook 必须在 exact plain working tree 内只打开一次。全部 child 路径在第一次 child COM 调用前完成预算、containment、reparse 与 typed parent 校验并冻结；同一 role 在一个 PowerShell/COM session 内按 parent-before-child 顺序批量激活声明的 SectionGroup/`.one` Section，并在末尾尝试一次 pages hierarchy。逐项 `OpenHierarchy` 错误最多只重试该失败项一次；确定性类型、parent、path、回收站或唯一性冲突立即 fail closed。随后按 typed relative address 重绑全部 live ID，要求完整声明 hierarchy 连续稳定两次，再捕获唯一一次完整 scenario before snapshot；同一份 snapshot 同时完成 cache 内容真实性复核和 mutation 基线取证，每个 Page 只读一次，不得在 scenario 开始时再次读取。全部 role 的 handoff 必须按 exact Notebook ID、role set 和 digest 单次消费完毕，任一 mutation 在未消费完成时必须在 MCP 调用前 fail closed。Notebook 直属 child 使用绝对 working path与空 relative ID；SectionGroup 下的嵌套 child 只能使用文件名与同批精确 parent ID。不得组合绝对 path 与非空 parent ID，也不得在 OneNote 开始接管路径后重新按磁盘存在性决定剩余请求。Cached manifest 中的 `notebook_copy_root`、working Notebook path 与 lifecycle lease 只能在先证明它们共同属于同一旧 run 后按字段重绑到当前 run，并写独立 evidence；不得递归替换内容或修改 template。任一映射、唯一性、双稳定、内容验证、run-local path 关系或 handoff 失败均在 mutation 前 fail closed，保留 working files 与当前 lease；默认 failure finalizer 随后精确关闭，显式 keep 模式才保持 active。真实 runtime 和纯测试 fake 必须使用同一 batch 合同，不得重新引入逐对象 activation fallback。Template 永不打开、修改或接收失败现场回写；working activation、COM 或 convergence 失败不得自动 quarantine 已验证 template，只有可确定归因于 template 身份、inventory 或缓存证据完整性的失败才可使 exact entry 不可命中。
- 使用绑定到 manifest 的精确 ID 和最新 confirmation field。可恢复操作默认必须 restore 并验证状态。每个具名 scenario 都可在用户显式传入 `--keep-worksite` 时，于 after/read-back 验证后跳过其契约内 restore/cleanup 并保留动作现场；本来就不可恢复的 scenario 则保留其既定最终状态。该模式必须保持源 Notebook 打开，在 evidence 中记录全部精确目标 ID 和人工清理要求，且不得由 `all` 透传。不可恢复操作只能触及 manifest allowlist 中的 disposable target。
- Delete scenario 必须保持非永久删除。普通 Scenario 绝不能删除 working Notebook、普通 validation artifact、Copy directory 或用户 Notebook。文件级例外仅包括 common fixture cache runtime 的 closed-bundle opaque copy/精确失效清理，以及用户显式确认的 `clear runs|cache|all` 对固定 managed validation root 下精确 owned payload 的清理。Maintenance 必须使用当前实际 OneNote 路径快照、短时 open lock、root containment、ownership、plain-tree 和 root-level pending/final receipt；成功 receipt 仅在完整证据已嵌入 durable summary 后可收敛，pending/failed/unbound receipt 必须保留；空 cache scaffold 只能对 canonical fingerprint 下已证明为空的目录逐层 `rmdir`。Run-local lifecycle lease 与物理独立 cache template 不形成跨域门禁。出现 mutation 失败、`copy_only`、restore 失败、fidelity 失败或状态不确定时，Scenario 仍必须以非零状态退出并保留全部 working files/evidence，默认按 exact lease 关闭本次 working bundle，且不得用它刷新 template；只有用户显式选择 `--keep-notebook` 或 `--keep-worksite` 才保持打开。任一精确 close 失败必须 fail closed，不得声称隔离完成。
- Move 必须保持严格：`copy_only`、source 未删除或 fidelity gate 失败都不算成功，不得跳过或降级处理。

## 变更要求

- 任何新增或修改的非只读生产 tool，都必须具备具名 scenario、静态 policy/allowlist、隔离 fixture、before/after evidence、失败 handoff，以及 `README.md` 中记录的精确用户命令。
- CLI、lifecycle、permission、registry 或 evidence 行为变化时，同步维护本文件、manual-validation README、相关开发文档和合同测试。
- Scenario dry-run 不得创建目录、启动 MCP 或访问 OneNote，并必须展示最终名称和路径、有序阶段、权限、allowlist、budget 及 lifecycle plan。Maintenance dry-run 同样零 managed write/delete、零 MCP、零 OneNote mutation/close，但会执行一次窄的只读 OneNote open-path snapshot；snapshot 失败时全部目标 fail closed。
- `--use-cache` 默认关闭；未传入时普通 Scenario 必须零 cache lookup/read/write/invalidate/cleanup。传入时只允许从 managed immutable template materialize 全新 working bundle，OneNote 不得打开 template。Interactive/UserAuthored 场景以统一 `interactive-<operation>` 为唯一公开入口：fresh 路径在同一次 run 内串联 bootstrap 阶段（HUMAN-GATED、不进入 `all`），cache 路径确定性跳过 bootstrap；其 dry-run 不读 cache、不读 stdin、不创建 checkpoint。
- 注册 dry-run case 只能包含冻结的声明式参数。测试 harness 独占 `--dry-run --json --run-dir`，使用正式 parser 和纯 plan builder，并以 sentinel 拒绝 MCP、lifecycle、bridge、subprocess 和目录副作用；README 的带标记代码块只能与 catalog 比较，绝不能执行。
- `--keep-notebook` 与 `--keep-worksite` 是所有具名 scenario 的公共、默认关闭选项，但不得属于或由特殊批处理入口 `all` 透传，也不得扩展任何 scenario 的 policy/tool allowlist。合同测试必须证明成功与失败的默认 lifecycle 都精确关闭、显式保留时保持 open；`--keep-worksite` 还须跳过适用的 restore/cleanup 并写入精确 `worksite.json`。默认失败关闭只改变 OneNote open state，不删除 working files、artifacts 或 cache。
- 合同测试必须覆盖 parser/registry 行为、最小权限、redaction/content-free audit、lifecycle lease、失败现场保留、严格安全门限，以及 `all` 只运行显式纳入 scenario 的保证。
