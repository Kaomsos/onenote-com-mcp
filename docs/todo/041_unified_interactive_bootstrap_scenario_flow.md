# 041：统一 Interactive Bootstrap 与 Scenario 验证流程

> ID：041
> 状态：已完成
> 优先级：P1
> 类型：Manual Validation / Interactive Fixture / CLI UX / Lifecycle
> 更新日期：2026-08-18

## 当前问题

当前 UserAuthored / Interactive 验证把 `bootstrap-<operation>-fixture` 与
`interactive-<operation>` 暴露为两个用户入口。用户必须先运行 bootstrap、手工注入内容并发布
cache template，再复制 instance 信息运行第二条 interactive 命令。双命令流程会中断一次完整验收的
上下文，增加选错 fingerprint / instance、遗漏参数和误判阶段的概率。

当前通用阶段展示也沿用普通 scenario 的
`notebook -> fixture -> scenario -> report -> lifecycle`，没有准确表达 Interactive 场景中“先由用户
创作并冻结模板，再重新打开独立 working copy执行测试案例”的真实生命周期。

本 TODO 只优化 human-gated manual-validation 的入口与编排体验。它不改变生产 Tool、Copy/Move
lossless gate、mutation policy、cache immutability，以及真实运行只能由用户本人前台启动等安全边界。

## 目标体验

公开入口统一为 `interactive-<operation>`；`bootstrap-<operation>-fixture` 不再作为需要用户单独调用的
公开命令。一次 interactive 运行根据 cache 参数选择以下路径：

```text
不带 --use-cache
notebook
-> bootstrap（创建 authoring zone、等待用户注入内容并确认、冻结并发布 template）
-> fixture（从刚发布的 immutable template 重新打开独立 working copy并重绑 live ID）
-> scenario（执行一次目标测试案例）
-> report
-> lifecycle

带 --use-cache
notebook / cache selection
-> fixture（直接 materialize 已有 ready template）
-> scenario
-> report
-> lifecycle
```

具体要求：

1. `interactive-<operation>` 成为用户唯一入口；bootstrap 保留为内部可复用阶段，而不是第二个公开
   scenario/命令。
2. 不带 `--use-cache` 时自动进入 interactive bootstrap；用户在精确 authoring zone 注入内容并完成
   run-bound 确认后，同一前台进程自动发布模板、materialize working copy，并继续执行 scenario，
   不要求用户复制 instance ID 或再输入另一条命令。
3. 用户确认属于 `bootstrap` 阶段；`fixture` 阶段表示模板发布后重新打开物理独立 working copy、完成
   typed hierarchy 重绑与 live validation；测试 Tool 调用只发生在 `scenario` 阶段。
4. 带 `--use-cache` 时跳过 bootstrap，只允许选择并消费已经验证为 ready、mutation-eligible 且与当前
   recipe fingerprint 匹配的 immutable template；cache miss、歧义、evidence-only 或 invalid entry 继续
   fail closed，不隐式退回 fresh authoring。

## 设计约束

- 保持扁平公开 CLI：每个 `interactive-<operation>` 仍是一个完整隔离 suite；不得重新引入公开
  `bootstrap`、`validate`、`fixture` 等 helper action。
- 一次运行最多启动一个 scenario MCP child process。Bootstrap fixture 创建、cache 发布、working copy
  materialization 与 scenario handoff 必须继续遵守既有最小权限和单 MCP 边界。
- Fresh 路径必须先精确关闭 authored Notebook bundle，证明 template inventory 与 frozen identity，再
  opaque 发布 immutable template；scenario 绝不能直接在用户刚编辑的 authored bundle 上执行。
- 重新打开的 working copy必须拥有新的 run-scoped 路径和 live OneNote ID，完成完整 role set 的 typed
  rebind、连续稳定观察、内容真实性复核与一次性 handoff 后才能 mutation。
- `--use-cache` 只控制是否复用 ready template，不得放宽 instance、fingerprint、capability、policy、
  lifecycle 或 lossless 校验。
- EOF、用户拒绝、确认超时、发布失败、materialization/rebind 失败或 scenario 失败均须保留 content-free
  evidence，默认精确关闭本次 lease；不得自动删除 working files、失败现场或 cache template。
- `--dry-run` 必须零 stdin、零 cache 读写、零目录创建、零 OneNote/MCP side effect，并能展示 fresh 与
  `--use-cache` 两条确定性阶段计划。
- `all` 不得自动纳入 Interactive 场景；合并入口不扩大真实批处理资格。

## 实施范围

- 重构 Interactive scenario / UserAuthored recipe 编排，使 fresh authoring、template publication、
  materialization、scenario execution 在同一 run checkpoint 中串联。
- 从 registry、parser、help、dry-run catalog 和 README 中移除独立 bootstrap 用户入口；保留必要的内部
  bootstrap 组件与可测试边界。
- 统一阶段状态、实时 progress、checkpoint、failure phase、report 和 lifecycle evidence 命名，确保输出
  顺序为 `notebook -> bootstrap -> fixture -> scenario -> report -> lifecycle`。
- 定义 fresh 流程自动选择本次刚发布 instance 的精确 handoff；定义 `--use-cache` 下显式或唯一 ready
  instance 的选择规则，避免“最近一次”或名称猜测。
- 迁移现有 Interactive Copy 与 Interactive Move 场景，避免只为某一个 operation 建立特例。

## 自动化合同

- Parser / registry 证明每个 operation 只有 `interactive-<operation>` 公开入口，旧 bootstrap 名称被明确
  拒绝或按一次性兼容决策处理，不形成长期双入口。
- Fresh dry-run 固定六阶段顺序和 bootstrap 用户确认位置；cache dry-run 固定跳过 bootstrap，并保持其他
  阶段顺序不变。
- Fresh 纯合同覆盖 authoring confirmation、ready/evidence-only 分类、全部 role close、immutable publish、
  自动 instance handoff、materialize/rebind、一次 scenario mutation 和最终 lifecycle。
- Cache 纯合同覆盖 ready hit、explicit/unique selection、miss、歧义、invalid、fingerprint mismatch，且证明
  任一失败都不会进入 bootstrap 或 mutation。
- 覆盖 bootstrap、fixture、scenario 各阶段失败的 checkpoint/finalization，证明无第二 MCP、无重复
  mutation、无 template 回写、无越界清理。
- README 中的真实命令、help、registered dry-run cases 和 parser catalog 保持一致；完整 pytest 与所有受
  影响命令的 `--dry-run --json` 通过。

## 真实验收

真实运行只能由用户本人在交互式前台终端执行：

1. 对至少一个 Interactive Copy 和一个 Interactive Move 场景运行不带 `--use-cache` 的单命令流程，
   确认用户只在 bootstrap 阶段输入内容/确认，随后自动进入独立 working copy 的 scenario。
2. 对同一 recipe 再运行带 `--use-cache` 的命令，确认完全跳过 authoring/bootstrap，并从 ready template
   materialize 新 working copy。
3. 核对两条路径的 progress、report、artifacts 与 lifecycle 阶段归属；确认 fresh authored bundle、cache
   template、scenario working bundle三者路径和 live identity 不混用。
4. 验证 cache miss/invalid/evidence-only 路径 fail closed，且不会悄悄要求用户创作或执行 mutation。

## 非目标

- 不改变生产 Copy/Move 实现、verification tier、lossless comparator 或 verified-Copy-before-delete 门限；
- 不允许 Agent、pytest、CI、hook、timer、watcher 或后台任务启动真实 Interactive/Bootstrap mutation；
- 不允许 `--use-cache` 自动选择不明确、过期或 evidence-only 的 template；
- 不在 authored Notebook bundle 上直接执行测试案例，也不省略 publish 后重新打开 working copy；
- 不把 Interactive 场景加入 `all`，不引入任意路径、用户业务 Notebook 或 raw XML 输入。

## 实现证据（2026-08-18）

- 七个 `bootstrap-<operation>-fixture` 公开命令已从 registry、parser 与 `scenarios/__init__.py` 移除；argparse 对旧名报未知命令。
- 七个 `interactive-<operation>` 统一入口已注册，`user-authored-fixture-consumer` 更名为 `interactive-user-authored-fixture`。
- Orchestrator fresh 六阶段 / cache 五阶段已落地；bootstrap 逻辑下沉为 `run_interactive_bootstrap_phase()` 内部组件。
- 各 operation 的 bootstrap/consumer recipe 已合并为单一 recipe；dry-run catalog 与合同测试已同步。
- Orchestrator 解析或发布的 authored instance 通过 materialized manifest 交给 scenario，不再从原始 CLI 参数二次选择；fresh 自动消费刚发布的 instance，cache 路径支持显式 ID 或唯一 ready 自动选择。
- Authored cache entry 现在持久化并在 live identity gate 复核 `state`、`mutation_eligible` 与 `move_source_deletion_allowed`；`evidence_only` 结果保持 mutation-ineligible。UserAuthored recipe 为 v4；Move 因 materialization identity 修复升至 v11，旧 v10 fingerprint 确定性 miss。
- 新增 ready/evidence-only 元数据、状态不一致、唯一/歧义选择、无显式 ID 的 UserAuthored/Move scenario 消费合同，以及 programmatic interactive bootstrap 的 null `template_instance` 发布回归合同。
- Move Fresh 真实尝试 `run-2026-08-18-21-57-36` 与 `run-2026-08-18-22-06-02` 都在第一个 destination Notebook create 前以 OneNote `0x80042006` 失败；两处现场均没有 Notebook 目录内容或 lifecycle lease，未进入 bootstrap/fixture/Move。第一次仅把 working name 从 65 压到 62 units，完整路径仍从 154 只降到 151，因此第二次复现推翻了名称 64-unit 单因解释。对照历史 Fresh 双 Notebook 的 147-unit create 成功证据，以及 inserted-file existing working root 在 150 units 的 materialized open 成功后，当前合同保守采用 Notebook root COM create/open 的 147-unit 已验证安全上限，并按实际 run root 动态压缩 Fresh/Cache physical name；Move dry-run 现将四个规划 root 全部限制到不超过 147 units。
- 用户真实 Fresh `run-2026-08-18-22-30-05` 已证明该路径修复：destination/source create、bootstrap、template publication、opaque materialization、typed rebind、双稳定与 scenario-before snapshot 均已通过。fixture 随后在 frozen identity gate 停止：同一页面的 RichText capability、object kind/count、标题与结构未变，但 OneNote 打开 working copy 时新增一个默认 `span lang`，导致旧 persistence digest 漂移。v9 最初仅忽略该语言标记；用户随后真实运行 `run-2026-08-18-22-40-20`，仍在同一 gate 停止，fresh/working 的 persistence 与 materialization digest 分别为 `c0e9…` / `9737…`，而 typed capability、object kind/count、标题、结构和 byte inventory 仍一致。这证明 OneNote 还会重序列化富文本展示 span/style。v10 的 materialization identity 因而保留可见富文本和结构化内容、忽略仅展示的富文本 span/style；template/working inventory、typed rebind、live validation 和后续 Move 的严格 fidelity gate 仍全部独立 fail closed。两次失败 run 都精确关闭 lease 并保留 working files/evidence；均不构成 Move 成功证据。
- 用户真实 v10 Fresh `run-2026-08-18-22-46-50` 再次进入同一 frozen identity gate；增强诊断明确列出 `template_instance_id, projection_digest`，其中前者由后者前 24 hex 派生，并非第二个独立差异。Fresh/materialized 的 materialization digest 为 `65b798…` / `c58c2a…`；两侧 capability 均为 `List, Outline, RichText, Table`，对象均为 92 个且 kind/count 完全相同，Table/Row/Cell/OE 分别为 `1/6/18/66`，template/working pre-open inventory byte-for-byte 相同。富文本展示已在 v10 排除，当前含 Table 页面仍有差异，因此剩余漂移推断为 semantic projection 中保留的 Table/Column/Row/Cell 展示属性。v11 排除这些打开时可重算的属性，但继续冻结可见文本、列数、Row/Cell 拓扑、List/Tag、对象计数与 binary hash；真实 Move 仍使用原先严格 Copy-before-delete fidelity gate。该失败 run 精确关闭双 lease 并保留现场，不构成 Move 成功证据。
- v11 materialization focused pure tests 已通过 `99 passed`；新增正向边界覆盖列宽/边框/底色重写，负向边界证明单元格正文或列拓扑变化仍改变 identity。Move v11 fresh/cache `--dry-run --json` 均通过并使用新 fingerprint `e2ca245f…fa241b3d`，分别保持六阶段（含零 stdin 的 checkpoint）与五阶段（无 bootstrap/checkpoint）计划。
- 用户真实 v11 Fresh `run-2026-08-18-22-54-06` 已完整通过 bootstrap、template publication、post-publish materialization identity 与 fixture live validation；随后 `run-2026-08-18-22-57-06` 也从同一 ready instance 以 cache 五阶段越过 fixture。两次 Move 都确定性停在 `verify_copy`：目标 Page 已创建，source→transformed 语义投影全通过，transformed→target 仅有同一 RichText run 的两个文本分段 path（`rich_text[2][0]` / `[3][0]`）不匹配；title、完整 visible text、content objects、binary、projection completeness 均通过，源/目标的 `span=187`、`span@lang=5`、`span@style=182` 与 Table/Row/Cell/OE=`1/6/18/66` 也一致。证据将差异限定在格式 run 的文本边界，但 content-free 现场不暴露具体字符；生产 `semantic_content_v1` 因而只接受可证明不改变非空白字符格式的边界空白移动：空白 style 中性化后合并，完整文本序列及每个非空白字符的有效 style/link 仍精确比较。若真实差异涉及非空白字符跨 style 移动，后续 run 仍会 fail closed。两次 run 均保持 `copy_only/source_untouched/source_deleted=false`，目标留存、双 lease 精确关闭；它们不构成真实 Move 成功证据。该读回修复的相关生产/场景测试为 `266 passed`，manual-validation 纯合同为 `618 passed`，完整 pytest 为 `1405 passed`；同一 v11 ready template 可直接用于下一次 `--use-cache` 真实复测。
- 用户随后确认 v11 Cache `run-2026-08-18-23-01-03` 真实通过：`decision=validated_hit`、`opened_template=false`，同一 ready instance materialize 后 `semantic_content_v1` 的 source→transformed 与 transformed→target 均通过，`copy_contract_satisfied=true`、`verified=true`、`lossless=true`、`outcome=moved`，源 Page 以非永久方式从 active hierarchy 移除。该次显式 keep 模式按契约保留双 Notebook 打开和 Move 现场供人工检查，并记录 `manual_cleanup_required=true`；没有删除 working files 或 template。
- Page 回读失败合同在真实成功后继续加固：单一 content category 抛对应 `PageReadbackMismatch` 子类，多类别与未知类别分别抛 `PageMixedContentReadbackMismatch` / `PageUnknownContentReadbackMismatch`；Move 收敛为 `copy_only` 时保留子类、稳定 `readback_error_code` 和 content-free details，同时继续使用顶层 `partial_failure` 兼容响应并阻止删源。该加固的 `tests/test_copying.py` 为 `183 passed`，相邻 server/manual Move 回读合同为 `80 passed`，manual-validation 纯合同为 `618 passed`，完整 pytest 为 `1421 passed`。
- 七个 fresh/cache `--dry-run --json` 均通过：fresh 为六运行阶段、八个声明式计划步骤并含 `interactive-checkpoint(stdin_read_performed=false)`、publish/materialize 与二次 live validation；cache 为五步且无 bootstrap/checkpoint。
- 用户已确认 `interactive-copy-inserted-file` 的真实 Fresh/Cache 配对通过：Fresh `run-2026-08-18-21-51-01` 为 `cache_mode=fresh`、`decision=bootstrap_published`，发布后 materialization validation 通过；Cache `run-2026-08-18-21-52-57` 为 `cache_mode=use_cache`、`decision=validated_hit`。两次 run 均为 `status=passed`、`opened_template=false`、`production_verified=true`、`production_lossless=true`，使用不同的 working Notebook identity，并以 `closed_preserved` 完成 lifecycle。
- 下列「完成定义」已由自动化合同、Interactive Copy Fresh/Cache 配对，以及 Interactive Move Fresh bootstrap/fixture 与最终 Cache lossless Move 的组合真实证据闭环。

## 真实验收命令（已完成）

以下命令只能由用户在 OneNote Desktop 已启动且 GUI 可见的交互式前台 PowerShell 中执行；本 TODO 所需验收已经完成，保留命令仅用于后续人工回归：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-move-page-content
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-move-page-content --use-cache
```

`--notebook-label` 是可选项；省略时使用 scenario 名称。若当前 fingerprint 恰好只有一个 ready instance，cache 命令可以省略 `--template-instance-id` 以使用唯一实例自动选择。若错误明确报告多个 ready instance，再使用 fresh 输出的精确 `authored-<24 hex>` ID 重试。为额外回归 bounded UserAuthored v4 的同一 handoff/eligibility 修复，可运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-user-authored-fixture --notebook-label todo-041-authored
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-user-authored-fixture --use-cache --notebook-label todo-041-authored-cache
```

只把用户确认的真实 run 结果写入完成证据；mock 和 dry-run 不能替代上述真实验收。

## 完成定义

- [x] 所有 UserAuthored / Interactive operation 只暴露统一的 `interactive-<operation>` 用户入口；
- [x] 不带 `--use-cache` 的一次命令完整串联 bootstrap、重新 materialize fixture 与 scenario；
- [x] 带 `--use-cache` 时确定性跳过 bootstrap，只消费匹配的 ready immutable template；
- [x] Progress、checkpoint、failure phase、report 与 lifecycle 统一呈现六阶段顺序，并正确归属用户确认；
- [x] 自动化覆盖 fresh/cache、成功/拒绝/超时/发布失败/rebind 失败/scenario 失败及 fail-closed 边界；
- [x] Manual Validation README、开发流程、help、dry-run catalog 与 AGENTS 安全约束同步；
- [x] 用户完成至少一项 Interactive Copy 和一项 Interactive Move 的 fresh/cache 真实验收并确认体验；
- [x] 聚焦测试、manual-validation 纯测试、完整 pytest 和相关 `--dry-run --json` 全部通过。

## 关联

- [TODO 004](004_interactive_copy_move_content_fidelity_validation.md)：既有 Interactive Copy 内容类型验收。
- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：immutable template cache 与隔离 working copy。
- [TODO 020](020_user_authored_fixture_development_scaffold.md)：UserAuthored fixture 通用脚手架。
- [TODO 026](026_manual_validation_progress_verbosity.md)：实时阶段进度与 verbosity 输出。
- [TODO 039](039_interactive_real_page_move_lossless_validation.md)：历史双入口暴露实际体验问题的 Interactive Move 验证，现已由统一入口承接。
- [Manual Validation Runner](../../tests/manual_validation/README.md)
- [OneNote mutation 隔离验证流程](../dev/isolated_mutation_validation.md)
