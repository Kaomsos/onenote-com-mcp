# 037：用户测试期工具调用与实现体验优化

> ID：037
> 状态：进行中
> 优先级：P0
> 类型：User Testing / Developer Dogfooding / Tool UX / 反馈驱动优化
> 更新日期：2026-08-18

## 当前状态

[TODO 034](034_pre_user_testing_tool_surface_convergence.md) 已完成用户测试准入。项目现进入开发者模拟真实用户行为的持续使用阶段：开发者通过受支持的 MCP 客户端，从用户任务出发发现、选择并调用工具，再根据实际体验提出和验证优化。UT-004 已交付复用原工具名的 bounded `items` 批处理与唯一新增工具 `sort_children`，production、自动化、manual-validation 静态合同和 canonical 文档已同步。用户首轮 9 个 human-gated run 为 6 个通过、3 个 fail closed；三条失败路径加固后于 2026-08-18 定向复跑，`create`、`rename`、`reparent-section-group` 均 passed、restored 且 lifecycle closed，因此 9 个具名允许路径均有最新真实通过证据。随后更新后的 `create` 与 `reorder-page` mutation 前拒绝探针也由用户前台运行通过；四类 batch partial-failure 由确定性 fault-injection 自动化合同作为最终依据，不在真实 OneNote 中故意制造后端故障。UT-004 因而闭合为 `completed`。UT-006 记录 online-backed Notebook 创建 SectionGroup 时的同步观察。UT-005、UT-007、UT-008、UT-009 已闭合为 `completed`；UT-006 未改变实现。

UT-010 已进入验证：所有公开 Page 范围已统一为 `include_subpages: bool = false`，`delete_page` 与 `reorder_page` 已补齐单页保护和完整缩进子树语义；Page Reparent/Delete batch 已从同一冻结快照计算整批 scope，并在任何主操作前按 Section 一次性提升全部排除后代。生产实现、自动化、具名 manual-validation 场景和 canonical 文档已同步，仍等待用户在 fresh disposable Notebook 中完成人工复测。

本 TODO 是用户测试期的**唯一改进台账**。工具选择、调用链、描述、schema、权限提示、响应、错误恢复或实现行为方面的观察与改进，暂时全部记录在本文，不为单项体验问题另建 TODO、Lesson、Overview、专题设计稿或其他独立跟踪文档。

若改进改变当前公开合同、运行流程或实现，仍必须同步修改对应代码、自动化测试以及既有 canonical README/design/dev 文档；这些必要同步不是新的反馈台账，变更位置应回链到本文对应记录。可复用 Lesson 或独立长期规划是否拆出，只在本 TODO 收尾时统一决定。

## 目标

- 用 Claude Code、Codex、Cursor、Grok Build 等受支持客户端，以用户任务而不是源码模块为起点使用当前 53 个工具；
- 观察 Agent 是否能仅凭工具名称、description、schema、权限错误和响应 envelope 选择正确且最短的调用链；
- 找出造成误选、重复调用、无效探测、权限困惑、恢复困难或结果难以消费的真实摩擦；
- 优先通过更准确的工具 description、schema/错误提示和产品文档解决认知问题；确属行为缺陷、能力缺口或不合理服务端工作时再修改代码；
- 对每项被接受的改进提供与风险相称的自动化证据，必要的真实 OneNote 证据只能由用户本人执行并确认。

## 用户测试方式

1. 开发者选择一个自然用户任务，并记录客户端、代码提交、OneNote 前置状态和显式开启的授权 gate。
2. 从客户端实际暴露的 MCP 工具面开始，不预先依赖 Service、Bridge、COM operation 或内部实现知识替 Agent 选工具。
3. 记录 content-free 的选择与调用轨迹：候选工具、实际工具、调用次数、失败阶段、重试/恢复和最终是否完成任务；不得在本文写入 Page 正文、原始 XML、binary、用户路径或其他敏感内容。
4. 将问题归类为描述/命名、schema、调用链、授权、响应/错误、性能/可靠性或实现缺陷，并判断是产品问题、客户端行为、环境限制还是无需修改。
5. 对接受的问题实施最小充分改进，先运行聚焦纯测试；影响共享行为时运行完整 pytest。真实 OneNote mutation 不得由 Agent、pytest、CI、hook、timer、watcher 或后台任务触发。
6. 在同一条记录中补充变更位置、验证证据、真实复测结论和最终状态，不把观察与修复拆散到多个台账。

用户测试 mutation 应优先使用 disposable 数据、精确 ID、最小静态权限和可恢复操作；不得把唯一副本或正常工作 Notebook 当作试验材料。权限保持默认关闭，只为当前任务显式开启必要 gate。

## 统一问题分类

| 类别 | 含义 | 常见改进方向 |
| --- | --- | --- |
| `discovery` | 工具名称、分类或 description 导致未发现或误选 | description、分类、命名或工具面调整 |
| `schema` | 参数名称、必填关系、scope、预算或示例难以理解 | input schema、description、错误提示 |
| `workflow` | 完成用户任务需要不清晰、重复或过长的调用链 | 工具职责、响应 handoff、调用指导 |
| `authorization` | 默认关闭、组合 gate 或拒绝后的用户行动不清楚 | policy 投影、错误信息、配置说明 |
| `response` | envelope、结果字段、warning 或 partial outcome 难以消费 | response schema、投影、错误模型 |
| `reliability` | timeout、收敛、幂等、性能或环境交互不符合合同 | Runtime、Service、Bridge 或预算实现 |
| `capability` | 合理用户任务在当前 typed 工具面无法完成 | 先确认任务与风险，再评估新增/调整能力 |
| `client` | 问题来自特定 MCP client 的加载、审批或呈现行为 | 配置/说明或上游限制，不伪装成服务端缺陷 |

## 用户测试任务与改进台账

每项使用不可变编号 `UT-001`、`UT-002`……。一条记录从观察持续更新到关闭，不复用编号。

| ID | 日期 | 客户端 / 用户任务 | Gate | 观察与分类 | 决策 / 改进 | 验证证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UT-001 | 2026-08-16 | Grok Build / 读取 Page | 7 类 gate 关闭 | 未检查 health 或给出 GUI 恢复路径；`workflow` / `authorization` / `client` | 已优化 Session description、typed 恢复提示与 Grok 最小 UI Control 测试授权 | 用户已完成原始 Page read 与 proactive session-health 前台验收并确认符合预期 | completed |
| UT-002 | 2026-08-16 | Grok Build / 创建 disposable Notebook | Writes + UI Control | 首次授权 effect 在 GUI absent 时被拦截，随后完成显式恢复并成功重试；`reliability` / `authorization` / `workflow` | 已交付独立 Registry platform-preflight policy 和统一 Runtime 门限 | 30-effect/46-denial 合同、完整 pytest 及用户前台真实恢复链通过 | completed |
| UT-003 | 2026-08-16 | Grok Build / 浏览既有 Notebook 的 Page 缩进树 | Writes + UI Control | `expand_section`/`expand_hierarchy` 把真实存在的相邻 `page_level` 跳级当成整本 snapshot 非法；`reliability` / `response` / `capability` | L1 后跟随的 L3 直接映射为该 L1 的子节点 | 确定性 Expand/Query 合同已覆盖；用户前台对同 Notebook 全部相关 Section 的真实 `expand_section` 已通过 | completed |
| UT-004 | 2026-08-17 | 用户 / 整理既有 Notebook 的层级与顺序 | 批量 Reparent：Writes + Organize；批量 Delete：Deletes；批量 Rename：Writes；批量 Create：Container=Create、Page=Create+Writes；Sort：Writes | 单对象调用导致 Agent 逐项编排；缺少按受控字段整理一个父节点完整直接子序列的能力；`workflow` / `capability` / `reliability` | 原 `create_*`、`rename_*`、`reparent_*`、`delete_*` 工具增加互斥的 `items[1..20]` 模式，不新增 `batch_*` 工具；唯一新增 `sort_children`，按父类型推断 Section/Page，只排直属子节点，不递归且不排序 SectionGroup | 9 个具名允许路径均有最新 human-gated passed/restored/closed 证据；`create`/`reorder-page` 拒绝探针真实通过；四类 partial 自动合同闭合 | completed |
| UT-005 | 2026-08-17 | 用户 / 授权模型审阅 | 现状：Copy = Writes + Copy；目标：Copy = Create + Writes，Move = Create + Writes + Deletes | 独立 Copy gate 与创建/写入语义不对应；Move 本质为经验证 Copy 后的可恢复源删除，现有 Copy 依赖使其授权模型不够一致；`authorization` / `workflow` / `capability` | 已实现独立 Create；移除独立 Copy 授权；Container Create=Create，Page Create/Copy=Create+Writes，Move=Create+Writes+Deletes | 授权负合同及 Create、4 Copy、3 Move 的 8 个最新 human-gated run 全部通过并精确关闭 | completed |
| UT-006 | 2026-08-17 | 用户 / 在 online-backed Notebook 中创建 SectionGroup | Writes | 可见 OneNote 同步在 `create_section_group` 后触发；`reliability` | 仅记录环境观察，不修改同步行为或契约 | 用户前台观察；尚未记录 bridge audit 或独立复现矩阵 | observed |
| UT-007 | 2026-08-17 | 用户 / 执行层级变更 | 依目标操作的现有 gate | 生产 Reparent 已移除逐 Page XML 比对，但容器 Reorder 仍在生产 read-back 中读取子树 Page XML；层级变更的验证边界不一致且会随无关正文增长；`reliability` / `performance` | Reparent、Page Reorder、Section Reorder 的 production read-back 均只验证 content-free hierarchy；容器响应声明 Page content=`not_read` | Reparent 既有证据保留；最新 `reorder-section` 的 4 次 mutation 均 `page_content=not_read`，7 Page manual comparator 前后/恢复一致 | completed |
| UT-008 | 2026-08-17 | 用户 / 读取既有 Page 的富文本内容 | 无 gate | `get_page_text` 只返回可见 plain text；粗体、链接、字体/颜色、列表、表格和 HTML 结构不进入响应；`capability` / `response` | `get_page_text(mode="rich"|"plain")` 已实现；默认 rich=`sanitized_html_v1`，显式 plain 保留兼容响应；完整 OneNote MathML conditional wrapper 现会安全规范化为 canonical `<math>` | `reparent-page`、`copy-section`、`copy-page` 真实 rich/plain/bounded/safety/signature 合同全部通过并精确关闭 | completed |
| UT-009 | 2026-08-17 | Grok Build / 将已有 Page 重建式 Move 到 disposable 目标 Notebook | 现状已迁移为 Create + Writes + Deletes | 目标区常已建出副本，但 `verify_copy` 的 `page_equivalence` 失败，源删除被挡住，结果为 `partial_failure` / `copy_only`；`reliability` / `response` | 新增 `semantic_content_v1`：分项验证有效标题、富文本/List/Tag、表格、二进制对象与非空 Outline；只忽略三类已知 COM 规范化，不完整投影回退 strict | 4 个 Copy run 的 11 份 report 全部 verified/lossless、零 issue；3 个 Move run 均先通过 Copy gate 再非永久删源 | completed |
| UT-010 | 2026-08-18 | 用户 / 删除或重排带缩进子页的父 Page，并统一 Page scope 参数 | Deletes / Writes；Reparent 另需 Organize | 原 `delete_page` 与 `reorder_page` 缺少一致的单页/子树选择与子页保护；原公开范围使用字符串 `page_scope`；Page Reparent/Delete batch 若逐项判断提升还会受前项拓扑变化影响；`reliability` / `schema` / `capability` | 所有公开 `page_scope` 已改为 `include_subpages: bool = false`；Delete/Reorder 单页路径提升并保护排除后代，完整子树路径按块处理；Page Reparent/Delete batch 从同一快照计算整批有效 scope 与一次性提升计划 | 生产、schema、自动化、manual-validation 场景和文档已同步；纯测试与 dry-run 通过，等待用户 fresh disposable human-gated 复测 | validating |

单项状态只使用：`observed`、`accepted`、`implementing`、`validating`、`completed`、`rejected`、`deferred`。`rejected` 必须记录为什么不是产品问题；`deferred` 必须记录风险、依赖或不阻塞理由。

### UT-001：Grok 读取 Page 前未处理 OneNote GUI readiness

- **问题描述**：2026-08-16，Grok Build 1.0.4 收到读取既有 Page 的指令时，没有先调用 `health_check`，也没有给出 GUI 未启动时的启动或用户行动路径。观察配置中 7 类公开 gate 均关闭，含 `UI Control=false`；OneNote GUI 未启动。
- **问题分析**：纯 read 按当前验收标准允许绕开 GUI readiness，因此这不是 read Runtime 门限问题。摩擦在于 Agent 未发现 Session health 工具，也没有形成“health 发现未 ready → 为后续授权 effect 准备恢复”的调用心智模型；UI Control 关闭还使显式 launch 不可执行。
- **改进决策**：`health_check` 保持无 gate、check-only，并在 session 开始时对 Agent 清晰可发现。`launch_onenote_gui` 只保留最小 UI Control gate；受支持的用户测试配置直接提供该授权。启动始终是显式 UI effect，不能隐藏在 health、read、初始化或 tools/list 中。纯 read 继续允许无可见 GUI 执行。
- **验收标准**：`health_check` 的 description 明确“session 开始时检查，授权 effect 前必须 ready”；`launch_onenote_gui` 的 description 明确“health 未 ready 时启动，随后重新 health check”。Grok Build 至少应能发现这条链；UI Control 关闭时给出可行动的开启授权或手动启动提示。Page read 在 GUI 已运行和未运行时均不得因 GUI 门限被拒绝。
- **实现位置**：`src/local_onenote_mcp/tools/system.py` 将 `health_check` 描述固定为 session 开始时发现、授权 effect 前必须 ready；`launch_onenote_gui` 描述固定为 health 未 ready 时显式启动、再次 health、再重试原 effect。`src/local_onenote_mcp/desktop.py` 的 typed readiness failure 现在返回固定恢复顺序、UI Control gate 名称、当前 gate 投影和手动启动替代路径。`.grok/config.toml` 的 developer user-testing profile 显式开启 Writes 与最小 UI Control，其余五类 effect gate 保持关闭；可复用配置仍默认关闭全部 gate。
- **自动化证据**：`tests/test_server.py` 冻结两项 Session tool description 与 health failure envelope；`tests/test_desktop.py` 覆盖 process absent、process-only、窗口证据不一致和 probe 不确定；`tests/test_project_mcp_configs.py` 冻结四类客户端配置及 Grok 的 reviewed static authorization。聚焦组合 `371 passed`；完整基线 `1178 passed`。自动化没有启动 OneNote 或执行真实 mutation。
- **用户真实复测证据**：第一阶段，用户于 2026-08-16 提供前台截图并确认 Agent 按预期行动；可见 content-free 轨迹为 `Create Notebook ×1（GUI absent 后拒绝）→ Health Check ×1 → Launch OneNote GUI ×1 → Health Check ×1 → Create Notebook ×1（成功）`，证明 Grok 能在 typed precondition failure 后发现并完成显式恢复链。第二阶段，用户在前台完成此前剩余的原始 Page read 与 proactive session-health 验收，并明确确认 UT-001 整体通过。用户未提供第二阶段的完整逐调用 transcript，因此本文只记录用户通过结论，不补写未观察到的调用次数、GUI 子状态或 `backend_calls` 数值。
- **完成判定**：description、typed recovery、最小 UI Control 配置和确定性合同均已落地，用户又明确确认原始 Page read 与 proactive session-health 前台验收完成；UT-001 因而转为 `completed`。上述真实调用均由用户通过前台 MCP client 发起，不是 Agent 运行 manual-validation runner。
- **合同影响**：已同步 Session description、恢复提示、用户测试配置、README 和公开 Tool contract；Runtime 门限范围由 UT-002 的独立 policy 定义。

### UT-002：授权 effect 未强制 OneNote GUI readiness

- **问题描述**：静态审查发现，生产代码只在 `health_check` 调用 `require_onenote_desktop()`。任何需要七类公开 gate 的 operation 都可在 authorization 后直接进入 backend handler，因此没有可见 OneNote GUI 时也可能发起 side effect。
- **问题分析**：Operation Runtime 的 `PLATFORM_PREFLIGHT` 目前只有阶段名称，没有 GUI probe；authorization 只校验各 gate。结果是 write、delete、organize、Copy/Move、Local File IO、UI navigation 和 Notebook Lifecycle 等路径缺少共同门限。纯 read 则按验收标准必须继续保持该门限之外。
- **改进决策**：把 native GUI readiness preflight 放入 Operation Runtime 的 binding/spec，在 authorization 之后、首个 backend side effect 之前统一执行。所有需公开 gate 的 operation 都受保护；`launch_onenote_gui` 是恢复工具，明确豁免。不得将启动隐藏进 read、mutation、health、初始化或 tools/list。
- **验收标准**：GUI 未运行、仅有后台进程、窗口不可见或 probe 不确定时，受保护 operation fail closed 且 `backend_calls=0`。typed failure 明确“未满足 OneNote GUI ready 前置条件”，并引导 Agent 执行 `health_check → launch_onenote_gui → health_check → 重试原 operation`；UI Control 未授权时，明确要求开启该最小 gate 或手动启动。`launch_onenote_gui` description 必须说明：所有授权 operation 前均需可见 GUI。
- **实现位置与策略解耦**：`OperationSpec.platform_preflight_policy`、`OperationBinding.platform_preflight` 与 `operation_catalog.PLATFORM_PREFLIGHT_POLICIES` 独立于 `authorization_policy`/authorizer。生产 catalog 显式将 30 个授权 effect 绑定到 `onenote_gui_ready`，而非从 operation kind 或授权 gate 隐式推导；`health_check`、21 个纯 read 及恢复入口 `launch_onenote_gui` 绑定 `none`。Runtime 固定按 `authorization → platform_preflight → coordination → handler` 执行，readiness 拒绝不取得 lease、不推进 generation、`backend_calls=0`。
- **自动化证据**：`tests/test_operation_runtime.py` 冻结完整 52-tool authorization 与 platform-preflight 双矩阵；30 个受保护 effect 在最小授权已满足但 GUI 未 ready 时全部停在 `platform_preflight`，46 个缺 gate 组合继续先停在 `authorization`，health/read/launch 即使 probe 被设为失败也不调用该前置策略。根 `conftest.py` 对既有纯 tool/service 合同提供 deterministic ready mock，真实 absent/process-only/unknown 拒绝由具名合同显式覆盖。聚焦组合 `371 passed`；完整基线 `1178 passed`。
- **用户真实复测证据**：用户于 2026-08-16 在前台 Grok Build、`Writes=true`、`UI Control=true`、其余五类公开 gate 关闭的静态 profile 下创建 disposable Notebook。GUI absent 时首次 `Create Notebook` 没有继续成功执行；Agent 随后依次调用 `Health Check → Launch OneNote GUI → Health Check`，最后重试 `Create Notebook` 并成功。用户明确确认行为符合预期。该轨迹与自动化的 `platform_preflight`、`backend_calls=0` 和豁免矩阵共同满足本项实现与真实恢复验收；process-only、窗口不可见和 probe 不确定继续由 deterministic 合同覆盖，不把截图扩张为这些状态的真实观察。
- **真实执行边界**：上述真实 mutation 与 GUI launch 均由用户通过前台 MCP client 发起，不是 Agent 运行 manual-validation runner；本次实现 Agent 从未执行 `run.py <scenario>|all`。现有每种 mutation-policy 权限继续由具名 manual-validation scenario 承担独立真实后端覆盖。
- **合同影响**：已同步 Operation Runtime/registry、typed error envelope、Session description、README、`docs/design/operation_runtime.md`、`docs/design/tool_contracts.md` 与当前架构；不放宽七类 gate，不改变 read 可用性，也不增加隐式 GUI side effect。

### UT-003：真实 Page 缩进允许相邻 `page_level` 跳级，expand 却整本失败

- **问题描述**：2026-08-16，Grok Build 按用户任务浏览一个已打开 Notebook 中某一 Section 的 Page 缩进树。`expand_notebook` 在 Section 叶节点成功返回。随后对目标 Section 调用 `expand_section`，以及对同一 Section root 调用 `expand_hierarchy`，均以 `validation_error` 失败，稳定文案为 `discontinuous Page indentation level`。错误 details 中的 Section ID 属于**同一 Notebook 内的另一个 Section**，不是本次请求的 root。`query_page` 对请求 Section 与报错 Section 均可返回完整 Page metadata。观察配置为 `Writes=true`、`UI Control=true`，其余五类公开 gate 关闭；任务为只读浏览。本文不记录 Notebook/Section/Page 名称、标题或真实 COM ID。
- **问题分析**：OneNote Desktop 的 `pageLevel` 合法范围是 1–3；`parent_page_id` 是按同 Section 有序序列派生的缩进边，不是 COM 容器父级。报错 Section 的 live metadata 中存在相邻 `page_level` 从 1 直接到 3 的序列：某一 `page_level=1` 的 Page 之后紧跟 `page_level=3` 的 Page；随后连续若干 Page 仍为 `page_level=3`，派生父级都指向该 L1。同 Section 其余 L3 都是 `2→3` 或 `3→3`。`query_page` 使用的 stack 派生已经把跳级页挂到最近更浅祖先。旧的 `expand_*` snapshot 校验却把「相对前一页 `page_level` 增幅大于 1」当成非法图，并且校验范围是整本打开的 Notebook，因此浏览任意含 Page 的 root 都会被无关 Section 毒死。这不是 Agent 选错工具，也不是 GUI/授权问题。
- **改进决策**：将「L1 后跟随的 L3」**直接映射为该 L1 的子节点**：`parent_page_id` 等于紧邻前序 L1 的 ID，Expand 树中该 L3 出现在该 L1 的 `children` 里，中间不插入虚构 L2。这是 COM 允许的 `pageLevel` 间隙（`page_level` 仍在 1–3），不是 incomplete/invalid hierarchy snapshot。紧随其后、仍为 L3 的兄弟页同样是该 L1 的直接子节点。`expand_section`、`expand_page` 与 `expand_hierarchy` 必须与 `query_page` 共用这一映射并返回该树。校验只拒绝真正无法投影的情况：缺 ID、重复 ID、环、跨 Section 缩进父级、`page_level` 越出 1–3、或首个 Page 不是 level 1。相邻跳级若需对调用方可见，只能作为 typed warning / 拓扑标注，不得让整本或无关 Section 的 Expand 失败。不得为此放开 raw XML、按名称选择或无界扫描。
- **验收标准**：对观察到的真实跳级序列，`query_page` 给出 `page_level=3` 且 `parent_page_id` 等于紧邻前序 L1；`expand_section` / `expand_page` / `expand_hierarchy` 将该 L3 作为该 L1 的直接子节点返回，不因缺少 L2 失败。同 Notebook 中不含跳级的 Section 不得再被另一 Section 的间隙毒死。自动化必须覆盖 `pageLevel=1` 后直接 `pageLevel=3` 的 fixture，证明 Expand 把 L3 挂在该 L1 下，而不是 `discontinuous Page indentation`。真实 OneNote 复测只能由用户前台执行；不得由 Agent 运行 `run.py <scenario>|all`。
- **实现位置**：`src/local_onenote_mcp/hierarchy.py` 提供唯一的有序 stack 父级派生，parser 与 `src/local_onenote_mcp/services/hierarchy.py` 的 snapshot 校验共同调用；L3 因而稳定挂到前序 L1。校验不再把相邻 `page_level` 增幅大于 1 当作非法图，但仍限制在 1–3 且要求 Section 首页为 level 1。parser 对缺失 `pageLevel` 保持默认 L1，对显式 `0`、负数、超范围或非数字值则保留为不可投影状态，由目标 Notebook 的 Expand 校验 fail closed，而不是静默修正为 L1。`tests/test_hierarchy_browsing.py` 通过 shipped `HierarchyService.expand_typed` / `expand_hierarchy` / `metadata_query` 覆盖该映射、同 Notebook 兄弟 Section 隔离，以及非法层级/非 1 根的 fail-closed。对象模型与 Expand 合同已改为当前实现态。
- **自动化证据**：`tests/test_hierarchy_browsing.py` 驱动 shipped `HierarchyService.expand_typed` / `expand_hierarchy` / `metadata_query`：L1 后接两个 L3 时树中 L3 均为该 L1 的直接子节点，`parent_page_id` 与 Query 一致；同 Notebook 中不含跳级的兄弟 Section 在同一 snapshot 内仍可 Expand；显式非法 COM `pageLevel` 经 parser 后由 Expand 拒绝。未再保留 `discontinuous Page indentation` 成功路径。聚焦 `tests/test_hierarchy_browsing.py` + `tests/test_metadata_query.py` + `tests/manual_validation/tests/test_test_utils.py` 为 `66 passed`；完整基线 `1200 passed`。自动化没有启动 OneNote 或执行 `run.py <scenario>|all`。
- **用户真实复测证据**：实现前的前台只读会话确认了旧 Expand 失败与 Query 可定位跳级。2026-08-16 用户重新打开 session 后，在前台 Grok Build 要求对同一打开 Notebook 内一组相关兄弟 Section 全部调用 `expand_section`。五次调用均 `ok=true`，无 `discontinuous Page indentation`。含相邻 `1→3` 的那一个 Section 成功返回完整树：一个 L1 的 `children` 下直接挂 5 个 `page_level=3` 的页，`parent_page_id` 均为该 L1，无虚构 L2。其余四个无此间隙的兄弟 Section 同样成功，页数与 Query 一致。本次只读轨迹为 `query_section ×1 → expand_section ×5`。Agent 未执行 `run.py <scenario>|all`。
- **真实执行边界**：上述真实 Expand 由用户通过前台 MCP client 发起，不是 Agent 运行 manual-validation runner。live 复测覆盖了用户任务所用的 `expand_section`；`expand_page` / `expand_hierarchy` 与其共用同一 snapshot/tree builder，由确定性合同覆盖，不把本次 live 扫描扩张为那两个入口的独立桌面观察。
- **合同影响**：Expand 现在与 Query 共用「L3 直接挂到前序 L1」映射。不改变 52 项工具面、七类 gate、exact ID 目标或 mutation 权限；Reorder 仍不得*创建*相邻跳级。公开对象模型、hierarchy browsing 合同与 parser 说明已同步为当前行为。

### UT-004：整理 Notebook 时需要批量 Reparent/Delete/Rename/Create 与单父节点 Sort

- **问题描述**：2026-08-17，用户在整理既有 Notebook 的层级和顺序时确认存在五类实际需求：把多个对象挂到同一目标、对多个不再需要的对象执行可恢复删除、为多个已确认对象更名、在同一已确认父级下创建多个同类对象，以及按常用字段将一个父节点下的直接子节点整体升序或降序排列。实施前公开的 `reparent_*`、`delete_*`、`rename_*`、`create_section`、`create_section_group`、`create_page`、`reorder_page` 和 `reorder_section` 都是单对象操作；Reorder 还要求调用方用 `after_*_id` 表达单次相对位置，调用方只能自行逐项编排。
- **问题分析**：单对象 typed Reparent/Delete/Rename/Create 契约及其底层收敛、对账、目标位置回传和 Operation Runtime 已为可靠批处理提供基础，但未提供一个能表达“多对象到单目标”“多个已确认对象可恢复删除”“显式 ID 到新名称映射”或“单个父级下创建多个同类对象”、统一预检并返回 partial outcome 的边界。Delete 的风险高于 Reparent：同一请求不得同时包含某容器及其后代，也不得把 partial 结果伪装成全量成功或以宽泛恢复掩盖已进入回收站的对象。Rename 不应以名称搜索或通用替换规则选择目标，且首版需拒绝同一父级的名称交换/循环，避免引入临时名 mutation。Create 在成功前不存在对象 ID，必须以每项输入序号和创建后的精确分配 ID 对账；其授权模型现已由 UT-005 冻结为 Container Create=Create、Page Create=Create+Writes。Copy/Move 不属于可直接批量化的单一 hierarchy mutation：Copy 本身包含经验证的目标创建、内容写入与 fidelity/read-back，Move 还必须在 Copy 成功后执行可恢复源删除。若批量化，会把每项的新旧 ID、内容保真、阶段依赖与 source-delete partial recovery 交叉放大，难以保持可解释、fail-closed 的控制面，故本轮不纳入。把多次 `after_*_id` 操作组合成批量 Reorder 也会把最终顺序、依赖、Page 缩进与中间状态耦合在一起；按单父节点的完整直接子序列进行 deterministic Sort 更符合“整理”的意图。Sort 不应接受任意属性路径或名称选择目标，而应只允许当前 hierarchy 对各类型共同投影的少量稳定键。Page 不能逐项打散排序：应把一级 Page 与其缩进子页视为同一排序块，保留块内顺序和层级。
- **改进决策**：用户进一步冻结公开形态：批处理不拆出任何 `batch_*` Tool，而是在原 `create_section_group`、`create_section`、`create_page`、三个 `rename_*`、三个 `reparent_*` 和三个可恢复 `delete_*` 中加入与单项字段互斥的 `items[1..20]` 模式；单项调用保持原工具名和语义。所有 batch 只接受同类型、同一 Notebook 的精确目标，先整体完成权限、身份、confirmation、预算、重复目标、祖先/后代或 Page scope 重叠、循环和名称冲突预检，再按输入顺序执行；它们不是事务，首个失败或不确定即停止，返回 `applied/failed/not_attempted`，不宽泛 rollback 或盲重试。Create 的所有项共用 confirmed parent；Section/SectionGroup 名称需唯一，Page 保留原单项工具允许重复标题并依 allocated ID 对账的语义。Reparent 的所有项共用 destination；Delete 永远非永久；Rename 是显式 ID→新名称映射并拒绝名称交换/循环。授权完全复用原工具矩阵。唯一新增 Tool 是 `sort_children`：`key=name|created|modified`，`direction=ascending|descending`，同键稳定；`child_type` 保留但可省略，并只作父类型推断的一致性断言。Notebook 或 SectionGroup 作为 parent 时只能排序直属 Section，Section 或 Page 作为 parent 时只能排序直属 Page；不排序 SectionGroup，不接受 recursive 模式。Page 直属子节点以各自完整缩进后代组成不可拆分 block，仅排序这些 block，不递归排序 block 内部。仍不实现批量 Reorder、Copy 或 Move。
- **验收标准**：批量 Reparent 的公开接口与响应明确最大对象数、每个对象的 exact-ID/precondition、唯一目标、逐项状态及最终 live hierarchy 摘要；缺少 Writes + Organize 时在 backend 调用前拒绝。批量 Delete 的接口与响应明确最大对象数、每个对象的 exact-ID/precondition、非永久删除、逐项回收站结果和 partial recovery 指引；缺少 Deletes 时在 backend 调用前拒绝，任何预检失败均不得删除任一项。批量 Rename 的接口与响应明确最大对象数、每项 exact-ID/confirmation/new name、逐项旧新名称和结果；缺少 Writes 时在 backend 调用前拒绝。批量 Create 的接口与响应明确唯一 confirmed parent、资源类型、最大对象数、每项输入序号和新分配 ID；授权矩阵必须与 UT-005 同步，缺少该 Tool 所需的 Create/Writes 任一 gate 时在 backend 调用前拒绝。`sort_children` 的公开接口明确唯一 `parent_id`、受控 `key` 枚举、排序方向、当前完整子序列的 optimistic confirmation 与同键值稳定规则；Notebook/SectionGroup 仅排序直属 Section，Section/Page 仅排序直属 Page，SectionGroup 不作为待排序子项且无 recursive 模式；仅在确认完整一致且每项均有可比较键时执行，并以最终完整顺序及 Page block 层级完成收敛验证。自动化覆盖同 Notebook/同类多对象到单目标的 Reparent、Page `id_map`、冲突/循环/重叠/过大请求拒绝、失败后的停止与 partial outcome；批量 Delete 的 Page/Section/SectionGroup、gate 拒绝、确认/回收站/重复/祖先-后代/跨 Notebook/永久删除拒绝与 partial outcome；批量 Rename 的 Page/Section/SectionGroup、确认/名称规范化/重复或交换/循环拒绝与 partial outcome；批量 Create 的三种类型、父级/名称/预算/授权/分配 ID/partial outcome；以及 Sort 的每种键/升降序、重复键稳定性、缺失或不可解析时间拒绝、父类型推断/冲突拒绝、容器直接子序列、Page block 与并发确认不一致拒绝。四类 batch 的中途 partial 必须由确定性 fault injection 证明 `applied/failed/not_attempted`、停止后续项、人工恢复指引、零 rollback 与零 replay；这是 partial 的最终验收依据，不故意在真实 OneNote 中制造后端故障。所有 production hierarchy read-back 依 UT-007 保持 content-free；对应具名 manual-validation scenario 仍逐 Page 比较内容。用户使用 fresh disposable Notebook 前台确认允许路径与 mutation 前预期拒绝证据后才能关闭本项。
- **实现与验证状态**：production 已落地上述同名双模式 Tool adapter、Operation Registry handler 和有界 Service 预检/顺序执行；公开面仅由 52 增至 53，新增项只有 `sort_children`，不存在可注册的 `batch_*` 名称。成功 Reparent batch 在全部 item 完成后再次 live 对账每个最终 ID/父级并返回 content-free `final_hierarchy`；整体对账失败转为 partial recovery，不能把逐项返回伪装成整批成功。`sort_children` 已实现父类型自动推断、可选 `child_type` 冲突拒绝、三种受控 key、升降序、稳定排序、完整 direct-child optimistic confirmation、SectionGroup 槽位保持、leveled Page block 保持和 content-free hierarchy 收敛；Sort 的确认序列上限独立为 1000，再受 resource/Page budget 约束，不误用 batch 的 20 项上限。`tests/test_batch_sort.py` 的 60 项确定性合同覆盖全部 12 个原工具名 dispatch、三类对象正向路径、授权、typed schema、整体预检、跨 Notebook、重复/重叠/循环/回收站/名称冲突/预算拒绝、分配 ID、逐项停止与 partial、Reparent 最终整体对账，以及 Sort 三键双方向稳定性、父类型推断/冲突、SectionGroup 槽位、Section/Page parent、leveled Page block、缺失/非法时间和并发确认；四类中途 partial 现还共同冻结公开 `partial_failure` envelope、`applied/failed/not_attempted`、输入序号、人工恢复、`rollback_attempted=false` 和 `mutation_replayed=false`。现有 `create`、`rename`、`delete`、`reparent-*`、`reorder-page`、`reorder-section` 具名 scenario 已扩展到同名 `items` 或 `sort_children` 路径并保留逐 Page manual evidence；`create` 和 `reorder-page` 默认路径新增 typed preflight 拒绝、read-only bridge audit 与 unchanged snapshot 证据。所有 Reparent scenario 还要求 `final_hierarchy.page_content="not_read"`。manual-validation 纯合同 `605 passed`，完整 pytest `1301 passed in 63.59s`；两个相关 `--dry-run --json` 均通过。canonical README、Tool contract、架构、Operation Runtime 与 manual-validation 文档已同步。Agent 仅运行纯测试和 `--dry-run`。
- **首轮用户真实复测（2026-08-17 23:25–23:33）**：用户前台连续运行 UT-004 的 9 个具名场景。`delete`=`run-2026-08-17-23-26-27`、`reparent-page`=`23-26-54`、`reparent-page-with-level`=`23-27-42`、`reparent-section`=`23-29-07`、`reorder-page`=`23-31-41`、`reorder-section`=`23-32-18`（后五项沿用 `run-2026-08-17-` 前缀）均为顶层/scenario `passed`。Delete 的 Page/Section/SectionGroup 三个同名 batch 均 applied 且 `permanently=false`；Page/Section Reparent 的 batch 响应均完成 content-free `final_hierarchy` 对账，Page ID remap 与逐 Page 内容比较通过；`sort_children` 真实覆盖 Page parent、Notebook parent 与 SectionGroup parent，均按 `name` ascending 收敛、`observed_outcome=applied`、`verification_scope.page_content="not_read"`，Page parent 只排序两个直属 L2 child。其余三个 run 均为 `failed_closed`：`create`=`23-25-33` 的 SectionGroup/Section/Page batch 各 2 项均已 applied，随后因场景 allowlist 缺少清理用 `delete_section` 失败；`rename`=`23-26-09` 的 Page batch 正向和恢复调用均 applied，但 restored snapshot 与 before 不一致；`reparent-section-group`=`23-30-23` 的三个正向 batch 均 applied，逆序恢复 case 3 成功后，case 2 的生产响应短暂报告 applied，但紧随其后的 manual snapshot 仍观察到目标位于 Notebook 根，因而 fail closed，case 1 未再尝试恢复。9 个 run 的 lifecycle/lease 均已关闭，失败现场和 durable evidence 均保留。
- **首轮失败修复（2026-08-18）**：Create scenario 的静态 allowlist 现完整包含 `delete_page/delete_section/delete_section_group`，继续只允许非永久 cleanup；Page Rename snapshot 新增剔除 Title 后的 canonical body hash，正向允许标题必然改变但仍严格比较正文、拓扑、无关 Page stable hash 与 content-object identity，恢复要求原标题及完整 canonical Page 语义复原，不再把 OneNote 对同一标题 XML 的重序列化当成内容损坏；SectionGroup Reparent fixture 升为 v3，在两个恢复目标 source 下各保留两个固定 source anchors，使目标搬出后源容器不为空，避免把该环境变量混入 Reparent 能力判断。同时针对证据中的延迟回退窗口，生产 `reparent_section_group` 从连续 2 次提高为 4 次稳定 hierarchy 观测；Page/Section 仍为 2 次，通用 4 秒 deadline 不变，不增加 mutation 重试或重放。聚焦 Reparent/Sort 生产回归 `96 passed`，完整 pytest `1296 passed in 63.38s`；完整测试未访问 OneNote 或执行 mutation。
- **定向真实复跑（2026-08-18 01:22–01:25）**：`create`=`run-2026-08-18-01-22-55`、`rename`=`run-2026-08-18-01-23-38`、`reparent-section-group`=`run-2026-08-18-01-24-10` 均为顶层/scenario `passed`、`restored=true`、`lifecycle.closed=true`。Create 的 Page/Section/SectionGroup 三个同名 batch 均为 2/2 `applied`，六个 batch 目标与两个重复标题单项 Page 全部以 `permanently=false` 清理。Rename 的 Page/Section/SectionGroup 正向与恢复各执行一次，六个 reconciliation 均为 `observed_outcome=applied`、`mutation_replayed=false`，Page 的 title-excluded body 与完整 canonical restore 合同通过。SectionGroup 的 Notebook→Group、Group→Notebook、Group→Group 三个正向 batch 均 `applied`，每项均记录 `stable_observations=4`、单次 `UpdateHierarchy`、无 mutation replay；三个逆序恢复也均 applied，最终 hierarchy ID、Page 内容、content-object ID、Notebook 归属和无关关系全部保持。三个 run 都没有 forbidden lifecycle call，现场已正常关闭。
- **最终拒绝探针复跑（2026-08-18 01:50–01:52）**：用户前台运行更新后的 `create`=`run-2026-08-18-01-50-18` 与 `reorder-page`=`run-2026-08-18-01-52-05`。两个 run 的顶层、run-state 与 scenario 均为 `passed`，`restored=true`、`lifecycle.closed=true`。Create 的规范化重名 Section batch 和 Sort 的 Page-parent/Section-child-type 冲突均返回 `validation_error`、`mutation_stage=preflight`、`mutation_attempted=false`；各自 bridge audit 只观察到一次 `get_hierarchy`，`mutation_bridge_calls=0`，拒绝后 snapshot 不变。真实负路径没有故意制造中途 backend failure。
- **当前完成判定**：Delete、Create、Rename、Page/Section/SectionGroup Reparent 及三类 parent 的 name-ascending Sort 具名允许路径，均有加固后或当前实现的最新 human-gated passed/restored/closed 证据；首轮三条 fail-closed 路径已由定向真实复跑闭合，两个 mutation 前拒绝探针也已真实通过。Batch partial-failure 已按最终依据由四类确定性合同闭合。实现、自动化合同、真实允许/拒绝路径和文档证据全部满足验收标准，UT-004 转为 `completed`。
- **范围边界**：不实现批量 Reorder、Copy 或 Move；不得引入 raw XML、按名称或其他 `key` 选择 mutation 目标、无界 hierarchy scan、跨 Notebook batch、批量 Notebook/PageContentObject/永久 Delete、通用 Rename pattern/template、自动恢复或宽泛 rollback；不放宽 Writes、Organize、Deletes、UT-005 确定后的 Create/Write 组合、confirmation、收敛、对账或现有 default fail-closed 门限。Sort 的 `key` 只决定既有精确 ID 子序列的顺序，不能用于选择 mutation 目标；排序永远是 direct-only，SectionGroup 不进入待排序子项，Page block 内部不递归排序。

### UT-005：新增 Create 权限，按创建、写入与删除收敛 Copy/Move 授权

- **问题描述**：2026-08-17，用户在审阅授权体系时指出独立的 Copy 类别与其实际副作用不一致。当前 `copy_*` 同时要求 Writes 和 `LOCAL_ONENOTE_ENABLE_COPY`；Move 则在此基础上再要求 Deletes。项目的公开权限中目前没有独立的 Create gate，Create 与写入均由 Writes 保护；但 Copy 实际是在已验证的目标中创建并写入内容，Move 则是在该 Copy 成功后执行可恢复的源删除。
- **问题分析**：额外 Copy gate 不对应独立的副作用类别，反而使调用方为 Copy/Move 申请一项不能从操作结果解释的权限。应将“创建对象”从 Writes 中独立出来：Copy 的风险模型是“创建 + 写入”，Move 的风险模型是“创建 + 写入 + 删除”。这不是放宽权限：新增 Create、现有 Writes 和 Deletes 都继续默认关闭；Copy 缺少 Create 或 Writes 时必须零 backend call 地拒绝，Move 缺少其中任一权限时也必须在 backend 调用前拒绝。
- **改进决策**：新增独立的 Create authorization category，并移除独立 Copy authorization category 及其环境变量。`copy_*` 固定要求 Create + Writes；`move_page`、`move_section`、`move_section_group` 固定要求 Create + Writes + Deletes，不再要求 Copy。所有现有 `create_*` Tool 也必须按其是否 materialize 新对象及是否写入初始内容，经过独立矩阵审查后绑定 Create 与必要的 Writes；不得仅因名称包含 create 而作隐式授权推断。Copy/Move 的 typed ID、confirmation、copy fidelity、source-delete、partial failure、convergence、reconciliation 与 default fail-closed 合同均不因授权收敛而放宽。
- **验收标准**：Registry、policy、health capability、authorization matrix、tool descriptions、README、客户端示例和 design 文档均声明新增 Create 类别与更新后的权限组合；`LOCAL_ONENOTE_ENABLE_COPY` 不再作为生产授权开关或静默兼容别名。所有 Copy Tool 在 Create/Writes 任一为 false 时零 backend call 拒绝，二者为 true 时才可进入既有 Copy 安全门；所有 Move Tool 在 Create/Writes/Deletes 任一为 false 时零 backend call 拒绝，三者为 true 时才可进入既有 Copy/Move 流程。纯合同覆盖所有受影响 Tool 的允许/拒绝矩阵、Create Tool 的精确绑定及原有 fidelity/partial failure 不变量；每条受 mutation policy 保护的真实执行路径仍由既有具名 manual-validation scenario 覆盖，用户前台复测后才可关闭本项。
- **实现与验证状态**：已新增默认关闭的 `LOCAL_ONENOTE_ENABLE_CREATE` 与 `MutationPolicy.create_enabled/require_create`；`LOCAL_ONENOTE_ENABLE_COPY` 已从生产读取、四类项目客户端配置和 manual-validation policy 投影移除，旧变量即使为 true 也不是 alias。Registry 固定 `create_notebook/create_section_group/create_section=create`，`create_page/copy_*=create_write`，`move_*=create_write_delete`；Service 内部防线与公开 description 同步。`health_check.mutation_policy` 投影 Create，`copy_move.authorization` 明示组合与 `independent_copy_gate=false`。manual-validation 的 `ScenarioPolicy`、fixture/tool policy catalog 和静态 spec 已按 Create/Write/Delete 闭包迁移。聚焦 Policy/Runtime/config 合同 `181 passed`，manual-validation 纯合同 `589 passed`；完整基线见本 TODO 末尾统一证据。
- **用户真实复测与完成判定**：用户于 2026-08-17 在前台依次完成 8 个 human-gated run：`create`=`run-2026-08-17-21-03-30`，四个 Copy 为 `copy-page`=`21-03-49`、`copy-section`=`21-06-31`、`copy-section-group`=`21-08-07`、`copy-notebook`=`21-09-57`，三个 Move 为 `move-page`=`21-11-01`、`move-section`=`21-12-14`、`move-section-group`=`21-12-49`（本段后七项均沿用 `run-2026-08-17-` 前缀）。8 个顶层与 scenario 状态均为 `passed`，lifecycle 均 `closed=true`。Create 返回两项 fresh/distinct allocated/read-back Page ID 并完成精确 cleanup/restore；四个 Copy 共 11 份 report，全部 `copy_contract_satisfied=true`、`verified=true`、`lossless=true`、零 issue，其中 `copy-notebook` 在 Deletes=false 下成功并按合同关闭而不删除目标；三个 Move 均在 verified/lossless Copy 后报告 `source_deleted_nonpermanently=true`。结合 47 条缺 gate 零 backend-call 自动化负合同与真实允许路径，本项验收闭合，转为 `completed`。这些结论只覆盖当前实现、当前 OneNote 环境和具名 disposable fixture，不外推为任意环境保证。
- **范围边界**：不新增默认授权、不把 Copy/Move 变为纯 read、不允许绕过 Create/Writes/Deletes、不引入通用 mutation、raw XML、名称选择或跨 Notebook Reparent；复制与重建式 Move 的既有内容保真、最小权限、可恢复删除和 fail-closed 边界保持有效。

### UT-006：online-backed Notebook 创建 SectionGroup 后出现 OneNote 同步

- **观察**：2026-08-17，用户在 OneNote Desktop 已打开的 online-backed Notebook 中调用 `create_section_group` 后，观察到 OneNote 的同步行为被触发。
- **证据边界**：这是用户前台对 OneNote UI 行为的观察；未保存 bridge audit、网络信息、同步状态机数据或可重复的环境矩阵。因此不能据此断言 MCP 调用了 `request_notebook_sync`、触发了云 API 或能够控制/等待远端同步完成。
- **决策**：仅作为 `reliability` / `environment` 观察保留。本轮不改生产代码、授权、Tool schema、Operation Runtime、manual-validation scenario、同步 contract 或用户文档；当前 local-only 边界与 `request_notebook_sync` 仅证明请求接受而非同步完成的契约保持不变。
- **后续条件**：只有当用户明确要求解释、抑制、等待或验证该现象时，才设计 content-free、local-only 的证据采集与最小变更；不得为了复现而添加隐式同步、联网检测、遥测或后台轮询。

### UT-007：所有层级变更的生产 read-back 不应逐 Page 读取正文

- **问题描述**：2026-08-17，用户在重挂载 Section 或 SectionGroup 等重型结构时观察到耗时很长。最初代码审阅确认，生产 `reparent_*` 在 mutation 前后都会对目标 Notebook 的每一个 Page 调用完整 XML 读取，并以 Page 内容摘要和内容对象映射完成 read-back；即使移动的是一个容器，该成本也包含不相关 Section 的 Page。Reparent 子范围已修复；本次扩展核验还发现 `_reorder_container` 仍在生产 read-back 前后对受影响容器子树的每一个 Page 调用 `PageService.xml(..., "all")` 并比较 digest。层级 mutation 的验证边界因此不一致，且容器 Reorder 的延迟仍会随正文规模增长。
- **问题分析**：`UpdateHierarchy` 的 production payload 只表达 typed hierarchy，不包含 Page 正文；生产成功的必要后置条件是稳定观察到对象身份、父级、完整子树/缩进与 sibling order，而非在调用延迟路径中对全 Notebook 或容器子树作一次正文保真验收。这一原理适用于现有及未来所有 typed hierarchy mutation，而不仅是 Reparent。Page 正文由 OneNote 管理，不能仅凭 API 分层断言绝不会受影响，因此逐 Page 比对不能被伪称为不再需要；应保留为 human-gated、disposable Notebook 上的真实兼容性证据，而不是每次生产调用的同步门限。
- **改进决策**：生产中的任何 typed hierarchy mutation（包括 Reparent、Page/Section Reorder 与 UT-004 后续交付的 Sort）只捕获有界、content-free hierarchy snapshot，并以稳定 bookend、typed ID、父级、完整子树/缩进、合法 sibling order 与 Page ID remap（如适用）完成对账。生产 `id_map` 对 Page 仅承诺 Page ID 映射；响应的 `verified` 不得声明 rich content 或 Page content 已验证。任何具名 manual-validation hierarchy scenario 继续在正向与恢复步骤前后读取其 scoped Page，并比较富内容语义、稳定内容摘要及内容对象，保留失败现场。不得新增隐式 Sync、close/reopen、raw XML 或名称选择。
- **验收标准**：生产 hierarchy mutation 的成功、execute-error reconciliation、not-applied 和 partial 路径均不得调用 `get_page_content` / `PageService.xml` 或同等逐 Page 正文读取；仍必须 fail closed 于 hierarchy ID、父级、scope、缩进、关系、direct-child 集合或顺序异常，并不得重放 mutation。确定性合同必须覆盖 Reparent、Page Reorder、Section Reorder 和 Sort 的零 Page XML read，以及 Page ID-only mapping（如适用）。manual-validation runner 的既有逐 Page before/after/restore comparator 不得删除或降级；新增或受影响的 hierarchy mutation scenario 仍须由用户在 fresh disposable Notebook 前台重新验证，方可关闭本项。
- **实现与验证状态**：Reparent 子范围保持既有 content-free hierarchy bookend、Page ID-only `id_map`、`49 passed` 聚焦证据与用户确认的四个真实场景。扩展范围现已完成：`MutationService._reorder_container` 删除 `_page_digests` 及所有 `PageService.xml` 调用，只以有界子树 signature、完整 direct-child 集合和 typed sibling order 收敛；响应删除 `page_content_unchanged` 声明并新增 `verification_scope.page_content="not_read"`。`tests/test_container_reorder.py` 覆盖成功、partial、indeterminate 的零 Page read，以及父级、兄弟集合、后代关系变化的 fail-closed。`docs/design/tool_contracts.md` 已把 Reparent/Page Reorder/Section Reorder 统一为 hierarchy-only production read-back；manual-validation 的逐 Page before/after/restore comparator 未删除或降级，纯合同包含在 `589 passed` 中。UT-004 的 `sort_children` 现已沿用同一 content-free hierarchy read-back，并由对应具名场景等待用户复测。
- **用户真实复测证据**：用户此前已明确确认 `reparent-section`、`reparent-page`、`reparent-page-with-level` 与 `reparent-section-group` 四个具名前台 scenario 通过。用户又于 2026-08-17 运行 `reorder-section`=`run-2026-08-17-21-13-29`；顶层与 scenario 均 `passed`，lifecycle `closed=true`，两个父级下的正向与恢复共 4 次 `reorder_section` 均 `ok=true`、`observed_outcome=applied`、`convergence.converged=true`，生产响应均声明 `verification_scope.page_content="not_read"`。Runner 对 7 个 scoped Page 保存的稳定 hash、canonical hash、内容对象投影与 reparent hash 在 before/after/restored 三个阶段完全一致，最终 `restored=true`。
- **完成判定**：生产 hierarchy-only read-back、确定性零正文读取合同、manual 逐 Page comparator、既有 Reparent 用户证据以及新增 Section Reorder human-gated 证据现已形成闭环，UT-007 转为 `completed`。Sort 的产品与真实复测状态仍归 UT-004；当前单环境结果不外推为跨版本保证。
- **范围边界**：本项降低的是所有层级变更在生产调用延迟路径中的验证范围，不改变各操作既有 gate、exact ID、confirmation、同 Notebook 限制、收敛、single-attempt/replay-never、partial/indeterminate fail-closed 或 manual validation 的正文保真门限；不将 manual evidence 外推为跨版本保证。它不改变正文 mutation 的内容验证合同。

### UT-008：`get_page_text` 不能表达 Page 富文本结构

- **问题描述**：2026-08-17，用户在读取既有 Page 时发现 `get_page_text` 默认返回 plain text，几乎不包含富文本结构。对需要理解链接、强调、字体/颜色、列表层级、表格单元格或其他 inline/block 语义的 Agent 而言，纯可见文字不足以可靠重建原有表达。
- **代码核验**：公开 `get_page_text(page_id, max_chars=60000)` 没有 `format` 或结构投影参数。`PageService.get_text` 调用 `text_from_page_xml(self.xml(page_id, "basic"))`；该 parser 遍历 `<T>` 元素，再由 `HTMLTextExtractor` 仅收集 `handle_data` 的字符，并只为 `br/p/div/li/tr/h1`–`h6` 插入换行。HTML tag、attribute、style、hyperlink target、list/table 边界与其他富文本语义不进入响应。当前 `get_page_content_objects` 是图片/附件等 typed 对象 metadata，不补足文本结构；公开契约也明确 Raw Page XML 不属于读取降级路线。
- **改进决策**：已接受让同一读取能力支持 `plain` 与 `rich` 两种模式，并在 2026-08-17 进一步确定默认使用 `rich`。`plain` 继续保留原字符预算和 `{text, chars}` 响应，供明确需要兼容投影的调用显式选择；不得直接暴露 raw Page XML 或绕过当前对象二进制预算。
- **实现与证据边界**：公开 schema 现为 `get_page_text(page_id, max_chars=60000, mode="rich"|"plain")`。默认 `rich` 返回 `{html, chars, mode="rich", format="sanitized_html_v1", truncated}`；显式 `plain` 返回兼容的 `{text, chars}`。投影由 `page/parser.py` 从 Page basic XML 构建受控 HTML，保留有效标题、强调、字体/颜色、允许 scheme 的链接、List/Tag 与 Table/Cell 结构，危险 tag/attribute/CSS/URL 被移除，Image/附件只留下不含 ID/binary 的位置 marker；输出经过 HTML-aware 截断，`max_chars` 不得超过进程上限。依赖 plain response 的 manual-validation fixture 已显式传入 `mode="plain"`，不再依赖公开默认值。`health_check.page_text_modes` 和 README/tool contract 已同步。`tests/test_page.py` 覆盖结构保留、消毒和 well-formed budget 截断；`tests/test_server.py` 固定 rich 默认响应、显式 plain 兼容、raw XML 不泄漏与未知 mode 在正文读取前拒绝。真实后端 test case 已补入三个既有 scenario：`copy-page` 覆盖省略 mode 的父/子 rich、显式 plain、有界 well-formed 截断与安全负合同；`reparent-page` 覆盖 before/ID-remap-after/restore rich signature；`copy-section` 覆盖源与同 Notebook/跨 Notebook 两个复制目标的父/子 rich signature。证据统一写入 content-free `page-text-projection.json`，MCP audit 对短 `html` 强制脱敏。
- **最新真实证据（2026-08-17 21:11–22:20）**：最新 9 个 run 中 7 个 `passed`、两次历史 `copy-page` 为 `failed_closed`，9 个 lifecycle 全部 `closed=true`。UT-008 的三项定向场景现均通过：`reparent-page`=`run-2026-08-17-21-48-22` 顶层/scenario 均 passed、restored=true，before、ID remap 后和 restore 后的 rich/safety/signature 全部匹配；`copy-section`=`run-2026-08-17-21-49-09` 顶层/scenario 均 passed、restored=true，源与同 Notebook、跨 Notebook 两个目标的六份 rich 投影和两个 parent/semantic-child signature comparison 全部通过；最终 `copy-page`=`run-2026-08-17-22-18-10` 顶层/scenario 均 passed、restored=true，父页默认 rich 为 1363 字符并含两个 MathML、formatting/table/image，语义子页含 List/Tag，explicit plain 精确保留 `{text, chars}` 业务字段，bounded default rich 从 1363 字符安全截断为 189/192 字符且 `truncated=true`，三份投影的 15 个 safety 布尔项全 true，`content_persisted=false`。六个 Copy case 均为 `copy_contract_satisfied=true`、`verified=true`、`lossless=true`、零 issue；共 9 个 target ID 全部由非永久 cleanup 对账，两个 disposable Notebook 均 `closed=true`。
- **失败根因与修复**：第一次失败源于 OneNote 把完整 Presentation MathML 放入 `<!--[if mathML]>…<![endif]-->` 条件注释，通用 `HTMLParser` 原先按普通 comment 丢弃整个公式。Rich projection 现在只识别这一完整包装，使用 XML parser 验证 canonical MathML namespace、allowlist 元素和 root-only `display="block"` 属性，再输出不带 prefix 的 canonical `<math xmlns="http://www.w3.org/1998/Math/MathML">`；第二次真实 run 已证明该路径有效。第二次失败不是公开 plain 契约变化，而是 manual-validation client 会在业务结果 `{text, chars}` 上固定展平 `ok/warnings/execution` 元数据，scenario 却要求顶层 key 精确等于两个业务字段。断言现只对剔除这三个已知 client metadata 后的业务 key 做精确比较，并继续拒绝任何未知业务字段；content-free evidence 分开记录业务 key 与 client metadata key。普通 comment、非法 MathML 和 raw XML/binary 边界不变，相关正负合同已有自动化覆盖。
- **完成判定**：production/schema、自动化正负合同、canonical 文档、三个既有 manual-validation scenario 以及用户前台真实复测已形成闭环，UT-008 转为 `completed`。本条不量化特定 Notebook 的信息损失比例，也不把当前单环境证据外推为跨版本保证。

### UT-009：重建式 Move 的 `verify_copy` 等价判断常因 COM 重写结构失败

- **观察**：2026-08-17，Grok Build 在已开启 Writes、Copy、Deletes 的配置下，把既有 Page 用 `move_page` 重建式搬到 disposable 目标 Notebook。调用方能独立发出 `move_page`；多数结果是 `ok=true` 但 `partial_failure` / `outcome=copy_only`，`failed_step=verify_copy`，`source_deleted=false`。目标区已出现同名副本，源页仍在原 Section。少数页完整 `moved`。本文不记录 Notebook/Section/Page 名称、标题、正文或真实 COM ID。
- **校验位置**：生产路径在 `write_page_content` 之后、删源之前，用 shipped `page_equivalence(transform_page_for_copy(源 XML), 读回目标 XML)`。默认档 `strict_canonical` 要求四项全过：`canonical_xml`（忽略 objectID、作者/时间、选中态和根 ID/name/pageLevel 后的规范化树哈希）、`visible_text`、`content_objects`（类型计数）、`binary_sha256`（图/附件解码字节）。仅当页的能力集是 Outline/RichText/List/Tag 的子集时才降到 `semantic_list_tag`（此时 `canonical_xml` 可以不过）。含 Table 或 Image 的页留在严格档。`verify_copy` 还要求拓扑收敛；任一页等价失败即 fail closed，不删源。另有一类：copy 读回已过，但删源前源范围变化，同样停在 `copy_only`。
- **典型失败形态**（对仍存活的三对源/副本，只读 `GetPageContent` 后走同一套 shipped 函数复现；不写入新 mutation）：
  1. **Title 下多余 `T` 被合并**：提交的 `Title/OE` 有两个 `T` 子节点，COM 读回只剩一个。`canonical_xml` 与 `visible_text` 失败（可见字数差 2），`content_objects` 与 `binary_sha256` 通过。页含 Image，走严格档。
  2. **根上少一块 Outline**：提交的 Page 根有两块 Outline，读回只剩一块。可见字完全一致，`canonical_xml` 与 `content_objects`（Outline 计数 2→1）失败，`binary_sha256` 通过。页含 List+Table，不能进 `semantic_list_tag`。
  3. **表格单元格 OE 被压扁**：可见字与对象计数一致，仅 `canonical_xml` 失败。首次树差在某一 `Table/Row/Cell/OEChildren/OE` 的子节点数 2→1。页含 List+Table，同样走严格档。
- **问题分析**：这不是 Agent 选错工具或未给 confirmation。COM `UpdatePageContent` 会改写 Title 分段、空/重复 Outline 和表格单元格内部结构；严格 XML 把这些当成不等价，于是重建式 Move 在「副本已在、源未动」处停住。`binary_sha256` 常通过，说明图/附件未丢。既有 `tests/test_copying.py` 已固定：纯 List/Tag 页允许 `canonical_xml=false` 仍等价；带 Table/Image 则必须四项全过。工作日志类页面多数带 Table 或 Image，因此大量落在严格档。
- **改进决策**：接受按内容能力组合的语义验证，不因页面含 Table/Image 就直接切换为整页严格 XML 比对。标题有效值、富文本/List/Tag、表格行列与单元格语义、二进制对象哈希及非空 Outline 分项验证；仅允许标题文本节点合并、空 Outline 消除、表格单元格 OE 扁平化等已知 COM 规范化。未知结构或语义投影不完整时，仍回退严格比对并 fail closed。
- **实现边界**：只调整 Copy read-back 的 Page 等价投影，不调整删源门限；`copy_only` 仍不得解释或重放为成功 Move。内容丢失负向用例、content-free 验证摘要和具名 manual-validation 静态闭包已补齐，用户前台的 verified-Copy-before-delete 复测证据见下。

- **实现与验证状态**：`page/copying.py` 新增 `semantic_content_v1`，只对 capability 完整且属于 `Outline/RichText/List/Tag/Table/Image` 的含 Table/Image Page 启用；MathML、DisplayEquation、纯 List/Tag、InkDrawing、UIShape 与其他类型保持既有独立 tier。投影把多个 Title `T` 合并为有效标题；忽略只含空结构的 Outline；在 Table Cell 内按有效富文本 run、List/Tag 与嵌套表格语义比较，允许 OE 分段压平；同时精确比较非 Outline 对象类型计数与解码 binary SHA-256。source/target 投影任一不完整时只允许 strict canonical fallback；未知结构、标题变化、样式/链接变化、List/Tag 变化、表格行列/单元格变化、非空 Outline 丢失和 binary 变化均 fail closed。`page_equivalence` 只返回 content-free checks/comparison 摘要。自动化已覆盖三类观察到的 COM 规范化正向样本，以及标题、富文本样式、非空 Outline、binary 丢失负向样本。生产 Move 的 verified-Copy-before-delete、source revalidation、`copy_only`、single-attempt 和 non-permanent delete 门限未放宽。
- **用户真实复测与完成判定**：用户于 2026-08-17 完成 `copy-page`=`run-2026-08-17-21-03-49`、`copy-section`=`21-06-31`、`copy-section-group`=`21-08-07`、`copy-notebook`=`21-09-57`、`move-page`=`21-11-01`、`move-section`=`21-12-14`、`move-section-group`=`21-12-49`（后六项沿用 `run-2026-08-17-` 前缀）。四个 Copy run 共 11 份 Copy report，全部 `copy_contract_satisfied=true`、`verified=true`、`lossless=true`、issues 为空；真实 verification tier 包含 `semantic_content_v1`、`semantic_list_tag`、`semantic_display_equation` 与 `strict_canonical`，其中 Section、SectionGroup、Notebook Copy 均实际进入 `semantic_content_v1`。`move-page` 的 root-only/subtree 两个 case 均先 `copy_verified=true` 再 `source_deleted_nonpermanently=true`；两个容器 Move 的 `move-result` 也均为 `outcome="moved"`、Copy report verified/lossless、源根非永久删除。7 个 run 均顶层/scenario `passed` 且 lifecycle `closed=true`，没有 `copy_only`、partial 或 issue。正负自动化合同与真实 Copy-before-delete 闭环均已满足，UT-009 转为 `completed`；结果不外推为所有未知 Page capability 或跨版本保证。

### UT-010：统一 Page 子页范围，并补齐 Delete/Reorder 与 batch 后代保护

- **问题描述**：2026-08-18，用户询问删除或移动一个带缩进子页的父 Page 时的行为。当前公开 `delete_page` 只接收父页的 exact ID、标题、Section 和可选 modified confirmation；生产路径直接对该 ID 调用 `DeleteHierarchy(permanently=false)`，并只确认该 ID 已从 active hierarchy 消失或被标记为回收站。当前 `reorder_page` 也只抽出并重插目标 Page，可改变它在同一 Section 中的顺序和 `page_level`，但没有声明或验证原缩进后代是随父页移动还是留在原位并提升。两者都不具备 Page Move/Reparent 已有的显式单页/完整子树选择和排除后代保护。
- **问题分析**：Page 缩进在工具模型中是显式的父子树；只操作父页而把后代处置交给 native side effect 或扁平序列重排，会使调用者无法安全表达“只操作父页、保留子页”还是“操作完整树”。现有 `copy_page`、`move_page`、`reparent_page` 又使用字符串 `page_scope="page_only"|"indentation_subtree"` 表达二元选择，增加 schema 和 Agent 选择成本。Page Reparent/Delete 还支持 `items[1..20]`：若每项执行前才独立判断是否提升，前一项造成的层级变化可能污染后一项 scope、confirmation 和提升判断，因此安全边界必须是整个 batch，而不是单项 Service 调用的简单循环。
- **统一公开参数决策**：删除所有公开 `page_scope` 参数，统一改为 `include_subpages: bool = false`，不保留旧字段 alias。该参数用于 `copy_page`、`move_page`、`reparent_page`、`delete_page` 与 `reorder_page`；Page Reparent/Delete 的 batch item 也各自携带同名布尔字段。`false` 表示只选择目标 Page，`true` 表示选择目标 Page 及其完整缩进子树。`sort_children` 不增加该参数：它继续把每个直属 Page 及其完整缩进后代视为不可拆分 block。
- **单项保护决策**：`delete_page(include_subpages=false)`、`reparent_page(include_subpages=false)`、`move_page(include_subpages=false)` 和 `reorder_page(include_subpages=false)` 必须先从同一 live Section snapshot 找出排除后代，并在 principal 操作前将它们整体提升一级；提升后精确证明后代 ID、Section、相对顺序、相对父子关系、合法缩进与稳定正文保持。任一提升或对账不完整时，不得开始父页的 Delete/Reparent/Move source-delete/Reorder。`include_subpages=true` 时，Reparent/Move/Reorder 将完整缩进子树作为不可拆分 block，保持块内 ID（允许既有 Reparent ID-remap 合同）、相对顺序与相对层级；Delete 则冻结完整 scoped ID 列表并按叶到根逐项非永久删除。
- **batch 整体规划决策**：Page Reparent/Delete batch 必须从一个有界、稳定、mutation 前的 hierarchy snapshot 计算每项 effective scope、全体 selected-ID union、全体需要保留并提升的 excluded descendants 以及每个受影响 Section 的最终预期拓扑。重复目标、祖先/后代目标、任意 effective-scope 重叠、跨 Notebook/Section 非法范围、回收站对象或预算超限必须在零 mutation 时拒绝。所有必要提升作为 batch-wide 前置阶段按 Section 一次性执行并全部收敛后，才允许按输入顺序开始 principal Reparent/Delete；不得让前一项临时提升改变后一项的 scope。提升阶段 partial/indeterminate 时不得开始任何 principal item，必须返回已改变/未改变的 Section 与后代 ID、人工恢复指引、`rollback_attempted=false` 和 `mutation_replayed=false`。
- **验收标准—Delete**：单项与 batch 都继续要求 Deletes、exact root confirmation、受控资源/Page budget 和无永久删除参数。`include_subpages=true` 必须逐项验证所有 selected ID 不再 active（回收站元数据仅作诊断）；首个失败或不确定结果返回 `partial_failure`、已删除/失败/未尝试 ID 和人工恢复指引，绝不 rollback、replay 或盲重试。`include_subpages=false` 必须在删除前后证明排除后代仍 active 且保护不变量全部成立。
- **验收标准—Reorder**：`reorder_page(include_subpages=false)` 只移动目标 Page，排除后代留在原位置并安全提升；`include_subpages=true` 将目标 Page 和完整缩进子树作为一个连续 block 移动，目标 root 的 `page_level` 由请求决定，所有后代相对层级与块内顺序保持。`after_page_id` 不得位于 selected block 内，目标插入位置、完整 Section Page ID 集合、未选 Page 拓扑和最终 block 连续性都必须稳定收敛；任何不确定状态 fail closed 且不得重放。
- **验收标准—参数迁移与既有能力**：`copy_page`、`move_page`、`reparent_page` 的现有两种 scope 正负合同全部迁移到 `include_subpages=false|true`，并增加旧 `page_scope` 在 backend call 前以 `validation_error` 拒绝的合同。Page Reparent/Delete batch schema、静态 preflight、partial envelope 和最终 hierarchy 摘要必须覆盖混合布尔值请求；既有 Copy fidelity、Move verified-Copy-before-delete、Reparent exact-ID/read-back、Delete 非永久化和 Sort Page-block 保护不得降级。
- **实现与自动化证据**：公开 Tool schema、description、Operation Registry、Service、响应、README/design/dev canonical 文档和所有 manual-validation 调用均已迁移；旧 `page_scope` 不保留 alias。确定性自动化覆盖五个受影响工具的 bool schema/default、旧字段拒绝、root-only 提升成功/失败、完整子树、Reorder block、Delete leaf-to-root、整批 mixed union/overlap/一次性提升、提升失败时零主操作、子树 Delete 局部进度、最终整批 hierarchy、partial failure 与零 replay。`delete` fixture v5 将两个无重叠叶子 Page batch 与混合布尔树范围 batch 分开执行，后者验证受保护子页和完整子树；`reorder-page` fixture v2 验证两种范围、内容哈希与恢复；Copy/Move/Reparent 场景全部改用布尔参数。真实执行只能由用户前台显式发起，因此本项保持 `validating`，等待 fresh disposable human-gated 复测。

## 本轮统一自动化证据

- `tests/test_policy.py + tests/test_operation_runtime.py + tests/test_project_mcp_configs.py`：`184 passed`；冻结当前 53 Tool 的精确授权/platform-preflight 矩阵、48 条缺 gate 拒绝、同名 batch 授权复用和旧 Copy 环境变量非 alias。
- `tests/manual_validation/tests`：`606 passed in 16.50s`；冻结 ScenarioPolicy、fixture/tool policy 闭包、具名 batch/Sort/Copy/Move/Reorder 场景、Delete/Reorder 的 `include_subpages=false|true` 计划、Create 三类 cleanup 闭包、Create/Sort typed preflight 拒绝的 read-only bridge audit 与 unchanged snapshot、Page Rename title-excluded body/restore 证据、SectionGroup source anchors、Reparent 整批最终 hierarchy 摘要、`get_page_text` content-free 投影证据和 dry-run catalog。该结果是纯合同，不是 OneNote 真实后端证据。
- 完整 `.venv\Scripts\python.exe -m pytest -q`：最新结果为 `1340 passed in 67.88s`。
- `.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run`：18 个 `all` 场景全部通过，`18 passed, 0 failed`；命令包含 `--dry-run`，未启动 MCP、未访问 OneNote、未执行 mutation。
- Agent 未执行任何真实 `run.py <scenario>`、`run.py all` 或真实 maintenance action。用于闭合 UT-005、UT-007、UT-008、UT-009 的既有 human-gated 证据仍成立。用户于 2026-08-18 01:22–01:25 完成的最新 3 个 durable run 全部 `passed/restored/closed`，已分别闭合前一批 Create 场景清理 allowlist、Rename 恢复快照比较和 SectionGroup Reparent 恢复阶段的 fail-closed 路径；旧 run 及其 durable 现场仍作为历史诊断证据保留。

## 单项记录最低要求

- 用户任务和预期结果；
- 客户端、测试日期、代码 commit/version 与 OneNote 前置状态；
- 开启的 7 类公开 gate 投影；
- content-free 的实际工具选择、调用次数和失败/恢复阶段；
- 预期与实际差异，以及问题分类；
- 接受、拒绝或延期的理由；
- description/schema/config/code 的具体变更位置；
- 聚焦测试、完整测试及用户真实复测证据；
- 是否影响当前 53 项工具面、授权矩阵、公开合同或已知限制。

## 范围边界

- 不因为模拟用户而绕过 exact ID、confirmation、预算、授权、收敛、对账或 partial failure 门限；
- 不以通用 action、raw XML、任意路径或隐藏入口快速填补体验缺口；
- 不引入 Graph、Azure、OAuth、遥测、远程内容处理或直接编辑 `.one` 文件；
- 不把单个客户端的模型偏好直接当作服务端事实；应先用可复现调用轨迹区分 description、client 和实现问题；
- 不为每个观察创建新 TODO。只有用户明确改变本规则，或本 TODO 收尾时决定将长期延期项拆出，才允许建立独立跟踪项。

## 完成定义

- [ ] 用户认可的代表性任务矩阵已覆盖主要只读、写入、组织、Copy/Move、本地文件、UI 与 Notebook lifecycle 工作流；
- [ ] 计划纳入本轮的客户端均完成至少一轮真实用户式使用，或记录无法执行的明确原因；
- [ ] 所有 `accepted` 项均已实施并验证，所有 `deferred` / `rejected` 项均有用户可审阅理由；
- [ ] 每项改进都在本文留下完整观察—决策—实现—验证链，没有散落的独立体验台账；
- [ ] 公开合同变化已同步 canonical 文档和自动化测试，真实 OneNote 结论均有用户确认；
- [ ] 用户审阅最终台账并明确批准结束用户测试优化阶段。

## 关联

- [TODO 034：用户测试前 MCP 工具发布面收敛](034_pre_user_testing_tool_surface_convergence.md)
- [公开 Tool 契约](../design/tool_contracts.md)
- [Manual Validation Runner](../../tests/manual_validation/README.md)
- [项目 README](../../README.md)
