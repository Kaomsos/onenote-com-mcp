# 039：真实 Page Content 的 Interactive Move Lossless 校验

> ID：039
> 状态：已完成
> 优先级：P0
> 类型：Bug / Page Move / Lossless Validation / Interactive Manual Validation
> 更新日期：2026-08-18

## 最终状态

2026-08-18，用户明确决定关闭 039，并将尚未闭环的 Page Copy/Move 回读校验工作统一转交 [TODO 040](040_move_readback_validation_followups.md)。本 TODO 已完成专用 representative-content bootstrap/interactive Move 脚手架、immutable template identity 稳定化、真实 `copy_only` 复现以及 source→transformed→target content-free 诊断基础；这些实现和历史证据继续有效。

关闭 039 不表示真实 Page Move 已经达到最终 `verified/lossless/copy_contract_satisfied=true`，也不表示目标标题、纯 RichText verification tier、Table 列宽或 typed equivalence failure 已解决。上述剩余 P0 范围及最终 Copy-before-delete 用户验收全部由 TODO 040 接管。本文自此作为只读历史台账关闭，不再与 040 并行维护同一问题。

> 兼容说明（2026-08-18）：本文后续出现的独立 bootstrap/consumer 命令属于 TODO 041 之前的历史设计与真实证据。当前公开入口只有 `interactive-move-page-content`：fresh 路径内含 bootstrap 阶段，`--use-cache` 路径跳过 bootstrap。

[TODO 037 / UT-009](037_user_testing_experience_feedback_and_optimization.md) 已修复三类已知 OneNote COM 规范化造成的 `verify_copy` 误报，并在受控 disposable fixture 上证明 `semantic_content_v1`、其他既有 verification tier 和 Copy-before-delete 安全链能够通过。但用户继续用 `move_page` 处理代表性真实 Page content 时，目标副本仍会被回读判定为非 `lossless`，因此结果停在 `partial_failure` / `copy_only`，源 Page 按设计不删除。

这是未解决的 P0 用户体感 bug，范围同时覆盖正文/富文本 fidelity 和默认标题 fidelity。当前证据只说明 lossless gate 阻塞了 Move，尚不能证明具体失败来自 verification tier 选择、能力投影不完整、已知但未建模的 COM 规范化、正文/表格/对象/二进制差异，还是 Copy 转换确实丢失了内容。不得把它描述为耗时或 timeout 问题，也不得通过提高 timeout、跳过回读、把 `copy_only` 当成功或先删源来掩盖。

本 TODO 接管 UT-009 尚未覆盖的“代表性真实 Page content”范围；037 按用户明确决定关闭。UT-009 已取得的受控 fixture 证据继续有效，但不再被表述为真实内容 Move 的完整闭环。

## 当前实施进度

2026-08-18 已完成第一阶段 scaffold：

- 新增 `interactive-move-page-content` 显式注册、`included_in_all=false` 的统一场景（fresh 路径含 bootstrap 阶段；`--use-cache` 跳过 bootstrap）；
- 新增共享同一 cache fingerprint 的双 Notebook `MovePageContentRecipe`，source 只允许一个 exact root leaf Canvas，destination 具有独立 Section/anchor；
- shared interactive bootstrap 已从单 source 发布路径泛化为完整 role bundle：逐 role 重读 authored snapshot、精确关闭全部 role、opaque 发布 immutable template、materialize 第二份双 role working bundle并 live validate；
- fresh bootstrap 阶段要求至少一种受支持的非平凡 Page capability；unknown/incomplete projection 只能冻结为 `evidence_only`，不能进入 Move；
- scenario 阶段消费 fresh 已发布的 instance，或在 `--use-cache` 时接受显式/唯一 ready `authored-<24 hex>` instance；使用 Create + Writes + Deletes 最小策略，固定一次 `move_page(include_subpages=false)`；lossless failure 保存 source/target after snapshot 与逐 tier/check 的 content-free `lossless-diagnostic.json` 后原样失败，绝不补调 mutation；
- success 路径只在生产 `verified/lossless/copy_contract_satisfied=true` 且 `source_deleted_nonpermanently=true` 后请求 run-bound UI ACCEPT；
- 新增纯合同覆盖 recipe identity、ready/evidence-only、双 role bootstrap、单次 Move、lossless 失败 envelope 解包、失败保源、诊断脱敏和成功后人工门；当前 manual-validation 纯测试为 `627 passed`，全量 pytest 为 `1361 passed`，bootstrap 的 `--dry-run --json` 通过，`all` 仍只包含原 18 个稳定场景。ready template 发布成功的普通终端输出会给出显式实例 ID 和可复制的下一条 Move 命令，不据此自动选择实例。

两次用户前台 bootstrap 已确认 source/destination 创建、代表性内容 projection、模板关闭/发布与双 role working copy 打开均可工作；尚未执行 Move。第一次 materialize 的 source hierarchy 重绑确定性失败：用户改变了 Canvas title，而 v1 template 仍冻结脚手架初始 relative address，连续 16 次都缺少该 Canvas。实现已在发布前冻结 authored Canvas 的 live title/path 并升至 v2。第二次重绑已完整通过，但在 materialized working bundle 的 full frozen identity 比对前失败：v2 把复制后必然变化的 Notebook/Page/Object ID、path 与 identity-sensitive page digest 放进模板实例摘要，故使同一 opaque copy 被误判不匹配。实际证据显示代表页稳定 `page_body_hash` 一致，只有 identity-sensitive hash 改变。实现现改用冻结标题/层级语义、稳定正文摘要和类型化 capability projection，并升至 v3；该失败不是 timeout 或 lossless gate，未调用 Move，working bundle 已精确关闭、template integrity 未受牵连。需由用户以 v3 重新 bootstrap。

后续连续两次用户前台 v3 bootstrap 又在同一个 full frozen identity gate 失败。两次 template inventory 与 materialize 前 working inventory 都逐字节一致，层级重绑、标题、能力集合、projection completeness 和模板 immutability 均通过，失败前仍未调用 Move；差异集中在关闭并从物理副本重开后的 `page_body_hash`，其中一次连对象种类序列也完全一致，证明该摘要仍吸收了 OneNote 持久化时的布局/序列化稳定化，而不是模板 copy 损坏。实现现为 RichText/List/Table/Image 使用与生产 `semantic_content_v1` 回读相同的 content-free 语义投影摘要，忽略空布局 Outline 与布局元数据但继续保留标题、正文、有效富文本、List/Tag、表格、图片 binary 和对象计数；投影不完整的其他能力仍严格 fallback 到稳定正文摘要。Recipe 已升至 v4，旧 v3 template 仅因 fingerprint 不再命中且不会被自动删除；自动化不能代替下一次用户前台 bootstrap 复验。

随后一个用户前台 v4 bootstrap 已完整通过，证明上述语义摘要能够覆盖代表性 Table/Image 内容。再后一个不同的 List/RichText fixture 仍在 full frozen identity gate 失败：发布前后语义摘要、标题、能力集合和 markup 数量完全一致，但 OneNote 重开时收敛掉一个空 Outline/OE，v4 projection 中冗余的原始 `object_kinds` 精确 multiset 因而否决了同一语义内容。实现现让完整 `semantic_content_v1` 同时拥有内容与对象身份判定，不再叠加原始对象种类序列；投影不完整的其他能力仍保留严格正文摘要与对象种类 multiset fallback。Recipe 已升至 v5，旧 v4 template 只会因新 fingerprint 被排除、不会自动删除；该 run 未调用 Move，template inventory unchanged，working bundle 已精确关闭。

随后 v5 已有一个用户前台 bootstrap 完整通过。另一个 Image/Tag/RichText fixture 仍在重开后的 identity gate 失败：标题、能力集合、两张 Image、Tag、markup 与非 Outline 对象序列均一致，但 OneNote 把“主 Outline + 仅含一个 OE 的第二 Outline”合并成单一 Outline，导致完整 Copy semantic projection 的 Outline 分组 digest 变化。该变化属于 template 持久化 identity 的容器归并；它不能用于放宽生产 Copy equivalence。实现现新增 persistence digest，仅对 bootstrap identity 扁平化 Outline 容器边界，同时严格保留 OE 顺序、正文、有效样式、List/Tag、Table、Image binary 与其他语义。Recipe 已升至 v6，旧 v5 template 仅被新 fingerprint 排除且不会自动删除；该 run 未调用 Move、template inventory unchanged、working bundle 已精确关闭。

随后两次新的用户前台 bootstrap 在发布前内容资格门失败。去敏证据显示 source 都是 exact leaf Page，typed projection 完整、对象 schema 有效且无 unknown capability；页面分别含多个 OE，但能力集合只有 `Outline + RichText`。失败来自 v6 人为排除 `RichText`、强制要求 Table/List/Image 等额外结构化能力，并非 hierarchy、cache identity 或 persistence convergence。Recipe v7 现把机器投影明确检测到的有效 `RichText` 纳入代表性能力，同时继续拒绝只有 `Outline` 的未编辑纯占位形状；unknown/incomplete projection 仍为 `evidence_only`，不得获得 Move 删源资格。旧 v6 template 仅由新 fingerprint 排除，不会自动删除。

2026-08-18 的首次有效前台 `interactive-move-page-content` 已复现 `copy_only`：source 与 target 均保留、source 未删除，`semantic_content_v1` 的 title、binary objects、对象计数和完整 capability projection 均通过，唯一 false 是 `rich_list_tag_table_outline`。源/目标均为 `Outline + RichText + Table`，目标的嵌入样式 `span` 数从 154 降到 151；可见文本不变但稳定正文摘要不同。其 6 次回读未收敛是 semantic equivalence 持续为 false 的结果，不是独立 timeout 根因。该次 run 所用版本尚未保存 source→transformed 的投影，故当时不能严格判断这 3 个样式 span 是写前转换还是 OneNote COM 写后规范化所致。

同一 run 还暴露了肉眼可见的标题错位：目标标题被显示为 `01-Representative-Moved-<run timestamp>`。这不是 OneNote 的非确定性改写；该次 run 所用场景主动传入了 `destination_title`，所以 `title=true` 仅比较 transformed payload 与 target，而没有验证 source 标题是否被默认保留。标题 fidelity 与富文本投影差异作为同一 P0 的 Copy-before-delete gate 收拢处理。

2026-08-18 已实施针对上述两个问题的代码修复，等待同一 ready template 的用户前台复测：专用场景不再传 `destination_title`，因此生产默认路径会以 source title 创建并验证目标；`semantic_content_v1` 的 inline projection 改为比较每段文本的有效格式，允许 OneNote 折叠不改变有效 CSS/inline 语义的冗余嵌套 `span`，但颜色/style、链接、正文、表格和 binary 的真实变化仍由负向合同拒绝。生产 `page_results[*].semantic_content_stages` 同时新增 source→transformed 与 transformed→target 的 content-free 摘要和有界 mismatch path，失败不再只能看到一个总布尔值。自动化已覆盖冗余 span 合并正向、style 丢失负向、诊断脱敏/有界性、生产报告接线和场景不传 rename 参数；这只能证明实现合同，尚不能代替最终真实 OneNote Move 验收。

## 目标

1. 锁定生产 Move 的关键校验链，完整区分 `lossless_candidate`、逐 Page equivalence、topology verification、blocking Copy issue、`copy_contract_satisfied` 与源重校验，准确指出是哪一项阻止删源。
2. 新增一对不进入 `all` 的 human-gated 场景：专用 bootstrap 冻结用户制作的代表性真实 Page content，专用 interactive Move 在 fresh working bundle 中调用一次公开 `move_page`。
3. 取得可复现、content-free 的 source/transformed/target 投影和 mismatch 证据；若属于安全的 OneNote COM 规范化，实施最窄的语义 comparator；若存在真实内容丢失，则保持 fail closed 并修复 Copy 转换或明确 unsupported 能力。
4. 保持 Move 的 verified-Copy-before-delete 门限：只有完整 Copy 合同和 scoped identity/topology 均通过时，才允许对 disposable 源 Page 执行非永久删除。

## 锁定的生产校验链

Page Move 继续固定为以下顺序，任何修复不得交换或省略安全阶段：

```text
exact-ID confirmation + bounded source plan
→ create fresh destination Page identity with the source title unless the caller explicitly requests a rename
→ transform and write Page content
→ read back the exact destination Page
→ select a statically reviewed verification tier
→ prove Page equivalence + scoped topology + complete Copy contract
→ revalidate the unchanged source scope
→ protect excluded subpages when include_subpages=false
→ non-permanently delete the disposable source Page(s)
→ final source/destination topology reconciliation
```

- `page_results[*].equivalence` 必须保留实际 `verification_tier`、`acceptance_checks` 和每项 content-free 布尔结果；不得把总 `lossless=false` 压缩成无法诊断的单一错误。默认 Copy/Move 还必须证明 source title、transformed title 与 target title 一致；只有调用方明确传入 rename 参数时才允许目标标题不同。
- 必须区分“投影不完整后回退 strict”“投影完整但语义不等价”“存在 `content_type_unverified` / omitted content”“拓扑不通过”四类失败；未知能力继续 fail closed。
- comparator 只允许忽略有真实证据、可精确定义且有正负自动化合同的 COM 规范化。标题、可见文本、富文本样式/链接、List/Tag、表格行列/单元格语义、非空 Outline、对象类型/数量和可读取 binary 的变化不得被宽泛归一化。
- `copy_contract_satisfied` 不是人工 ACCEPT 可以覆盖的字段。interactive 场景只能提供真实表现与机器 mismatch 证据，不能在运行时修改生产 allowlist、verification tier 或删源资格。
- `copy_only`、`copy_unverified`、partial 或 indeterminate 结果不得自动重试、replay、rollback 或删除源；target 和完整失败现场必须留给用户审阅。

## 历史专用 Bootstrap（已由统一入口取代）

历史实现曾以 `bootstrap-move-page-content-fixture` 为独立入口；TODO 041 后由 `interactive-move-page-content` fresh 路径内部的 bootstrap 阶段承担同一职责：

- 使用 fresh、disposable、双 Notebook role bundle，预先创建 exact source Canvas、destination Section、reserved marker 和 bounded authoring zone；不得接受用户业务 Notebook、外部 Notebook/Page ID 或任意本地 `.one` 路径。
- 用户只在 exact Canvas 中制作或粘贴一页经过筛选的、非敏感的代表性真实内容。它可以组合日常 Page 中实际出现的富文本、链接、List/Tag、表格、图片、附件、公式或其他已公开能力，而不是只依赖现有最小 synthetic fixture。
- bootstrap 在用户 run-bound 确认后至少连续读取两次 exact source Page，冻结 capability projection、对象计数、verification tier 候选、稳定的正文 digest、标题/层级语义和 immutable template inventory；模板实例摘要不得纳入复制后必然变化的 Notebook/Page/Object COM ID、working path 或 identity-sensitive page digest。证据不得保存正文、标题、原始 XML、binary、用户路径或真实 COM ID 到版本库。
- 出现未知节点、投影不完整、越界编辑、reserved marker 变化、额外 Page、身份歧义或不稳定 source 时，模板只能标记为 `evidence_only` 或拒绝发布，不能获得 Move deletion eligibility。
- ready template 必须关闭后才允许 opaque byte-for-byte cache publication；随后同一 fresh run 或后续 `--use-cache` 路径只 materialize 物理独立 working copy并重新绑定 live ID，绝不打开或修改 cache master。
- bootstrap 真实命令只能由用户在交互式前台执行。Agent、pytest、CI、hook、timer、watcher 和后台任务只能运行 `--dry-run`。

该专用 recipe 可以复用 `InteractiveFixtureRecipe`、checkpoint、freeze、cache 和 explicit instance selection 基础设施，但不依赖完成 P3 [TODO 020](020_user_authored_fixture_development_scaffold.md) 的整个自由创作矩阵，也不得借 `UserAuthoredRecipe.ready` 自动授予 Move 权限。

## 专用 Interactive Move

新增具名命令，名称暂定为 `interactive-move-page-content`：

- `included_in_all = False`；`--use-cache` 的 miss/invalid 返回 `interactive_cache_miss` 并提示移除该选项，不自动进入 authoring，也不猜测最近的 template instance。
- Fresh 自动消费刚发布的 ready instance；cache 路径消费显式或唯一 ready、mutation-eligible 的 `template_instance_id`，materialize fresh source/destination working bundle，启动一个 scenario-scoped MCP，使用 Create + Writes + Deletes 的最小静态 policy；不启用 Permanent Delete 或 Raw XML。
- 对 exact source Page 调用一次公开 `move_page`，且不传 `destination_title`，以验证默认标题与代表性真实内容一并保真。首个验收 case 固定 `include_subpages=false` 且 source 为叶子 Page，从而把本 TODO 的核心范围锁定在 Page content/title lossless gate；子页范围行为继续由 UT-010 的既有 `move-page` 场景覆盖，不用复杂拓扑掩盖 comparator 问题。
- mutation 前保存 content-free source contract；响应中保存完整 Copy report、逐 Page tier/check、issue code、target identity 和 source deletion gate。场景可在失败后对仍存在的 source/target 做有界只读诊断，但不得再次调用 mutation。
- `lossless=false` 或 `copy_contract_satisfied=false` 时必须断言 source 仍 active、target 明确标为未验证、场景非零退出并默认保留 working evidence；用户可用 `--keep-worksite` 保持 OneNote 现场打开。
- 只有机器校验完整通过后，才允许 Move 内部非永久删源；随后必须证明 source inactive、destination target 唯一、内容合同仍成立，并请求用户对目标 Page 的可见/可交互表现提交 run-bound ACCEPT。
- 人工 verdict 只补充 GUI 可见证据，不能把机器失败改写为 passed，也不能触发补删源。

## 诊断与修复要求

首次真实失败证据必须至少回答：

1. 生产选择了哪个 verification tier，source/transformed/target 各自的 capability projection 与标题语义是否完整且一致；
2. 哪些 acceptance check 为 false，是否为 strict fallback，以及 failure 是否来自 blocking issue 而非 equivalence；
3. mismatch 首次出现在哪个受控语义路径或对象类别；仅记录 path/kind/count/hash/布尔值，不记录正文或 raw XML；
4. source → transformed 与 transformed → target 两段中哪一段产生差异；
5. 同一 frozen fixture 的 Copy-only 诊断是否能稳定复现，排除目标定位、scope 或源删除阶段干扰。

修复必须基于上述证据选择最小路径：

- 安全 COM 规范化：增加窄语义 projection/comparator，并固定观察到的正向样本以及真实内容变化负向样本；
- 转换丢失或错误改写：修复 `transform_page_for_copy`，不能靠 comparator 忽略；
- 已知内容类型缺少完整验证：先补 typed detector、机器 invariant 和真实 Copy 证据，再静态更新 allowlist；
- 当前 COM 无法可靠复制或回读：保持 unsupported / `copy_only`，返回可行动的具体限制，不伪称 Move 支持。

## 自动化合同

- Registry、dry-run catalog、policy 和 help 固定 unified fresh/cache 入口、`included_in_all=false`、显式或唯一 ready instance、双 role、最小 gate 与 human-only 边界；
- bootstrap 覆盖 confirmation、EOF/timeout/cancel、authoring-zone 越界、不稳定 source、unknown/incomplete projection、ready/evidence-only、发布前 close 和 immutable cache；
- cache 路径覆盖 miss/invalid/歧义/错误 instance；scenario 覆盖一次 Move 调用、失败后零第二次 mutation、source untouched、target/evidence 保留、成功时 verified Copy 后才非永久删源；
- comparator 使用保存的最小去敏 fixture 或构造样本覆盖观察到的规范化正向分支，以及文本、style/link、List/Tag、表格、Outline、对象和 binary 丢失负向分支；
- 响应和 manual evidence 冻结 `verification_tier`、`acceptance_checks`、checks、projection completeness、issue codes、`lossless`、`verified`、`copy_contract_satisfied` 与 deletion decision，不泄露 Page content；
- 聚焦纯测试通过后运行完整 `.venv\Scripts\python.exe -m pytest -q`，并运行两个新命令及受影响 Copy/Move 命令的 `--dry-run --json`。自动化不得启动 OneNote 或执行真实 mutation。

## 真实验收

真实验收只能由用户本人前台完成：

1. 运行 `interactive-move-page-content` fresh 路径，在 exact disposable Canvas 中建立代表性真实内容、发布 ready instance，并由同一 run 执行一次公开 Move；
2. 再运行 `interactive-move-page-content --use-cache --template-instance-id ... --keep-worksite`（只有一个 ready instance 时可省略 ID），确认 cache 路径跳过 bootstrap 且仍只调用一次公开 Move；
3. 若首次结果为 `copy_only`，保留 source、target 和 content-free mismatch evidence，完成根因修复后从同一 immutable template materialize 新 working bundle复测；
4. 最终 run 必须报告 `verified=true`、`lossless=true`、`copy_contract_satisfied=true`，随后才有 `source_deleted_nonpermanently=true`；用户检查目标 Page 后提交 run-bound ACCEPT；
5. 记录 Office/OneNote 版本、代码 commit、template fingerprint/instance、场景状态和 lifecycle，结论只覆盖该代表性 fixture 与环境，不外推为所有未知 Page 能力。

## 非目标

- 不直接移动、缓存、接管或删除用户现有业务 Notebook/Page；
- 不通过提高 timeout 或 CopyBudget、减少稳定观察、跳过 binary/object 校验、放宽所有 Table/Image 或全局忽略 XML 差异来取得通过；
- 不建立第二套 Move-only 内容 allowlist；Move 必须消费生产 Copy 的共享静态合同；
- 不新增公开 raw XML、任意路径、Graph、Azure、OAuth、遥测、远程内容处理或直接 `.one` 编辑能力；
- 不把 human ACCEPT 当作 lossless 证明，也不在失败后自动清理用于诊断的 target。

## 关闭定义

- [x] 专用 bootstrap/interactive Move、双 role immutable template 和 content-free failure evidence 已实现并取得用户前台真实复现；
- [x] 已证明阻塞来自 lossless/equivalence，而非独立 timeout，并保持 `copy_only`、source untouched 和 verified-Copy-before-delete 门限；
- [x] 已将标题/path、纯 RichText verification tier、Table 列宽、typed equivalence failure 和最终真实 Move 验收完整转交 TODO 040，未伪称产品问题已经解决；
- [x] 聚焦测试、完整 pytest、相关 dry-run、README/design/manual-validation 文档和 TODO 037 转交关系已同步；
- [x] 用户于 2026-08-18 明确要求关闭 039、转结给 040。

## 关联

- [TODO 037 / UT-009](037_user_testing_experience_feedback_and_optimization.md)：已完成的受控 fixture comparator 修复，以及本 TODO 接管的真实内容缺口。
- [TODO 004](004_interactive_copy_move_content_fidelity_validation.md)：逐内容类型 bootstrap/interactive Copy、静态 allowlist 与 human-gated 证据边界。
- [TODO 020](020_user_authored_fixture_development_scaffold.md)：可复用但不构成本 P0 前置依赖的自由创作 fixture 基础。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：一次公开调用、内部 planning 与 Copy-before-delete 产品边界。
- [TODO 040](040_move_readback_validation_followups.md)：进行中，统一接管 Page Copy 标题/path、纯 RichText verification tier、富文本/Table 回读差异、typed equivalence failure 和最终 Move 闭环。
- [公开 Tool 契约](../design/tool_contracts.md)
- [Manual Validation Runner](../../tests/manual_validation/README.md)
