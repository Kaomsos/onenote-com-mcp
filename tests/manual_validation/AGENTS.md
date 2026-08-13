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
- `all` 将已注册 scenario 作为相互独立的子命令串行启动。Scenario 之间不得共享 run directory、Notebook、MCP process、policy、fixture、evidence 或 lifecycle。
- `clear` 不是 Scenario，也不进入 registry 或 `all`。它只允许 `runs`、`cache`、`all` 三个子 action；不得增加任意 path、glob、fingerprint、instance、run ID、`--force` 或忽略打开状态的参数。
- Cache/run schema 切换后，runtime 与 maintenance 只识别 32-hex fingerprint、typed `p`/`a` instance、短 staging 和新 run metadata。不得增加 legacy lookup、payload/index-entry 迁移、fallback 或删除能力；旧 payload 必须由用户在升级前版本中通过 human-gated `clear all` 清理。唯一过渡例外是首次新 cache 初始化可在 durable `clear-all` 成功 summary、空 v1 index、零旧 payload/run 与精确 ownership 全部证明后，仅把旧命令留下的空 marker/index 壳原子 stamp 为新 schema；summary 后创建且由 schema/ownership flags/`started_at`/mtime 共同证明的 v2 run 可共存，证明不完整时仍须 fail closed。

## 隔离、权限和生命周期

- 每个真实 scenario 都获得全新的 run-scoped disposable working Notebook bundle 和全新或空的 evidence directory：默认 fresh 路径直接创建；显式 `--use-cache` 只能从已关闭 immutable template opaque-copy 后打开新的 working paths。Notebook 名称冲突或非空 run directory 必须被拒绝。
- Cache template 与 run working bundle 不维持 lease、所有权或生命周期关系。多个 scenario 可以从同一 immutable entry materialize 各自唯一的 run-scoped working bundle；短时全局 open lock 内打开前后各捕获一次当前 Notebook ID/path snapshot，全部历史 `lifecycle-lease*.json` 只与 snapshot 做内存比较，不得逐 lease 重复访问 COM。只有实际 live Notebook ID 集相交、working path 相交、role 内重复或身份尚未可靠重绑定时才拒绝。Run-local active lease 不得阻止物理独立 cache entry 的 invalidation/cleanup；cache cleanup 只按实际 template path 判断 template 本身是否打开。
- 所有受管 cache/staging/working/evidence 路径使用普通绝对 Windows 路径并受 240 UTF-16 units preflight 约束；不得使用 `\\?\`、依赖系统 long-path 开关、截断 opaque Notebook 名称或以重试 `WinError 3` 绕过预算。
- OneNote 返回的 Notebook、SectionGroup、Section、Page 与对象 ID 只属于逻辑身份；不得把完整 ID 插入任何受管文件名、目录名、working name 或临时名。物理名称只能使用固定语义 token、有界 ordinal 或既有 typed short key，完整 ID 必须保存在 JSON evidence/metadata 内；运行时 name guard 与源码合同测试必须同时覆盖该边界。
- 一个 scenario 最多启动一个 MCP child process。其静态 spec 只能包含该 scenario 的 fixture、mutation、evidence read 和 restore/cleanup 所必需的完整最小权限闭包。
- Fixture 创建前，通过一次 `health_check` 核对精确的 policy、tool allowlist、timeout 和适用的 Copy budget。绝不能合并不同 scenario 的权限，也不能在启动后扩权。
- Working Notebook 的 create/open/get/close 只属于窄 lifecycle wrapper，并受精确 ID/name/path/role lease 约束；cache path 必须额外证明 `actual_path == working_path`、`actual_path != template_path`。Fixture 创建必须留在 scenario MCP process 内。
- Materialized working Notebook 必须在 exact plain working tree 内有界打开声明的 SectionGroup 和 `.one` Section：优先使用绝对 working path 与空 relative ID，必要时才使用文件名与精确 parent ID，并在两种情况下都回读证明实际 parent。不得组合绝对 path 与非空 parent ID；仅打开空 Notebook shell 或只收到 `OpenHierarchy` 返回 ID 不算成功。全局 hierarchy snapshot 暂时不可见时，只有同一 COM 返回 ID 的 exact-self 回读同时证明预期类型、名称、非回收站状态和精确 parent，才可继续进入后续完整 live validation。OneNote 重建 ID 时，必须先按唯一 Notebook-relative typed address 记录 old→live 映射，后续 validator/mutation 只能使用 live ID。Working-copy open/activation 失败必须保留现场与绑定实际 live ID 的 active lease，但不能反向污染已验证的 immutable template；用户精确关闭该 working Notebook 后允许重试。映射缺失、歧义或 live validation 失败时 exact cache entry 必须变为不可命中的 invalid/quarantine，模板与失败现场保持不删除。
- 使用绑定到 manifest 的精确 ID 和最新 confirmation field。可恢复操作默认必须 restore 并验证状态。每个具名 scenario 都可在用户显式传入 `--keep-worksite` 时，于 after/read-back 验证后跳过其契约内 restore/cleanup 并保留动作现场；本来就不可恢复的 scenario 则保留其既定最终状态。该模式必须保持源 Notebook 打开，在 evidence 中记录全部精确目标 ID 和人工清理要求，且不得由 `all` 透传。不可恢复操作只能触及 manifest allowlist 中的 disposable target。
- Delete scenario 必须保持非永久删除。普通 Scenario 绝不能删除 working Notebook、普通 validation artifact、Copy directory 或用户 Notebook。文件级例外仅包括 common fixture cache runtime 的 closed-bundle opaque copy/精确失效清理，以及用户显式确认的 `clear runs|cache|all` 对固定 managed validation root 下精确 owned payload 的清理。Maintenance 必须使用当前实际 OneNote 路径快照、短时 open lock、root containment、ownership、plain-tree 和 root-level pending/final receipt；成功 receipt 仅在完整证据已嵌入 durable summary 后可收敛，pending/failed/unbound receipt 必须保留；空 cache scaffold 只能对 canonical fingerprint 下已证明为空的目录逐层 `rmdir`。Run-local lifecycle lease 与物理独立 cache template 不形成跨域门禁。出现 mutation 失败、`copy_only`、restore 失败、fidelity 失败或状态不确定时，Scenario 仍必须以非零状态退出、保持 working bundle 打开并保留全部 evidence，且不得用它刷新 template。
- Move 必须保持严格：`copy_only`、source 未删除或 fidelity gate 失败都不算成功，不得跳过或降级处理。

## 变更要求

- 任何新增或修改的非只读生产 tool，都必须具备具名 scenario、静态 policy/allowlist、隔离 fixture、before/after evidence、失败 handoff，以及 `README.md` 中记录的精确用户命令。
- CLI、lifecycle、permission、registry 或 evidence 行为变化时，同步维护本文件、manual-validation README、相关开发文档和合同测试。
- Scenario dry-run 不得创建目录、启动 MCP 或访问 OneNote，并必须展示最终名称和路径、有序阶段、权限、allowlist、budget 及 lifecycle plan。Maintenance dry-run 同样零 managed write/delete、零 MCP、零 OneNote mutation/close，但会执行一次窄的只读 OneNote open-path snapshot；snapshot 失败时全部目标 fail closed。
- `--use-cache` 默认关闭；未传入时普通 Scenario 必须零 cache lookup/read/write/invalidate/cleanup。传入时只允许从 managed immutable template materialize 全新 working bundle，OneNote 不得打开 template。Interactive/UserAuthored bootstrap 是显式具名 HUMAN-GATED 发布流程，不进入 `all`；其 dry-run 不读 cache、不读 stdin、不创建 checkpoint。
- 注册 dry-run case 只能包含冻结的声明式参数。测试 harness 独占 `--dry-run --json --run-dir`，使用正式 parser 和纯 plan builder，并以 sentinel 拒绝 MCP、lifecycle、bridge、subprocess 和目录副作用；README 的带标记代码块只能与 catalog 比较，绝不能执行。
- `--keep-worksite` 是所有具名 scenario 的公共、默认关闭选项，但不得属于或由特殊批处理入口 `all` 透传，也不得扩展任何 scenario 的 policy/tool allowlist 或改变失败保留语义。合同测试必须证明默认 restore/cleanup 仍执行、显式保留时跳过适用的 restore/cleanup、写入精确 `worksite.json` 且 source lifecycle 保持 open。
- 合同测试必须覆盖 parser/registry 行为、最小权限、redaction/content-free audit、lifecycle lease、失败现场保留、严格安全门限，以及 `all` 只运行显式纳入 scenario 的保证。
