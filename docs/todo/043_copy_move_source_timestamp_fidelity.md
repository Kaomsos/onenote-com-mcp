# 043：Copy/Move Page `dateTime` 秒级语义保真

> ID：043
> 状态：已完成
> 优先级：P1
> 类型：Copy / Move / Page content 保真 / OneNote COM
> 更新日期：2026-08-23

## 决策摘要

本 TODO 的唯一目标是：在现有 Copy 和重建式 Move 流程中，把 source Page 根 `dateTime` 作为 Page content 的一部分复制到对应的 exact target Page，并在最终内容写入后回读验证。目标与 source 只要在时区归一化后落在同一个 UTC 秒内，就视为语义保真；不要求保留小数秒或原始时区文本。

2026-08-22 已在生产 Copy/Move 流水线落地该秒级保真：planning 预检合法根 `dateTime`，reorder 后写入，再用最终完整 target XML 重生成 acceptance，并对范围内 source 做有界重读以产生 `source_drifted`。公开 `copy_report` 只增加 `page_results[].date_time.status`。当前契约见[产品能力边界](../product/README.md)和[工具契约](../design/tool_contracts.md)。

本 TODO 不处理通用层级元数据，不恢复 `createdTime`、`creationTime`、`lastModifiedTime` 或容器时间，也不新增独立的时间修改能力。Move 只有在现有内容、标题、拓扑合同以及范围内全部 Page 的 `dateTime` 秒级语义保真均通过后，才允许删除 source；写入失败或回读不一致时必须保留 source，并返回可诊断的 `copy_only`/未验证结果。范围内任一 Page 在 planning 时缺失或非法 `dateTime` 会在首个 mutation 前 fail closed，不再报告 `source_missing`。

2026-08-22，用户确认 `copy-page` 与 `move-page` 正向真实运行通过，单 Page 与含 Page 子树的 Copy/Move 正向证据已经取得。2026-08-23，用户以前台真实运行确认了跨秒负向门：Copy 报告保持完整通过，而删源前 source `dateTime` 跨秒后严格返回 `copy_only`、保留 source、零删除且不产生重复 target。该次历史证据在重构前通过 `move-page --datetime-drift-negative` 取得；当前验证入口已剥离为独立的 `negative-move-page-datetime-drift` 场景，保持 fresh-only、不进 `all`、不支持 `--use-cache`。至此本 TODO 的完成定义全部满足。

## 验证闭环

- 自动化合同已覆盖同秒通过、相邻秒失败、planning 预检零 mutation、最终写入不改正文/标题、子树聚合、`source_drifted`，以及 Copy 成功后、删源前 source `dateTime` 跨秒导致 `protected_digest` 失配时的 `copy_only` / 零删除 / 不重复 Copy / 不回写 report；
- `copy-page` / `move-page` 的独立 before/after 秒级 observer 与 scenario 断言已经过用户真实运行；Section / SectionGroup / Notebook Copy 不启用该 projection；
- 独立 `negative-move-page-datetime-drift` 场景的自动化合同与 dry-run，以及其重构前等价入口取得的用户真实 OneNote 证据均已具备；真实命令按仓库规则以预期非零退出保存 `copy_only` 失败现场，不能按普通成功 run 的退出码解释。

## 已验证字段、方法与限制（2026-08-22）

用户显式运行的 `.local-validation/run-2026-08-22-21-12-25` 对 fresh disposable Page 给出了以下能力证据；它不是 Copy/Move 产品合同或完成证据：

- **可写字段：**仅验证了 Page 根属性 `dateTime`。用 exact Page ID 经 `UpdateHierarchy` 和 `UpdatePageContent` 各写入一次后，都从同一来源读回并在最终双读中保持同一时刻。OneNote 把请求的 `2020-02-03T04:05:06.123456+08:00` 规范化为 `2020-02-02T20:05:06.000Z`，说明时区表达与小数秒不能作为字面保真要求；
- **修改方法：**`UpdateHierarchy` 使用 exact Notebook→…→Page ancestor chain；`UpdatePageContent` 使用只含 exact Page ID 与 `dateTime` 的 Page root。两条路径都由 internal validation bridge 生成最小 XML，调用方不能传入 raw XML；
- **明确排除：**测试过的 Page `lastModifiedTime` 以及 Section、SectionGroup、Notebook 的 `lastModifiedTime` 均未持久化请求值。`createdTime`、`creationTime` 在该 run 的来源中未暴露。这些字段和所有容器时间均不属于本 TODO；
- **证据边界：**`prob-timestamp-fidelity` 不调用 Copy/Move、不进入 `all`，只能证明 Page `dateTime` 的受控写入能力，不能替代本 TODO 所需的具名 Copy/Move 真实验证。

## Copy/Move 正向真实证据（2026-08-22）

用户确认以下 fresh disposable 真实运行通过：

- `.local-validation/run-2026-08-22-23-46-40`：`copy-page` 为 `passed`，覆盖同 Section、跨 Section、跨 Notebook 的 root-only 与 subtree 共 6 个 case、9 个 Page。全部独立 `page-datetime-<case>.json` 记录 `same_utc_second=true`；对应 `copy_report` 全部为 `verified=true`、`copy_contract_satisfied=true`、`fidelity=lossless`，9 个 `page_results[].date_time.status` 均为 `verified`；
- `.local-validation/run-2026-08-22-23-47-29`：`move-page` 为 `passed`，覆盖跨 Notebook 的 root-only 与 subtree 共 2 个 case、3 个 Page。全部独立秒级证据记录 `same_utc_second=true`；对应 `copy_report` 全部为 `verified=true`、`copy_contract_satisfied=true`、`fidelity=lossless`，3 个 `page_results[].date_time.status` 均为 `verified`，两组 Move 均为 `outcome=moved` 且 `source_deleted_nonpermanently=true`；
- 两次 run 的 source/destination lifecycle 均为 `closed_preserved`。证据只来自用户已执行的真实场景；智能体未启动真实 OneNote mutation。

这些证据满足单 Page 与含 Page 子树的 Copy/Move 正向完成条件；跨秒 Move 负向删源保护由下述独立 run 补齐。

## Move 跨秒负向真实证据（2026-08-23）

用户本人以前台命令运行 `.local-validation/run-2026-08-23-00-43-39`，场景按设计非零退出并保存 `failed_closed` 现场；其验收证据完整：

- 在 Copy 最终 `dateTime` 校验后观察到 exact `topology_verification/get_hierarchy` 触发点，只对 subtree child 执行一次 `UpdateHierarchy` 路径写入，把 source Page 根 `dateTime` 从 `2026-08-22T16:43:51Z` 精确推进至 `2026-08-22T16:43:52Z`；setter 只提交一次、未重放；
- 原 `copy_report` 保持 `verified=true`、`lossless=true`、`copy_contract_satisfied=true`，两页 `page_results[].date_time.status` 均仍为 `verified`，没有回写为 `source_drifted`；
- Move 返回 `partial_failure` / `outcome=copy_only` / `source_deleted=false`。两页 source 仍存在且稳定正文 hash 未变，两页 target 精确存在、无重复，target `dateTime` 仍为冻结的 `16:43:51Z`；
- content-free backend 证据为 `move_page_submissions=1`、`create_new_page=2`、`delete_hierarchy=0`，`datetime-drift-negative.json` 记录 `negative_gate_verified=true`；
- `failure-finalization.json` 证明 source 与 destination 两个 exact leased Notebook 均已关闭，`isolation_passed=true`、`filesystem_deleted=false`，working files 与证据按规则保留。

该 run 满足 Copy 成功后 source 跨秒漂移时的 Move 负向删源保护完成条件。非零退出是对预期 `copy_only` 的忠实表达，不是验收失败。

## 工作范围

### A. Copy/Move 合同

- 仅覆盖现有公开 Copy 和重建式 Move；不新增通用 `set_datetime`、任意资源时间编辑或其他独立 mutation 能力；
- 对 `copy_page`/`move_page` 处理目标 Page，对 Section、SectionGroup、Notebook 的 Copy/Move 只处理其实际复制范围内的 Page；空容器没有本 TODO 所要求的时间写入；
- 只读取、写入和验证 Page 根 `dateTime`，并将它归入 Page content fidelity；不修改 `createdTime`、`creationTime`、`lastModifiedTime`、最后修改者、容器时间或 Page 内部 OE/Tag 等 content object 的时间属性；
- 不要求建立 Page、Section、SectionGroup、Notebook 的通用时间字段能力矩阵，也不把公开对象模型中的 `created/modified` 映射扩展成本 TODO 的合同；
- 秒级语义相等定义为：解析 source 与 target 的带 offset 时间，归一化为 UTC 后位于同一个自然秒；忽略小数秒和原始时区表示，但不允许用可跨秒的正负容差掩盖相邻秒差异。

### B. 执行顺序与 Page 映射

1. 内部 planning 以 exact source Page ID 冻结 `dateTime`，并绑定到新生成的 exact target Page ID；不得按标题、名称或 legacy `path` 反查目标；
2. 先完成目标创建、Page content 写入、标题修正、层级创建和最终 reorder；
3. 在每个 target Page 的最后一次 content/结构 mutation 后写入其 `dateTime`，避免后续操作覆盖结果；
4. 对每个 source Page→target Page 映射逐项回读，按同一 UTC 秒比较，同时重校验 source 未发生漂移；
5. 批量或递归操作必须按范围内全部 Page 汇总结果，任何一页缺失、漂移、写入失败或秒级不一致都不能被其他成功项掩盖。

若 source Page 未返回合法 offset-aware `dateTime`，planning/preflight 必须在 `create_resources` 之前 fail closed，零创建、零写入。这不是 `copy_report.date_time.status`，也不走 `source_missing` partial。不得用当前时间、父对象时间或相邻 Page 时间补值。

### C. 结果与失败语义

- 在 `copy_report.page_results[].date_time.status` 中表达执行期 `dateTime` 结果，仅区分 `verified`、`write_failed`、`readback_mismatch` 和 `source_drifted`；不得记录 Page 正文、raw XML、原始或规范化时间；公开响应没有 `source_missing`；
- `copy_contract_satisfied` 与完整 content fidelity 结论必须纳入范围内全部 Page 的 `dateTime` 秒级回读；
- Copy 的 `dateTime` 写入失败时保留已创建 target，返回清晰的 partial/unverified 结果和 exact target ID，禁止自动重试出第二份副本；
- Move 的删源门必须依赖整批 Page `dateTime` 检查通过。任一 target Page 失败时保留所有尚未删除的 source，并按现有安全合同报告 `copy_only`；
- OneNote 对小数秒的截断以及时区文本规范化不构成失败，只要 source 与 target 仍位于同一 UTC 秒。

## 自动化与真实验证

### 自动化合同

- 覆盖现有 Copy/Move 的 source Page `dateTime` 捕获、exact Page ID 映射、最终写入顺序和回读汇总；
- 覆盖单 Page、带 Page 的子树、跨 Notebook、重名目标以及 batch/递归部分失败；
- 覆盖等价时区表示、同秒不同小数秒、相邻秒不等、缺失字段、写入失败、写入后被后续 mutation 改写、回读 mismatch 和 source 并发修改；
- 断言 Move 在任一 Page `dateTime` 检查失败时零次删源，并且不会通过补做 Copy 隐式产生重复 target；
- 将 `dateTime` 纳入 Page content fidelity，同时保持 Page 正文对象 typed equivalence 的既有职责边界。

### Human-gated 真实验证

- 现有 fresh-only `prob-timestamp-fidelity` 可继续作为 Page `dateTime` 可写能力证据，但不计作 Copy/Move 完成证据。真实执行仍只允许用户本人显式启动；
- 在 `tests/manual_validation/` 为受影响的具名 Copy/Move scenario 增加 Page `dateTime` before/after 证据；真实运行仍只由用户本人显式启动；
- 已覆盖单 Page Copy/Move 和包含 Page 的子树 Copy/Move，证明所有 Page 均按同一 UTC 秒回读；
- 已覆盖同秒但小数秒或时区表示不同的正向能力证据，以及一次跨秒回读不一致的真实负向路径，证明 Move 保留 source、保存 exact target 诊断且不执行删除；
- 证据只保存 Page ID、标准化到秒的时间和比较结论，不保存 Page content、raw XML 或用户标题。

## 非目标与安全边界

- 不处理 Copy/Move 之外的时间编辑，也不提供独立公开时间修改 tool；
- 不修改 Page 根 `dateTime` 之外的任何 datetime/timestamp 字段；
- 不把本项扩展为层级资源 `created/modified` 元数据保真、容器时间保真或审计字段保真；
- 不要求亚秒级字面一致；同一 UTC 秒即为语义保真，但不接受跨秒容差；
- 不通过直接修改 `.one` 文件实现回写；只能使用受控的 OneNote COM/XML 路径；
- 不扩大现有 Create、Writes、Deletes、Notebook Lifecycle、Local File IO 或其他 policy 权限；
- 不以无界全 Notebook 扫描寻找目标或做回读，预算按实际 source Page→target Page scope 计费；
- 不由 pytest、CI、agent 或后台任务启动真实 OneNote mutation scenario。

## 完成定义

- [x] 现有公开 Copy/Move 均以 exact Page ID 映射，在 target Page 最后一次 content/结构 mutation 后恢复 source Page 根 `dateTime`（自动化合同已覆盖）；
- [x] 所有比较均按同一 UTC 秒判定：同秒即语义保真，小数秒或时区文本差异不导致失败，相邻秒仍判为不一致（自动化已覆盖）；
- [x] `dateTime` 已纳入 Page content fidelity、`copy_report` 诊断和 Move 删源门，且未扩展到其他时间字段或容器元数据；
- [x] batch/递归结果按范围内全部 Page 聚合，局部失败不会被误报为完整成功（自动化已覆盖）；
- [x] 字段缺失在 planning/preflight 零 mutation；写入失败、回读 mismatch 或 source drift 时 Copy/Move 均 fail closed，自动化已证明 Move 零删除、零重复 Copy；
- [x] 自动化合同、受影响的 design/README 文档以及具名 `copy-page` / `move-page` 证据说明已同步；
- [x] 用户确认单 Page 与含 Page 子树的真实 Copy/Move 正向证据；共 8 个 case、12 个 Page 均按同一 UTC 秒验证通过；
- [x] 跨秒负向验证已剥离为独立 `negative-move-page-datetime-drift` 场景（fresh-only、不进 `all`、不改生产代码），纯测试与 dry-run 已覆盖；
- [x] 用户确认跨秒 mismatch 的 Move 负向删源保护真实证据；`run-2026-08-23-00-43-39` 证明 `copy_only`、source 保留、target 唯一、原 Copy 报告不改写且 `delete_hierarchy=0`。

本 TODO 已按原完成定义闭环；实现与验证过程中未新增生产注入缝或新 scenario 入口。

## 关联

- [对象模型](../design/object_model.md)：当前 `created/modified` 的公开字段映射；本 TODO 明确不修改该通用元数据合同。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：Copy/Move 内部 planning 与服务端证明职责。
- [TODO 040](040_move_readback_validation_followups.md)：Page Move 内容、标题与 typed readback 校验的历史闭环；本项把 Page 根 `dateTime` 纳入 content fidelity。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
