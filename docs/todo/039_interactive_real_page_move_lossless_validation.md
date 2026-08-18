# 039：真实 Page Content 的 Interactive Move Lossless 校验

> ID：039
> 状态：待办
> 优先级：P0
> 类型：Bug / Page Move / Lossless Validation / Interactive Manual Validation
> 更新日期：2026-08-18

## 当前结论

[TODO 037 / UT-009](037_user_testing_experience_feedback_and_optimization.md) 已修复三类已知 OneNote COM 规范化造成的 `verify_copy` 误报，并在受控 disposable fixture 上证明 `semantic_content_v1`、其他既有 verification tier 和 Copy-before-delete 安全链能够通过。但用户继续用 `move_page` 处理代表性真实 Page content 时，目标副本仍会被回读判定为非 `lossless`，因此结果停在 `partial_failure` / `copy_only`，源 Page 按设计不删除。

这是未解决的 P0 用户体感 bug。当前证据只说明 lossless gate 阻塞了 Move，尚不能证明具体失败来自 verification tier 选择、能力投影不完整、已知但未建模的 COM 规范化、正文/表格/对象/二进制差异，还是 Copy 转换确实丢失了内容。不得把它描述为耗时或 timeout 问题，也不得通过提高 timeout、跳过回读、把 `copy_only` 当成功或先删源来掩盖。

本 TODO 接管 UT-009 尚未覆盖的“代表性真实 Page content”范围；037 按用户明确决定关闭。UT-009 已取得的受控 fixture 证据继续有效，但不再被表述为真实内容 Move 的完整闭环。

## 目标

1. 锁定生产 Move 的关键校验链，完整区分 `lossless_candidate`、逐 Page equivalence、topology verification、blocking Copy issue、`copy_contract_satisfied` 与源重校验，准确指出是哪一项阻止删源。
2. 新增一对不进入 `all` 的 human-gated 场景：专用 bootstrap 冻结用户制作的代表性真实 Page content，专用 interactive Move 在 fresh working bundle 中调用一次公开 `move_page`。
3. 取得可复现、content-free 的 source/transformed/target 投影和 mismatch 证据；若属于安全的 OneNote COM 规范化，实施最窄的语义 comparator；若存在真实内容丢失，则保持 fail closed 并修复 Copy 转换或明确 unsupported 能力。
4. 保持 Move 的 verified-Copy-before-delete 门限：只有完整 Copy 合同和 scoped identity/topology 均通过时，才允许对 disposable 源 Page 执行非永久删除。

## 锁定的生产校验链

Page Move 继续固定为以下顺序，任何修复不得交换或省略安全阶段：

```text
exact-ID confirmation + bounded source plan
→ create fresh destination Page identity
→ transform and write Page content
→ read back the exact destination Page
→ select a statically reviewed verification tier
→ prove Page equivalence + scoped topology + complete Copy contract
→ revalidate the unchanged source scope
→ protect excluded subpages when include_subpages=false
→ non-permanently delete the disposable source Page(s)
→ final source/destination topology reconciliation
```

- `page_results[*].equivalence` 必须保留实际 `verification_tier`、`acceptance_checks` 和每项 content-free 布尔结果；不得把总 `lossless=false` 压缩成无法诊断的单一错误。
- 必须区分“投影不完整后回退 strict”“投影完整但语义不等价”“存在 `content_type_unverified` / omitted content”“拓扑不通过”四类失败；未知能力继续 fail closed。
- comparator 只允许忽略有真实证据、可精确定义且有正负自动化合同的 COM 规范化。标题、可见文本、富文本样式/链接、List/Tag、表格行列/单元格语义、非空 Outline、对象类型/数量和可读取 binary 的变化不得被宽泛归一化。
- `copy_contract_satisfied` 不是人工 ACCEPT 可以覆盖的字段。interactive 场景只能提供真实表现与机器 mismatch 证据，不能在运行时修改生产 allowlist、verification tier 或删源资格。
- `copy_only`、`copy_unverified`、partial 或 indeterminate 结果不得自动重试、replay、rollback 或删除源；target 和完整失败现场必须留给用户审阅。

## 专用 Bootstrap

新增具名命令，名称暂定为 `bootstrap-move-page-content-fixture`：

- 使用 fresh、disposable、双 Notebook role bundle，预先创建 exact source Canvas、destination Section、reserved marker 和 bounded authoring zone；不得接受用户业务 Notebook、外部 Notebook/Page ID 或任意本地 `.one` 路径。
- 用户只在 exact Canvas 中制作或粘贴一页经过筛选的、非敏感的代表性真实内容。它可以组合日常 Page 中实际出现的富文本、链接、List/Tag、表格、图片、附件、公式或其他已公开能力，而不是只依赖现有最小 synthetic fixture。
- bootstrap 在用户 run-bound 确认后至少连续读取两次 exact source Page，冻结 capability projection、对象计数、verification tier 候选、稳定语义 digest、binary digest、层级身份和 immutable template inventory；证据不得保存正文、标题、原始 XML、binary、用户路径或真实 COM ID 到版本库。
- 出现未知节点、投影不完整、越界编辑、reserved marker 变化、额外 Page、身份歧义或不稳定 source 时，模板只能标记为 `evidence_only` 或拒绝发布，不能获得 Move deletion eligibility。
- ready template 必须关闭后才允许 opaque byte-for-byte cache publication；后续 consumer 只 materialize 物理独立 working copy并重新绑定 live ID，绝不打开或修改 cache master。
- bootstrap 真实命令只能由用户在交互式前台执行。Agent、pytest、CI、hook、timer、watcher 和后台任务只能运行 `--dry-run`。

该专用 recipe 可以复用 `InteractiveFixtureRecipe`、checkpoint、freeze、cache 和 explicit instance selection 基础设施，但不依赖完成 P3 [TODO 020](020_user_authored_fixture_development_scaffold.md) 的整个自由创作矩阵，也不得借 `UserAuthoredRecipe.ready` 自动授予 Move 权限。

## 专用 Interactive Move

新增具名命令，名称暂定为 `interactive-move-page-content`：

- `included_in_all = False`；cache miss/invalid 只返回对应 bootstrap handoff，不自动进入 authoring，也不猜测最近的 template instance。
- 只消费显式 ready 的 `template_instance_id`，materialize fresh source/destination working bundle，启动一个 scenario-scoped MCP，使用 Create + Writes + Deletes 的最小静态 policy；不启用 Permanent Delete 或 Raw XML。
- 对 exact source Page 调用一次公开 `move_page`。首个验收 case 固定 `include_subpages=false` 且 source 为叶子 Page，从而把本 TODO 的核心范围锁定在 Page content lossless gate；子页范围行为继续由 UT-010 的既有 `move-page` 场景覆盖，不用复杂拓扑掩盖 comparator 问题。
- mutation 前保存 content-free source contract；响应中保存完整 Copy report、逐 Page tier/check、issue code、target identity 和 source deletion gate。场景可在失败后对仍存在的 source/target 做有界只读诊断，但不得再次调用 mutation。
- `lossless=false` 或 `copy_contract_satisfied=false` 时必须断言 source 仍 active、target 明确标为未验证、场景非零退出并默认保留 working evidence；用户可用 `--keep-worksite` 保持 OneNote 现场打开。
- 只有机器校验完整通过后，才允许 Move 内部非永久删源；随后必须证明 source inactive、destination target 唯一、内容合同仍成立，并请求用户对目标 Page 的可见/可交互表现提交 run-bound ACCEPT。
- 人工 verdict 只补充 GUI 可见证据，不能把机器失败改写为 passed，也不能触发补删源。

## 诊断与修复要求

首次真实失败证据必须至少回答：

1. 生产选择了哪个 verification tier，source/transformed/target 各自的 capability projection 是否完整；
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

- Registry、dry-run catalog、policy 和 help 固定 bootstrap/consumer 配对、`included_in_all=false`、explicit instance、双 role、最小 gate 与 human-only 边界；
- bootstrap 覆盖 confirmation、EOF/timeout/cancel、authoring-zone 越界、不稳定 source、unknown/incomplete projection、ready/evidence-only、发布前 close 和 immutable cache；
- consumer 覆盖 cache miss/invalid、错误 instance、一次 Move 调用、失败后零第二次 mutation、source untouched、target/evidence 保留、成功时 verified Copy 后才非永久删源；
- comparator 使用保存的最小去敏 fixture 或构造样本覆盖观察到的规范化正向分支，以及文本、style/link、List/Tag、表格、Outline、对象和 binary 丢失负向分支；
- 响应和 manual evidence 冻结 `verification_tier`、`acceptance_checks`、checks、projection completeness、issue codes、`lossless`、`verified`、`copy_contract_satisfied` 与 deletion decision，不泄露 Page content；
- 聚焦纯测试通过后运行完整 `.venv\Scripts\python.exe -m pytest -q`，并运行两个新命令及受影响 Copy/Move 命令的 `--dry-run --json`。自动化不得启动 OneNote 或执行真实 mutation。

## 真实验收

真实验收只能由用户本人前台完成：

1. 运行专用 bootstrap，在 exact disposable Canvas 中建立代表性真实内容并发布一个 ready instance；
2. 运行 `interactive-move-page-content --use-cache --template-instance-id ... --keep-worksite`，确认其只调用一次公开 Move；
3. 若首次结果为 `copy_only`，保留 source、target 和 content-free mismatch evidence，完成根因修复后从同一 immutable template materialize 新 working bundle复测；
4. 最终 run 必须报告 `verified=true`、`lossless=true`、`copy_contract_satisfied=true`，随后才有 `source_deleted_nonpermanently=true`；用户检查目标 Page 后提交 run-bound ACCEPT；
5. 记录 Office/OneNote 版本、代码 commit、template fingerprint/instance、场景状态和 lifecycle，结论只覆盖该代表性 fixture 与环境，不外推为所有未知 Page 能力。

## 非目标

- 不直接移动、缓存、接管或删除用户现有业务 Notebook/Page；
- 不通过提高 timeout 或 CopyBudget、减少稳定观察、跳过 binary/object 校验、放宽所有 Table/Image 或全局忽略 XML 差异来取得通过；
- 不建立第二套 Move-only 内容 allowlist；Move 必须消费生产 Copy 的共享静态合同；
- 不新增公开 raw XML、任意路径、Graph、Azure、OAuth、遥测、远程内容处理或直接 `.one` 编辑能力；
- 不把 human ACCEPT 当作 lossless 证明，也不在失败后自动清理用于诊断的 target。

## 完成定义

- [ ] 已用专用 bootstrap 冻结至少一个用户确认的代表性真实 Page content ready instance，且 authoring/identity/cache 安全合同全部通过；
- [ ] 专用 interactive Move 能稳定复现原 lossless 阻塞，并生成足以定位 source→transform→target 首个差异的 content-free 证据；
- [ ] 根因已归类并按最窄路径修复，或以明确 unsupported 合同和可行动响应关闭；
- [ ] 正负自动化证明修复只接受已知安全规范化，不掩盖真实正文、结构、对象或 binary 丢失；
- [ ] 最终用户前台 run 在一次公开 Move 中先达到 `verified/lossless/copy_contract_satisfied=true`，再非永久删除 disposable 源，并通过 run-bound UI 验收；
- [ ] 聚焦测试、完整 pytest、相关 dry-run、README/design/manual-validation 文档和 TODO 037 转交关系均已同步；
- [ ] 用户审阅最终证据并明确批准关闭本 P0。

## 关联

- [TODO 037 / UT-009](037_user_testing_experience_feedback_and_optimization.md)：已完成的受控 fixture comparator 修复，以及本 TODO 接管的真实内容缺口。
- [TODO 004](004_interactive_copy_move_content_fidelity_validation.md)：逐内容类型 bootstrap/interactive Copy、静态 allowlist 与 human-gated 证据边界。
- [TODO 020](020_user_authored_fixture_development_scaffold.md)：可复用但不构成本 P0 前置依赖的自由创作 fixture 基础。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：一次公开调用、内部 planning 与 Copy-before-delete 产品边界。
- [公开 Tool 契约](../design/tool_contracts.md)
- [Manual Validation Runner](../../tests/manual_validation/README.md)
