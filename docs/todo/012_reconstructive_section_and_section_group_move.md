# 012：跨 Notebook Section 与 SectionGroup 重建式 Move

> ID：012
> 状态：已完成
> 优先级：P2
> 类型：公开 mutation 契约 / 容器级 Copy-Verify-Delete
> 更新日期：2026-08-11

## 背景

当前生产工具已经提供：

- `copy_section`：递归复制一个 Section 及其全部 Page；
- `copy_section_group`：递归复制一个 SectionGroup、嵌套 SectionGroup、Section 和 Page；
- `delete_section` / `delete_section_group`：基于精确 ID 与 confirmation 的 typed Delete，默认执行非永久删除；
- `plan_move_page` / `move_page`：使用 Move 专属 plan digest，先复制并验证完整 Page 缩进子树，再重校验源快照，最后非永久删除源 Page；
- `reparent_section` / `reparent_section_group`：同一 Notebook 内保持容器身份的 typed 换父级。

从生产原语看，Section 和 SectionGroup 已具备实现重建式 Move 的主要组成部分。但容器 Move 不能简单地顺序调用公开 Copy 与 Delete：它必须把复制、保真验证、源重校验、非永久根删除、整棵源子树活动态消失验证和部分失败 envelope 组合为一个不可跳过的 service 合同。

同时必须避免重新混淆历史术语。此前 `move-section` 表示同 Notebook 父级变化，已经迁移并更名为 `reparent-section` / `reparent_section`。本 TODO 中的新 `move_section`、`move_section_group` 只表示**跨 Notebook、创建新 ID 的重建式转移**，不是旧行为的别名或恢复。

## 2026-08-11 实施进展

已完成代码与离线合同：

- 默认注册 `plan_move_section` / `move_section` / `plan_move_section_group` / `move_section_group`；
- 新增独立、默认关闭的 `LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS` 与 health-check 字段；
- plan 强制绑定不同的源/目标 Notebook ID，同 Notebook 在 mutation 前拒绝并指向对应 `reparent_*`；
- execute 复用生产 Copy gate，要求完整单射 `id_map`、`verified=true`、`lossless=true`、无 skipped content 和稳定源 digest；
- Section/SectionGroup 均只调用一次 typed 根删除，`permanently=false` 固定在 service 内；删除后验证全部计划源 ID 不再活动，并比较删除前后的目标子树 digest；
- 已注册 `move-section` 与 `move-section-group` 双 Notebook 场景，均设置 `included_in_all=False`，fixture 只使用 Outline/RichText，场景只审计 verified Copy→安全根删除，不重复未验证内容类型 comparator；
- 聚焦生产测试、manual-validation 纯合同和注册 dry-run 已覆盖；Agent 未运行真实 scenario。

2026-08-11，用户本人依次运行两个真实场景，完成本 TODO 的最终后端验收：

- `run-2026-08-11-20-31-28`（`move-section --use-cache`）：`copy_report.verified=true/lossless=true`，两项源对象得到完整单射映射；执行只尝试并删除一次 Section 根，`source_deleted_nonpermanently=true`、`source_deleted_to_recycle_bin=true`，根与 Page 两个源 ID 均退出活动树，`remaining_source_ids=[]`；source/destination lease 最终均为 `closed`；
- `run-2026-08-11-20-33-29`（`move-section-group --use-cache`）：`copy_report.verified=true/lossless=true`，Group/Section/Page 三项源对象得到完整单射映射；执行只尝试并删除一次 SectionGroup 根，三项源 ID 均退出活动树，`remaining_source_ids=[]`，source/destination lease 最终均为 `closed`；
- SectionGroup 运行中 COM 未暴露回收站标记，因此结果为 `recycle_bin_verification=not_required_com_unavailable`；这与既定合同一致：非永久 Delete 已执行且完整源子树从活动 hierarchy 消失，但不据此虚构 `is_in_recycle_bin=true`；
- 两个场景都只使用最小 Outline/RichText fixture，证明的是“已验证 Copy + 安全单次根删除”的 Move 编排，不扩大其他内容类型的保真 allowlist。

代码、离线合同、具名双 Notebook 场景和用户确认的真实结果均满足完成定义，本 TODO 正式标记为“已完成”。该结论只适用于已记录环境，不取消独立的实验 policy，也不外推为所有 OneNote/Office 版本保证。

## 可行性结论

结论分为两层：

- **代码实现可行性：高。** 现有 CopyService 已能捕获有界源子树、生成稳定 digest、递归创建目标、返回完整 old→new `id_map`，并按 Page 类型执行严格/语义保真验证；typed container Delete 也已存在；
- **安全交付可行性：已在当前环境得到正向证据。** Section、SectionGroup 的最小容器子树均完成跨 Notebook verified/lossless Copy、单次非永久根删除、完整源子树活动态缺席和生命周期关闭；独立实验门、内容保真门与跨版本证据边界继续保留。

实现继续依赖严格的 Copy gate，不得因为 Copy/Delete 两个单项工具存在就绕过组合操作的独立验证与 policy。

## 术语和范围决策

### Reparent

- 只允许同一 Notebook；
- 使用 `reparent_section` / `reparent_section_group`；
- 保持 Section/SectionGroup 及适用后代 ID；
- 不执行 Copy 或 Delete。

### Move

- `move_section` / `move_section_group` 只允许源与目标位于两个不同的已打开 Notebook；
- 采用 Copy→验证→源快照重校验→非永久删除源根→整棵源子树活动态消失验证；
- 目标和全部后代获得新 ID，并返回完整、单射的 `id_map`；
- 不扫描已关闭 Notebook、磁盘目录或直接操作 `.one` 文件；
- 同 Notebook 请求必须在任何 mutation 前拒绝，并提示调用对应 `reparent_*`，不能为了创建新 ID 而静默退化为 Copy/Delete。

该边界使调用方可以仅从操作名判断身份语义，也避免重新引入旧 `move-section` 的含混合同。

## 目标工具契约

建议新增四个默认注册、执行时 fail-closed 的 typed 工具：

| 工具 | 主要参数 | 语义 |
| --- | --- | --- |
| `plan_move_section` | `section_id`, `destination_parent_id`, `destination_name=""` | 只读生成跨 Notebook Section Move 计划与 Move 专属 digest。 |
| `move_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `plan_digest`, `expected_modified=null`, `destination_name=""` | 复制并验证 Section 子树，通过门限后非永久删除源 Section 根。 |
| `plan_move_section_group` | `section_group_id`, `destination_parent_id`, `destination_name=""` | 只读生成跨 Notebook SectionGroup Move 计划。 |
| `move_section_group` | `section_group_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `plan_digest`, `expected_modified=null`, `destination_name=""` | 复制并验证完整 Group 子树，通过门限后非永久删除源 Group 根。 |

目标父级继续复用 Copy 的 typed 限制：

- Section 目标只能是另一个 Notebook 或 SectionGroup；
- SectionGroup 目标只能是另一个 Notebook 或 SectionGroup；
- 目标必须处于活动 hierarchy，且所属 Notebook 必须与源 Notebook 不同；
- 目标直接子级存在大小写不敏感同名对象时拒绝，不覆盖、合并或自动改名。

## Move 专属 Plan

`plan_copy` 的 digest 不能授权后续源删除。必须使用 `operation=move_section` / `operation=move_section_group` 的独立 plan digest，至少绑定：

- 源根精确 ID、类型、名称、父级、modified 与完整有界子树 snapshot；
- 每个 SectionGroup/Section/Page 的身份、父子关系、Page order/level 和源 Notebook；
- 每个 Page 的稳定内容摘要、内容能力、二进制 hash、验证 tier 和 Copy issue；
- 目标父级精确 ID、类型、Notebook、名称、modified 与现有直接子级；
- 源/目标 Notebook 不相同的证明；
- 目标名称和名称冲突检查；
- 预计创建的 Group/Section/Page/内容对象数量及适用 Copy budgets；
- `verify_copy`、`revalidate_source`、`delete_source_root_nonpermanently`、`verify_source_subtree_inactive` 与 `revalidate_destination` 步骤；
- 外部入站链接不会被扫描或重写的明确 warning。

Execute 必须重新生成相同 operation 的 fresh plan；`plan_copy`、`plan_move_page`、另一容器类型或任何 stale digest 都必须在 mutation 前拒绝。

## 严格执行状态机

```text
require Writes + Experimental Copy + Deletes + Container Move
→ confirm exact source ID/name/parent/modified
→ rebuild and match Move-specific plan digest
→ copy complete source subtree
→ require complete injective id_map
→ require copy_report.lossless=true and verified=true for every Page
→ require no skipped/unknown/unverified content
→ recapture source subtree and match original source digest
→ issue exactly one typed root Delete with permanently=false
→ read active hierarchy until bounded deadline
→ require every original source subtree ID absent from active hierarchy
→ revalidate destination mapping/topology/content
→ return outcome=moved
```

任何检查失败都不得继续下一阶段。尤其：

- Copy 创建或 read-back 失败：返回 `copy_only`/`copy_unverified` 的归一化 partial failure，源保持活动；
- Copy 有 issue、非 lossless、非 verified 或 `id_map` 不完整：`outcome=copy_only`，禁止源删除；
- Copy 后源 digest 变化：`outcome=copy_only`，禁止源删除；
- 源根 Delete 调用失败：保留已验证目标，返回 `source_delete_failed`；
- Delete 返回后仍有部分源 ID 活动：返回 `source_partially_removed`，记录精确 removed/remaining IDs，不猜测恢复；
- 源已消失但最终目标回读失败：返回 `source_removed_destination_revalidation_failed`，不得虚报成功或自动删除目标；
- 所有部分失败都保留目标、源/目标 Notebook 和 evidence，不执行破坏性自动回滚。

## 容器删除策略

Page Move 需要从叶到根逐个删除 Page 缩进子树；Section/SectionGroup Move 应采用不同的受约束策略：

- 只对源 Section 或 SectionGroup 根调用一次 typed `delete_section` / `delete_section_group`；
- 永久删除参数在 service 内硬编码为 `false`，公共 Move tool 不接受 `permanently`；
- 删除前保存全部源后代 ID；删除后验证所有这些 ID 都已从活动 hierarchy 消失；
- `is_in_recycle_bin=true` 是正向诊断证据，但 COM 不暴露旧 ID 时，活动 hierarchy 中精确缺席仍可作为成功关口；
- 如果根已消失但后代仍以异常活动对象存在，必须报告 partial failure 和精确剩余 ID；
- 不逐个删除后代 Page/Section 来“完成”失败的根删除，避免扩大不可逆的部分状态。

## 保真与身份门限

源删除只有同时满足以下条件才允许：

- `id_map` 覆盖计划中每个源 Group、Section 和 Page，target ID 全部唯一；
- 映射后的父子拓扑、Section/Page 顺序、Page level 与缩进关系等价；
- 每个 Page 使用其静态验证 tier 通过内容、可见文本、结构和二进制比较；
- `copy_report.skipped_content` 为空，且没有 unknown/unverified capability；
- 目标根位于计划绑定的目标父级和目标 Notebook；
- Copy 前后源子树稳定 digest 完全一致；
- Copy 未改变源/目标 Notebook 中的无关对象；
- 全部工作处于 Copy resource/Page/XML/time budgets 内。

当前静态 allowlist 中的 `Outline/Image/RichText/Table/List/Tag` 仍只代表已有指定环境证据。`FileAttachment`、`InsertedFile`、`InkDrawing`、`MediaFile`、`MeetingInfo` 或未知节点继续阻止 Move 删除源；本 TODO 不因容器层级扩大而放宽内容保真门。

## Policy 与注册

实现不复用名称和风险范围都只针对 Page 的 `LOCAL_ONENOTE_ENABLE_MOVE_PAGE`，而是使用：

```text
LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS=true
```

以及 health-check 字段：

```text
move_containers_enabled
```

执行 `move_section` / `move_section_group` 必须同时满足：

- `LOCAL_ONENOTE_ENABLE_WRITES=true`；
- `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY=true`；
- `LOCAL_ONENOTE_ENABLE_DELETES=true`；
- `LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS=true`；
- 永久删除与 Raw XML 不需要且不得由 Move 路径使用。

`LOCAL_ONENOTE_ENABLE_MOVE_PAGE` 保持现有语义，避免不相关的配置破坏。容器 Move 使用独立门是因为单次 SectionGroup 操作可能影响多层 Group、Section 和 Page，风险与 Page Move 不同。

## 建议生产代码重构

### 1. 共享受约束 Move engine

在 `CopyService` 内提取只接受枚举/typed strategy 的内部 Move pipeline：

- 共用 Move 专属 plan、Copy execution、fidelity gate、源 digest 重校验和 partial envelope；
- Page strategy 保持叶到根 Page 删除；
- Section/SectionGroup strategy 只允许一次根删除并执行整棵活动态缺席验证；
- tool adapter 仍分别暴露 typed 参数，不提供 `resource_type` 字符串或 generic `move_object` 公共工具；
- internal strategy 不能接受任意 delete callback，删除函数必须由受审查的资源类型固定映射。

### 2. 扩展计划 operation

将当前只特殊处理 `move_page` 的 `_build_plan()` / `_public_plan()` 收敛为受限 Move operation 集：

```text
copy
move_page
move_section
move_section_group
```

不同 operation 必须进入 digest，且公开 `execute_tool`、steps、warnings 与 source type 精确匹配。未知 operation 立即拒绝。

### 3. Source-subtree 删除验证

新增 bounded helper，输入只能是 plan 中的源 ID 集合和 resource type：

- 在 deadline 内重新枚举活动 hierarchy；
- 计算 `inactive_source_ids` 与 `remaining_active_source_ids`；
- 可选收集 recycle metadata，但不按名称或路径重新定位对象；
- 不因读不到 recycle bin 就切换到永久删除或再次删除；
- 返回结构化证据供成功和 partial failure 共同使用。

### 4. 最终目标复核

源删除后再次按 `id_map` 读取目标子树，证明目标身份、拓扑和内容仍等价。该步骤失败时源可能已经删除，因此必须返回独立的高严重度 partial outcome，不能降级为普通 `copy_only`。

## Manual-validation 场景

新增两个扁平、具名、HUMAN-GATED 场景：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py move-section --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py move-section-group --dry-run --json
```

真实命令仍只能由用户本人显式启动。两个场景保持 `included_in_all=False`；本次真实评审完成不等于自动取得 `all` 批处理资格，后续若要纳入仍需独立权限与稳定性审查。

### 双 Notebook fixture

跨 Notebook 合同要求场景创建两个全新的 disposable Notebook：

- source Notebook：包含编号 Section/SectionGroup 源子树、严格富内容父页与 List/Tag 语义子页，以及无关锚点；
- destination Notebook：包含合法目标 Notebook/SectionGroup 父级和无关锚点；
- 所有对象都由本次 run 的精确 lease/manifest 绑定，不接受外部 Notebook ID 或名称选择。

当前 lifecycle wrapper 主要管理一个 source Notebook。实施时必须扩展为角色化多 lease，例如 `source` / `destination`，并保持：

- 在启动唯一 scenario MCP process 前创建两个精确 Notebook；
- 两个 lease 分别绑定 ID、名称、本地路径和状态；
- 成功时按精确 lease 关闭两个 Notebook，但绝不删除本地文件；
- `--keep-worksite` 保持两个 Notebook 打开并记录清理要求；
- mutation、copy_only、删除不确定、目标复核或 close 失败时保持两个 Notebook 和全部 evidence；
- lifecycle wrapper 仍不能成为通用 hierarchy mutation 入口。

场景 after 证据至少验证：

- 目标全部映射、层级拓扑、Page 内容和二进制；
- 源根和全部后代从 source Notebook 活动 hierarchy 消失；
- source/destination 两边无关锚点不变；
- Move 调用使用正确 typed tool、Move 专属 digest 和 `permanently=false`；
- 回收站 metadata 只作诊断，不替代活动态缺席门限。

历史 CLI 的 `move-section` 拒绝测试需要重新定义：新名称不再代表旧的同 Notebook 行为，而是新的跨 Notebook 重建式场景。合同测试必须证明同 Notebook 参数被拒绝并提示 `reparent-section`，不能通过兼容别名恢复旧语义。

## 自动化合同要求

- 四个新 tool 的 schema、默认注册、独立 policy 矩阵和 health-check；
- `plan_copy`/其他 Move digest 不能授权 container Move，stale digest 在 mutation 前拒绝；
- source/destination Notebook 相同、目标类型错误、名称冲突、回收站对象和 confirmation 不匹配均在 Copy 前拒绝；
- 完整 `id_map`、单射映射、拓扑、Page tier、二进制、unrelated objects 和 budget gate；
- Copy partial、unverified content、源变化、删除失败、部分源残留和最终目标复核失败的独立 outcome；
- 永久删除始终为 false，Move tool 不暴露永久删除参数；
- Section/SectionGroup 只调用一次根删除，失败后不逐个删除后代；
- 回收站 metadata 缺失但全部源 ID 活动缺席时可以成功，并产生 warning；
- 双 Notebook lifecycle lease、失败保留、`--keep-worksite` 和两个 Notebook 的精确 close；
- dry-run 不创建 Notebook、目录、MCP 或 COM，并展示两个 lifecycle role、静态 policy、allowlist、budgets 和 ordered steps；
- pytest、CI、hook、timer、watcher 或 Agent 绝不能运行不带 `--dry-run` 的新场景。

## 风险与缓解

### P0：未完全验证 Copy 就删除容器源

风险：Section/SectionGroup 包含多页或多层后代，任一内容/拓扑遗漏都会在源删除后造成数据损失风险。

缓解：Move 只接受完整且 lossless/verified 的 Copy report；任何 issue 或未知类型返回 `copy_only`；源删除路径保持独立默认关闭，并要求真实 Copy 场景证据。

### P0：根删除后的部分状态

风险：COM 返回成功但部分后代仍活动，或读取失败导致状态不确定。

缓解：保存全部源 ID，执行一次非永久根删除，随后逐 ID 验证活动态缺席；剩余对象精确报告并停止，不追加后代删除或自动回滚。

### P1：Reparent 与 Move 语义再次混淆

风险：调用方把同 Notebook `move_section` 当成旧换父级行为，错误接受新 ID 和删除语义。

缓解：Container Move 强制跨 Notebook；同 Notebook fail closed 并指向 `reparent_*`；计划、tool help、README、错误消息和 scenario Description 都明确新 ID/非永久删除。

### P1：双 Notebook lifecycle 扩大 Runner 可信边界

风险：close 错 Notebook、复用用户 Notebook 或失败时关闭现场。

缓解：两个 Notebook 都由本次 run 创建，使用角色化精确 lease 绑定 ID/name/path；任一不匹配时不关闭；不得按名称重新发现或删除文件。

### P1：外部链接失效

风险：源子树之外指向旧 Section/Group/Page ID 的 OneNote 链接不会随重建自动更新。

缓解：计划与结果始终警告“外部入站链接未扫描”；只重写 Copy engine 已明确识别的子树内部链接；不声称链接全局保真。

### P2：超大 SectionGroup 超时或超过预算

风险：多层 Group/Page 导致 plan、Copy、源重校验和最终回读耗时过长。

缓解：沿用并明确累计 Copy resource/Page/object/XML/time budgets；预算超限在删除前失败；不得按子树分片后分别删除源。

### P2：与测试框架 TODO 的实现冲突

风险：新增两个场景会扩大当前集中式 `fixtures.py` 和手工 dry-run 列表。

缓解：建议先完成 [TODO 011](011_scenario_owned_fixture_recipes.md) 的 Scenario-owned recipe 骨架和 [TODO 010](010_registered_dry_run_test_cases.md) 的自动 dry-run case 接口，再添加 container Move 场景；若先实现生产工具，manual scenario 仍必须在同一交付中完成，不能以框架重构为理由省略。

## 依赖与实施顺序

1. 保持 [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) 的术语边界：同 Notebook 始终使用 Reparent；
2. 以已完成的 [TODO 002](002_p2_copy_and_reconstructive_page_move.md) 为基线：用户已确认 `copy-section`、`copy-section-group` 的最终真实闭环，当前静态内容 tier 在容器复制中成立；
3. 建议先落地 TODO 011/010 的 Scenario recipe 与 dry-run registration 基础，避免新场景继续扩大中央模块；
4. 实现 Move 专属 plan、policy、typed tools、service pipeline 与纯合同测试；
5. 实现双 Notebook lifecycle 和两个不进入 `all` 的具名场景；
6. 用户先运行 `move-section`，确认较小容器的成功/失败边界；再运行 `move-section-group`；
7. 未验证内容类型继续由 [TODO 004](004_interactive_copy_move_content_fidelity_validation.md) 跟踪，不阻塞代码识别，但必须阻止源删除；
8. 用户确认真实证据后再更新能力矩阵；单环境结果不外推为跨版本保证。

## 非目标

- 不实现 Notebook Move 或 Notebook Delete；
- 不让 container Move 支持同 Notebook，或替代 `reparent_section` / `reparent_section_group`；
- 不保留旧 `move-section` 到 Reparent 的别名；
- 不覆盖、合并、自动改名或按名称选择目标；
- 不扫描已关闭 Notebook、全局入站链接或直接编辑 `.one` 文件；
- 不在 Copy/Move 失败时自动永久删除目标或源；
- 不因容器 Move 需要而放宽未知内容、budget、confirmation、Copy fidelity 或 Delete policy；
- 不把 mock、dry-run 或 Page Move 的成功/失败证据当作 Section/SectionGroup Move 的真实通过证据。

## 完成定义

- `plan_move_section`、`move_section`、`plan_move_section_group`、`move_section_group` 具有稳定 typed schema，并默认注册、执行时独立 fail closed；
- 新 Move 只允许跨已打开 Notebook；同 Notebook 请求在 mutation 前拒绝并明确指向对应 Reparent 工具；
- Move-specific digest 绑定源/目标快照、内容能力、预算、operation 和非永久根删除步骤，Copy plan 不能授权 Move；
- 所有源对象都有完整单射 old→new `id_map`，目标身份、拓扑、Page 内容和无关对象验证通过后才可能删除源；
- 源在 Copy 后重新捕获且 digest 未变化；任何变化产生 `copy_only/source_deleted=false`；
- Section/SectionGroup 仅执行一次 `permanently=false` 的 typed 根删除，并验证全部原源 ID 从活动 hierarchy 消失；
- Copy、源重校验、删除和最终目标复核的每类失败都有明确 outcome、created/removed/remaining IDs 和失败保留证据；
- `LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS`、health-check、README、设计文档和 policy 测试完成，现有 Page Move 配置保持兼容；
- `move-section`、`move-section-group` 具名场景使用两个 fresh disposable Notebook、单 MCP、角色化 lease、静态最小权限和 `included_in_all=False`；
- manual-validation 纯测试与完整 pytest 通过；所有 Agent 执行的新场景命令都显式带 `--dry-run`；
- 用户分别确认 `copy-section`、`copy-section-group` 以及两个新 container Move 场景的真实证据；失败或未运行不能标记为通过；
- 当前 tool contracts、对象模型、architecture、manual-validation README、TODO 002/004/009/010/011 和 TODO 索引与最终实现一致。
