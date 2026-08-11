# 015：Mutation 目标精确定位收尾与重名 Page 回归

> ID：015
> 状态：已完成
> 优先级：P1
> 类型：Mutation 安全 / 精确 ID 定位与真实回归
> 更新日期：2026-08-11

## 背景

阶段 B 双 Notebook `copy-page` 真实验证首次越过 fixture/cache 与 plan evidence 后，暴露了一个生产 mutation target 定位错误。用户运行 `run-2026-08-11-14-40-17` 时：

- `same-section-root-only` 成功创建全新 Page ID，并通过严格内容与拓扑回读；
- `same-section-subtree` 为复制根分配了新 ID，但把源子页 ID 同时记录成复制子页的目标 ID；
- 后续正文写入和 `UpdateHierarchy` 重排因此作用到源子页，`verify_copy` 正确返回 `partial_failure`，没有继续跨 Section case；
- 响应中的 `source_untouched=true` 与真实已执行步骤矛盾，不能作为该失败现场的可信结论；
- working Notebook bundle 按失败语义保留，immutable cache template 没有被工作副本反向刷新。

根因是创建回读同时接受 `candidate.path == expected_path` 或 `candidate.id == allocated_id`，并返回 hierarchy 列表中的首个匹配项。同一 Section 允许重名 Page；当新建子页与源子页同名时，旧 Page 先命中相同 friendly path，遮蔽了 COM 返回的精确新 ID。

这个缺陷不仅是 Copy comparator 问题，而是“创建身份、mutation target 与最终落点”之间的安全边界错误。修复必须保证名称和路径只用于无歧义发现，任何写入、层级更新、删除或后续 confirmation 都绑定精确 ID。

## 当前实施状态

截至 2026-08-11，生产代码、自动化矩阵、原场景增强、用户真实复验与文档收尾均已闭合。增强后的 `create`、v4 `copy-page` 与 `move-page` 都取得用户真实成功证据；v4 过程中依次暴露并修复了空 selection `<T>` 比较误报、Copy 转换标题拼接和最终 restore 对无关 Description Page 过宽比较三个问题。最终 `run-2026-08-11-16-18-20` 已完成六 case、cleanup/restore、双 Notebook close 与顶层 `passed`：

- `HierarchyService.wait_for_created()` 改为优先精确 `allocated_id`；只有精确 ID 未出现且路径唯一时才允许 path fallback，重复路径 fail closed；
- `CopyService._execute_copy()` 在写正文或重排前拒绝源 ID 命中、多个源映射同一目标、错误 resource type、错误目标 Section 或错误容器父级；
- 自动化测试已覆盖 allocated ID 优先、重复路径歧义拒绝和 Copy create read-back 命中源 ID；
- Page Copy 当前落点合同已澄清：`destination_section_id` 只标识 Section，既有 Page 顺序保留，新复制块追加到末尾，根归一化为 level 1，后代恢复相对 `page_level`；
- 完整 pytest 在最终 restore 门修复后通过（`533 passed`）；manual-validation 纯测试 `332 passed`，受影响的 `copy-page --use-cache --dry-run --json` 与 `git diff --check` 通过；Agent 未运行真实 scenario。
- 用户随后运行 `run-2026-08-11-14-54-05` 与 `run-2026-08-11-14-57-01`，同 Section、跨 Section、跨 Notebook各自 root-only/subtree 的六个 `copy-page` case 连续两次全部 `verified=true`、`lossless=true`；所有 subtree 的父子 target ID 均为 fresh ID，未再复用 source child ID。前一 run 完成默认 cleanup/restore/close，后一 run 按 `--keep-worksite` 保留现场。这两次 run 证明核心 helper 修复，但跨 Section/Notebook destination 当时尚未预置同标题 anchors，不能充当 v4 完成证据。
- public Create 现在同时校验 allocated ID、type、friendly path、active state、计划父级与 before-ID 集；只有 allocated ID 不可见且存在唯一 fresh typed path match 时才记录一对一 remap。`create_page` 在任何初始化正文写入前拒绝既有/forbidden ID。
- 四类 Copy 共用 `_validate_created_target()`，并在 Page 正文写入与 `UpdateHierarchy` 前完成 source/target disjoint、target uniqueness、type/parent/recycle 校验；partial evidence 现在区分 `allocated_ids/resolved_target_ids/possibly_untracked_allocated_ids/source_touched/topology_touched/manual_recovery_required`。
- `copy_section/copy_section_group/copy_notebook` 的自动化重复标题矩阵通过，共享 primitive 未出现容器分支差异，因此没有触发三个真实容器 Scenario 的升级门。
- Advanced `delete_hierarchy` 已从全部生产注册与 service 公共入口移除；`open_hierarchy` existing path 重复时拒绝；`merge_sections/set_filing_location` 改为 exact typed ID 参数。兼容 `find_resource_by_path` 仅保留给只读调用，新 unique/all-match 接口承担安全调用。
- 原 `create` Recipe/Scenario 已加入空 `Duplicate-Title-Target`、连续同标题 Create、allocated/read-back/before-after 证据和默认 exact non-permanent cleanup；原 `copy-page` v4 已在跨 Section/Notebook destination 各加入同标题不同正文 anchor，并逐 case 验证 anchor hash/order/level/parent；原 `move-page` Recipe/Scenario 已加入同名不同正文 anchor、fresh-ID/anchor/source-delete gates。
- 用户运行 `run-2026-08-11-15-41-20`：两次同标题 Create 的 allocated/read-back IDs 完全一致且互异，均为 fresh、正文可独立读取；默认 exact non-permanent cleanup、`restored=true` 与 lifecycle close 全部完成。
- 用户运行 `run-2026-08-11-15-43-26`：Move 的两页 `allocated_ids/resolved_target_ids/id_map` fresh 且互异，Copy `verified=true/lossless=true`，collision anchor 不变，之后才按叶到根执行非永久源删除；活动源子树消失并完成 lifecycle close。
- 用户运行 v4 `run-2026-08-11-15-46-34`：validated-hit materialization、双 role live validation、两个同标题 anchors 与前三个 Copy 均成功；三个 Copy 各自返回 fresh target、`verified=true/lossless=true`。第三个 case 后 runner 因 Description Page 与源 Parent 的 stable hash 改变而 fail closed 并保留双 Notebook。保存 evidence 证明 Description 与 Parent 的内容对象/能力投影不变，源 Parent 的 canonical 差异恰好由 OneNote 后补的空 `<T selected=...>` 视图节点解释，并非 created-target alias 或 anchor/source 写入。
- comparator 现在只忽略“空、无子节点且仅携带 `selected/isSelected`”的 T 占位；普通空 T、非空文本、格式、二进制和内容对象 ID 仍严格比较。六 case 的不变性门显式绑定 source Parent/Child 与两个 manifest anchors，不再让非验收目标的 Description Page 后台规范化冒充业务内容损坏。
- 用户在 comparator 修复后运行 v4 `run-2026-08-11-16-06-07`：同 Section root-only/subtree 均为 fresh、`verified=true/lossless=true`；跨 Section root-only 创建并精确回读了 fresh target，但 strict read-back 发现目标标题变成“目标标题 + 原标题”并返回 `copy_unverified`，未继续后三个 case。保存的 source XML 行为与前一 run 一致：空 selection T 位于真实标题 T 之前；Copy 转换旧顺序先删除其 selection 属性，使 `_set_title()` 错把该空节点写成目标标题并保留原标题。转换现在在剥 volatile 属性之前移除且只移除严格定义的空 selection T；普通空 T 与带可见内容的 selected T 保留并参与回读。
- 用户运行最终转换修复后的 v4 `run-2026-08-11-16-11-01`：六个 case 全部 `verified=true/lossless=true`，每个 root/subtree mapping 均通过 fresh/disjoint、目标 Section、order/level、source/anchors 不变门。默认 cleanup 已执行，保存的 `before/restored` 显示 11 个原对象的 ID/父级/order/level 完全一致、全部 Page 对象身份与能力投影一致、四个保护页稳定内容一致；旧通用 restore 比较仅因无关 Description Page 后台重序列化返回 `needs_manual_restore`，因而没有进入 lifecycle close。
- Page Copy 最终 restore 现在使用与逐 case 相同的证据边界：全 bundle 身份/拓扑、全部 Page 对象身份、全部能力投影严格相等，并对 manifest-bound source Parent/Child 与两个 anchors 额外要求稳定内容相等；它不再让非验收 Description Page 的文本重序列化单独否定已证明的清理恢复。
- 用户运行最终 v4 `run-2026-08-11-16-18-20`：fingerprint `05a513f7de2fddf635795dcf107e0109b4010b159e30d4d3bec9617170787581` 为 `validated_hit` 且 `opened_template=false`；六 case 按 `1/2/1/2/1/2` 映射 9 个 fresh、互异、与 before/source/anchors 不相交的 target。所有 Copy 均 `verified=true/lossless=true`，三个 subtree child 均为 fresh level 2 并归属 fresh root，root-only 均未复制 Child，两个显式 anchors 与 source Parent/Child 的拓扑、稳定内容和对象身份不变。
- 同一 run 反向精确非永久清理全部 9 个 target，`restored=true`、`worksite_preserved=false`；source/destination 两个 working Notebook 均为 `closed_preserved`，本地 working 文件未删除。cache template 两个 role 的逐文件 inventories 全部 `unchanged=true`。全程仅启动 1 个 scenario MCP process，记录 223 次 MCP tool call 与 594 次 bridge call。
- 完整纯自动化测试（`533 passed`）、manual-validation 纯测试（`332 passed`）、受影响的 `copy-page --use-cache --dry-run --json` 与 `git diff --check` 已通过；Agent 未运行真实 scenario。

旧两次六 case 证据闭合了核心 Page Copy bug 与 TODO 014 阶段 B 的既定空目标矩阵；新 Create/Move run 闭合对应真实门，最终 v4 run 闭合增强后 destination anchors、identity、fidelity、cleanup/restore、cache immutability 和 lifecycle 门。本 TODO 的完成定义现已全部满足。

## 扩散审计结论

| 功能面 | 风险 | 当前判断 |
| --- | --- | --- |
| `create_page` | 同 Section 重名时可能返回旧 Page ID，并遗失真正 allocated Page 的跟踪 | 直接受影响；实现、自动化与增强后的真实 `create` 证据已完成 |
| `copy_page` | 子树后代保留源标题，可能与源 Section 或非空目标 Section 的 Page 重名 | 已完成；ID 防碰撞与增强后六 case、9 targets cleanup/restore、双 Notebook close 和 cache immutability 均有最终真实证据 |
| `move_page` | 复用 Copy 重建；误映射既可能写错对象，也可能使后续源删除判断失真 | 直接受影响；实现、自动化与增强后的 anchor/fresh-ID/source-delete 真实证据已完成 |
| `copy_section` / `copy_section_group` / `copy_notebook` | 源 Section 自身存在同名 Page 时，递归创建第二个同名 Page 可触发同一 helper 缺陷 | 条件性受影响；共享实现和三类重复标题自动化矩阵通过，未触发真实 Scenario 升级门 |
| Programmatic fixture build | Recipe 创建重名 Page 时可能把错误 ID 写进 manifest/snapshot | 条件性受影响；原场景增强不得依赖 path-only 地址区分重名 Page |
| Cache lifecycle | Notebook/Section 激活共用 `wait_for_created` | 旧 wrapper 另有 exact ID、parent 和实际 working path 门禁，主要风险为 false failure；仍需保留回归 |
| Typed Reparent | 从 target/destination 精确 ID 开始，并验证唯一 old→new Page ID、父级、内容与无关对象 | 不经过该 helper，未发现直接扩散 |
| Reorder / typed Delete / Page 内容 mutation | 使用精确 ID 加 confirmation，并按 ID 回读 | 未发现直接扩散 |
| Advanced `delete_hierarchy` | 首次删除后按原 path 追踪并继续删除新 ID；重名 Page 可能被误当成同一对象 | 已从所有生产注册与 service 公共入口移除；bridge operation 只供 typed Delete/Move 内部使用 |
| Advanced `open_hierarchy` | existing-path 分支使用首个 path match | 已改为 unique exact-path；重复路径在 bridge 前 fail closed |
| Advanced `merge_sections` / `set_filing_location` | 接受通用 identifier，而不是公开 schema 级 exact ID | 已迁移为 exact Section/Page ID 参数并具 schema/service 合同 |

## 目标安全合同

### 创建回读

- COM create/open 返回的 object ID 是第一身份来源；只要该 typed ID 可回读，就不能由名称或路径覆盖。
- path fallback 仅用于后端明确重映射 ID 的兼容路径，且必须恰好一个 typed match；零个继续有界 retry，多个立即保持歧义并最终 fail closed。
- 返回对象必须同时满足 resource type 与计划父级；Page 额外满足目标 Section。
- public Create 成功响应中的 ID 必须是本次操作后精确回读的对象；无法证明时返回带 allocated ID 的 partial failure。

### Copy / Move

- 每个 source ID 必须映射到一个全新、互异、typed target ID；`source_ids ∩ target_ids == ∅` 且 target IDs 无重复。
- 所有 ID、类型和父级检查在任何 copied Page 正文写入和层级重排前完成。
- Page Copy 的目标是 Section，不接受父 Page 作为 destination；复制块追加策略与相对 level 必须按 fresh IDs 回读。
- Move 只有在目标全部 fresh、内容/拓扑 verified、源快照仍当前时才允许非永久删除源；任一别名、歧义或未跟踪 allocated object 都保持 `source_deleted=false`。
- partial failure 必须准确区分已知 allocated IDs、已确认 target IDs、可能未跟踪对象、源是否实际被触及以及人工恢复要求，不能默认声称 `source_untouched=true`。

### Advanced profile

- 默认 profile 继续不注册 generic/raw mutation 工具。
- `delete_hierarchy` 不得仅凭相同 path 追删另一个 ID。首选方案是改成 exact ID + confirmation 的单对象删除；若不再有必要，则从 advanced profile 移除。
- `open_hierarchy` 的 existing-path 读取在重复 typed path 时拒绝，不返回首个候选。
- `merge_sections`、`set_filing_location` 评估迁移为 exact ID 参数；若保留 identifier，必须证明所有歧义均在 mutation 前拒绝。

## 生产代码收尾

1. 提取统一的 created-target validator，供 Copy 的 Page/Section/SectionGroup/Notebook 创建使用，避免不同分支重新形成宽松判断。
2. 在 `MutationService.create_page` 返回前显式断言 read-back ID 等于 allocated ID，或记录后端可证明的一对一 remap；普通同名 path 不构成 remap 证据。
3. 审查 `create_notebook/create_section/create_section_group/open_hierarchy` 的“打开既有对象”和“创建新对象”语义，确保 public Create 不把既有对象静默报告为新建成功。
4. 改进 Copy partial-failure evidence：按顺序记录 `allocated_ids`、`resolved_target_ids`、`id_map`、`source_touched`、`topology_touched` 与 `manual_recovery_required`，字段只陈述已经证明的状态。
5. 对 `delete_hierarchy` 作删除、exact-ID 重构或明确保留的决策；不得继续用 path 首匹配追删。
6. 对 `find_resource_by_path` 增加唯一性接口，mutation 调用者不得使用返回首项的便利方法；兼容只读调用必须明确其歧义行为。
7. 检查 tool schema、tool 描述、README、object model、tool contracts 和 advanced profile 文档，保持 exact-ID mutation 边界一致。

## 自动化回归矩阵

### Hierarchy 与 Create

- 同类型两个 Page 具有相同 path，allocated ID 位于第二项时必须返回 allocated Page。
- allocated ID 不可见且 path 唯一时允许兼容回退；path 重复时返回 `None`/结构化歧义，不返回首项。
- allocated ID 命中错误类型、错误 Section 或 recycle-bin 对象时拒绝。
- `create_page` 连续创建两个同标题 Page，响应 ID 必须互异，且第二次不能返回第一次 ID。
- 初始化正文、title 回读或 identity 验证失败时，partial response 保留真实 allocated ID，不猜测 resolved target。
- Notebook/Section/SectionGroup create/open 覆盖 existing path、stale returned ID、唯一 remap 和歧义失败。

### Copy 四类型

- `copy_page` 覆盖同 Section、跨 Section、跨 Notebook三类目标；每类均覆盖 root-only 与 subtree，并在 subtree 目标 Section 预置与源子页同名的 anchor。
- 所有 case 断言 target ID 集合与 before ID 集合不相交、每个 source 恰好一个 target、anchor/source 内容与层级不变。
- `copy_section` 使用含两个同标题 Page 的源 Section，验证目标 Page ID 互异、内容分别对应、顺序与 level 正确。
- `copy_section_group` 与 `copy_notebook` 在后代 Section 重复上述同标题 Page 回归，证明共享 create primitive 没有被容器分支绕过。
- 注入 create read-back 返回源 ID、前一个 target ID、错误类型和错误父级，全部在 `write_page_content`/`reorder_pages` 前 partial fail。
- 目标创建后 read-back 超时必须报告所有已知 allocated IDs，不自动回滚、覆盖、改名或猜测 path。

### Move、Reparent、Reorder 与 Delete

- `move_page` 的 Copy 阶段复用同标题 destination anchor；只有 fresh target mapping 全部验证后才进入源删除。
- 任一 target alias/歧义注入时断言没有 `delete_hierarchy` 源调用，`source_deleted=false`。
- Reparent、Reorder、typed Delete 和 Page 内容 mutation 增加负向合同，证明其 mutation target 不调用 `find_path/resolve(name)` 或 created-path fallback。
- Advanced `delete_hierarchy` 覆盖相同 path 的另一个 Page：删除原 ID 后不能继续删除 sibling；如果工具被移除，则注册表和迁移错误合同覆盖不可调用。
- Advanced `open_hierarchy` 重复 path 必须报歧义；`merge_sections`/`set_filing_location` 的 exact-ID 或唯一解析决策有 schema 与 service 测试。

### Manual-validation 与 cache 纯合同

- 原有 Scenario registry、静态最小权限、`--keep-worksite`、失败保留、cache template immutability 和双 Notebook lifecycle 不改变。
- Recipe version/fingerprint 在 fixture 内容变化时提升，旧模板不能命中新回归合同。
- duplicate-title fixture 不得依赖 path-only manifest address；如果 cache ID remap 无法唯一映射重名 Page，相关 collision Page 只作为 scenario 执行期 disposable target 创建，或先扩展可证明唯一的 recipe-owned地址模型。
- pytest、CI、hook、import、timer 和 Agent 继续不能启动真实 scenario。

## 原 Scenario 的 Manual-validation 增强

不新增 `duplicate-name-*` 旁路 scenario。回归内容直接进入已有 Recipe 和执行合同；真实命令仍只由用户运行。

### `create`

- 在原 Recipe 增加一个空的 `Duplicate-Title-Target` Section，作为受 manifest 约束的 disposable 创建区。
- 在原 `create` Scenario 增加连续创建两个同标题 Page 的 case。
- 保存每次 COM allocated/read-back ID、Section ID 和 before/after hierarchy；成功要求两个 fresh ID 互异、均属于目标 Section，且正文分别可回读。
- 默认 restore/cleanup 继续按精确 ID、非永久方式执行；失败保留现场。

### `copy-page`

- 保留当前同一 source Page 的 `3 destination scopes × 2 subtree modes` 六 case，不新建第二个 Copy scenario。
- Source Section 中现有源子页自然形成 same-section 同名碰撞条件；在 source role 的跨 Section 目标和 destination role 的跨 Notebook 目标各增加一个与源子页同标题的 anchor Page。
- 两个 anchors 使用不同正文标记并进入 before hash，确保 comparator 能证明没有被覆盖或重排。
- 六 case 都记录 destination before IDs、allocated IDs、最终 `id_map`、order、level、derived parent；subtree case 明确断言 copied child 是 fresh ID，且不等于源子页或同名 anchor ID。
- root-only 继续证明不创建子页；subtree 继续证明新块追加、根 level 1、child level 2、源子树和所有 anchors 不变。
- 失败现场继续保持双 Notebook 打开，不执行后续 case、cleanup、close 或 cache publish。

### `move-page`

- 在原 Move Recipe 的目标 Section 加入一个与源子页同标题、不同正文的 anchor Page。
- 保留原完整子树 Move case；Copy 阶段必须返回全部 fresh target IDs，anchor hash/order 不变。
- 在 target fidelity、topology、fresh-ID 和源快照门全部通过前，证据必须显示零源删除调用。
- 成功后仍只执行非永久、叶到根源删除，并验证活动树中源 IDs 消失；失败继续按 `copy_only`/`copy_unverified` 保留现场。

### 容器 Copy

- `copy-section`、`copy-section-group`、`copy-notebook` 继续使用各自原 Scenario，不新增替代入口。
- 第一阶段以共享生产 primitive 的自动化重复标题矩阵和现有真实容器 Copy 证据为门；不为了制造重复 path 破坏当前 cache 的唯一 typed-address rebind。
- 若自动化或后续真实结果表明容器分支与 Page Copy 不同，则在对应原 Recipe 内通过“先创建唯一标题并记录 ID，再按精确 ID Rename 为重复标题”的方式加入两个同名 Page，并同步扩展 cache remap 的唯一身份合同；不得按 path 任选一个 Page。
- 该升级需要分别提升相关 Recipe version，并由用户重跑原 `copy-section`、`copy-section-group`、`copy-notebook`，不能用 `copy-page` 的真实结果冒充容器证据。

## 用户真实复验顺序

1. 已完成：此前失败的 `run-2026-08-11-14-40-17` working bundle 按失败语义保留，未写回 cache template。
2. 已完成：`run-2026-08-11-15-41-20` 证明 public Create 在同标题下返回两个 fresh、互异、正文独立可读的 IDs，并完成默认非永久 cleanup/restore/close。
3. 历史核心证据：用户运行 `run-2026-08-11-14-54-05` 与 `run-2026-08-11-14-57-01`；两次旧 Recipe 六 case 均通过，但跨 Section/Notebook 当时没有同标题 anchor。
4. 诊断证据：v4 `run-2026-08-11-15-46-34` 因空 selection T comparator 误报 fail closed；`run-2026-08-11-16-06-07` 随后因同一占位符触发 Copy 标题拼接而由 strict verifier fail closed。两个问题均已按保存证据修复，cache template 未写回。
5. 六 case/cleanup 证据：`run-2026-08-11-16-11-01` 的六 case 全部 fresh、`verified=true/lossless=true`，source/anchors 不变并清理全部目标；保存的 restored evidence 通过最终收窄门。旧通用 restore 只因无关 Description Page 重序列化停止，未产生 lifecycle close。
6. 已完成：`run-2026-08-11-16-18-20` 取得全部六 case、9 个 fresh target、默认 cleanup/restore、双 Notebook lifecycle close、cache template unchanged 与顶层 `passed` 的单 run 完整证据。
7. 已完成：`run-2026-08-11-15-43-26` 证明 Move fresh target、anchor 不变和严格 source deletion gate。
8. 只有触发“容器 Copy”决策门时，再依次运行增强后的三个原容器 scenario；当前自动化未触发。
9. 每个失败立即停止并保留现场；Agent 只检查用户产生的 evidence，不执行真实命令。

## 非目标

- 不禁止 OneNote 中合法的重名 Page，也不通过自动后缀规避定位问题。
- 不把 Page path 或 title 改造成伪唯一键，不让 comparator 接受 source→source identity mapping。
- 不把 destination Page ID 添加到 `copy_page`；Page Copy 的直接目标仍是 Section，子树父子关系由 order/level 重建。
- 不借此合并 Reparent、Copy、Move 或 Reorder 的 placement 参数与权限。
- 不直接编辑 `.one` 文件，不解析 cache template 二进制，不删除失败 working Notebook 或用户 Notebook。
- 不以 mock、dry-run 或旧的 root-only 成功证明真实 subtree 修复已完成。

## 风险与缓解

### P0：错误对象被写入或删除

所有 create read-back 在正文写入前完成 source/target disjoint、type 和 parent 校验；Move 删除门要求完整 fresh mapping。任一证据不全即 partial fail，并报告人工恢复。

### P1：partial evidence 错报 source 未触及

逐阶段维护 `source_touched/topology_touched`，只在执行记录证明零源写入时返回 `source_untouched=true`。历史失败保留原始响应，同时在 TODO/诊断中明确其不可信字段。

### P1：Advanced path retry 扩大删除范围

从 production registration 移除或改成 exact-ID 单对象语义；不能用更多 path retry 掩盖 ID 不确定性。

### P1：重名 fixture 与 cache ID remap 冲突

优先在目标 Section 预置跨 Section 同名 anchor，避免模板内出现重复 typed path；必须在模板内重名时先定义可验证的唯一 recipe-owned身份，不允许按 occurrence 猜测。

### P2：只修 Page Copy，容器分支回归遗漏

自动化对四类 Copy 全覆盖；容器真实场景采用明确决策门，发现分支差异后直接增强原 Scenario，而不是新增旁路验证。

## 依赖与关联

- [TODO 002](002_p2_copy_and_reconstructive_page_move.md)：四层 Copy 与严格 Page Move 的基础合同；本 TODO 不撤销其历史完成状态，但补充后续发现的安全回归。
- [TODO 005](005_page_copy_without_indentation_subtree.md)：root-only/subtree scope 合同；本 TODO 专注 fresh identity 与同名落点。
- [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md)：证明 typed Reparent 已按精确 ID 收敛，也是 advanced generic mutation 进一步缩面的先例。
- [TODO 013](013_reparent_default_placement_contract.md)：定义 Reparent 默认位置；与本 TODO 的 mutation identity 分离，不能用 placement 验证替代 ID 验证。
- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：多 Notebook cache、Recipe version、live ID rebind 与失败 working lease；fixture 增强必须保持其安全边界。
- [`OneNote mutation created-target identity Lesson`](../lesson/onenote_mutation_created_target_identity.md)：记录真实失败/修复对照、friendly path 的证据边界与可复用 fail-closed 设计影响。

## 完成定义

- `wait_for_created` 及所有 mutation 调用者遵循 exact allocated ID first、unique path fallback only、ambiguity fail closed，并具有直接自动化覆盖。
- public `create_page` 在同 Section 连续创建同标题 Page 时返回两个精确、互异、正确归属的 IDs；失败保留真实 allocated ID。
- 四类 Copy 的自动化矩阵证明所有 target IDs fresh/互异、父级正确、源和 anchors 不变；错误 read-back 在正文写入和重排前停止。
- Move 自动化证明任何 alias/歧义都会阻止源删除；成功仍要求完整 fidelity/topology/source-current gates。
- typed Reparent、Reorder、Delete 与内容 mutation 的负向合同证明不存在名称/path mutation target fallback。
- Advanced `delete_hierarchy/open_hierarchy/merge_sections/set_filing_location` 完成逐项决策与实现；默认 profile 不扩大，任何保留 mutation 都 fail closed。
- Copy partial-failure evidence 准确记录 allocated/resolved/source-touch/topology-touch 状态，不再无条件声称 source untouched。
- 原 `create`、`copy-page`、`move-page` Recipe/Scenario 已按本文件增强，未新增旁路 scenario；fixture version、dry-run、权限、失败保留与 cache immutability 合同通过。
- 容器 Copy 的自动化门通过；如触发真实差异，三个原容器 Scenario 按决策门增强并由用户逐一复验。
- manual-validation 纯测试、完整 pytest、所有相关 `--dry-run --json` 与 `git diff --check` 通过；Agent 未执行真实 scenario。
- 用户运行并确认增强后的 `create`、六 case `copy-page` 和 `move-page` 真实成功，证据包含 fresh ID disjoint、正确 Section/order/level、anchors/source 不变和严格删除门。
- README、tool contracts、object model、manual-validation README、相关 Lesson/TODO 和本索引与最终行为及真实证据一致。
