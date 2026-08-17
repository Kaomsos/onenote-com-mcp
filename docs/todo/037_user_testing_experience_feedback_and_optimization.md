# 037：用户测试期工具调用与实现体验优化

> ID：037
> 状态：进行中
> 优先级：P0
> 类型：User Testing / Developer Dogfooding / Tool UX / 反馈驱动优化
> 更新日期：2026-08-17

## 当前状态

[TODO 034](034_pre_user_testing_tool_surface_convergence.md) 已完成用户测试准入。项目现进入开发者模拟真实用户行为的持续使用阶段：开发者通过受支持的 MCP 客户端，从用户任务出发发现、选择并调用工具，再根据实际体验提出和验证优化。UT-004 已确认整理 Notebook 时存在批量 Reparent/Delete/Rename/Create 与单父节点 Sort 的实际需求；UT-005 已接受新增 Create 权限并将 Copy/Move 收敛到创建、写入与删除权限的方向；UT-006 记录 online-backed Notebook 创建 SectionGroup 时的同步观察；UT-007 已完成 Reparent 子范围的 production hierarchy-only read-back 和用户复测，并已扩展为所有层级变更均不在生产路径逐 Page 比对的方向；UT-008 已接受让 `get_page_text` 同时支持 `plain` 与 `rich` 两种读取模式的方向。UT-004、UT-005、UT-007、UT-008、UT-009 尚未整体实现，UT-006 未改变实现。UT-009 已接受按内容能力组合语义验证的方向。

本 TODO 是用户测试期的**唯一改进台账**。工具选择、调用链、描述、schema、权限提示、响应、错误恢复或实现行为方面的观察与改进，暂时全部记录在本文，不为单项体验问题另建 TODO、Lesson、Overview、专题设计稿或其他独立跟踪文档。

若改进改变当前公开合同、运行流程或实现，仍必须同步修改对应代码、自动化测试以及既有 canonical README/design/dev 文档；这些必要同步不是新的反馈台账，变更位置应回链到本文对应记录。可复用 Lesson 或独立长期规划是否拆出，只在本 TODO 收尾时统一决定。

## 目标

- 用 Claude Code、Codex、Cursor、Grok Build 等受支持客户端，以用户任务而不是源码模块为起点使用当前 52 个工具；
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
| UT-004 | 2026-08-17 | 用户 / 整理既有 Notebook 的层级与顺序 | 批量 Reparent：Writes + Organize；批量 Delete：Deletes；批量 Rename：Writes；批量 Create：现状 Writes，落地须跟随 UT-005 Create/Write 矩阵；Sort（拟）：沿用相应 Reorder 的 Writes gate | 现有 typed 工具只支持单对象 Reparent/Delete/Rename/Create 和基于 `after_*_id` 的精确 Reorder；批量调整需要 Agent 逐项编排，且没有按常用 hierarchy 字段规范单个父节点完整直接子序列的能力；Copy/Move 是多阶段组合操作，不纳入本批量范围；`workflow` / `capability` / `reliability` | 已接受：新增同 Notebook、exact-ID、bounded 的批量 Reparent/Delete/Rename/Create；不做批量 Reorder、Copy 或 Move。另评估独立 Sort：以受控 `key`（名称、创建时间或修改时间）和升降序排列一个父节点下的完整直接子序列 | 用户确认存在实际整理需求和调整方向；尚无实现、自动化或修复后真实复测 | accepted |
| UT-005 | 2026-08-17 | 用户 / 授权模型审阅 | 现状：Copy = Writes + Copy；目标：Copy = Create + Writes，Move = Create + Writes + Deletes | 独立 Copy gate 与创建/写入语义不对应；Move 本质为经验证 Copy 后的可恢复源删除，现有 Copy 依赖使其授权模型不够一致；`authorization` / `workflow` / `capability` | 已接受：新增独立 Create 权限，移除独立 Copy 授权，按 Create/Write/Delete 的实际副作用授权 | 用户确认改进方向；尚无实现、自动化或变更后的前台复测 | accepted |
| UT-006 | 2026-08-17 | 用户 / 在 online-backed Notebook 中创建 SectionGroup | Writes | 可见 OneNote 同步在 `create_section_group` 后触发；`reliability` | 仅记录环境观察，不修改同步行为或契约 | 用户前台观察；尚未记录 bridge audit 或独立复现矩阵 | observed |
| UT-007 | 2026-08-17 | 用户 / 执行层级变更 | 依目标操作的现有 gate | 生产 Reparent 已移除逐 Page XML 比对，但容器 Reorder 仍在生产 read-back 中读取子树 Page XML；层级变更的验证边界不一致且会随无关正文增长；`reliability` / `performance` | 已接受：所有 typed hierarchy mutation 的生产 read-back 仅验证稳定 hierarchy，不逐 Page 比对；逐 Page 正文/内容对象比较只保留在具名 manual validation | Reparent 子范围：确定性合同 `49 passed`、其余完整基线 `1200 passed, 1 deselected`，且用户确认四个前台场景通过；容器 Reorder/Sort 范围尚未实现或复测 | accepted |
| UT-008 | 2026-08-17 | 用户 / 读取既有 Page 的富文本内容 | 无 gate | `get_page_text` 只返回可见 plain text；粗体、链接、字体/颜色、列表、表格和 HTML 结构不进入响应；`capability` / `response` | 已接受：同一读取能力支持 `plain` 与 `rich` 两种模式；默认保持 plain，rich 投影 schema 待设计 | 用户确认改进方向；当前代码审阅仍证明仅有 plain 投影，尚无实现、自动化或真实复测 | accepted |
| UT-009 | 2026-08-17 | Grok Build / 将已有 Page 重建式 Move 到 disposable 目标 Notebook | Writes + Copy + Deletes | 目标区常已建出副本，但 `verify_copy` 的 `page_equivalence` 失败，源删除被挡住，结果为 `partial_failure` / `copy_only`；`reliability` / `response` | 已接受按内容能力组合的语义验证：分项验证标题、富文本/List/Tag、表格、二进制对象与非空 Outline；只忽略已知 COM 规范化，未知或投影不完整时继续严格 fail closed | 三对仍存活的源/副本经 shipped `page_equivalence` 复现；既有 List/Tag 单测证明严格档与语义档差异。方案尚未实现、无变更后复测 | accepted |

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

- **问题描述**：2026-08-17，用户在整理既有 Notebook 的层级和顺序时确认存在五类实际需求：把多个对象挂到同一目标、对多个不再需要的对象执行可恢复删除、为多个已确认对象更名、在同一已确认父级下创建多个同类对象，以及按常用字段将一个父节点下的直接子节点整体升序或降序排列。当前公开的 `reparent_*`、`delete_*`、`rename_*`、`create_section`、`create_section_group`、`create_page`、`reorder_page` 和 `reorder_section` 都是单对象操作；Reorder 还要求调用方用 `after_*_id` 表达单次相对位置，调用方只能自行逐项编排。
- **问题分析**：单对象 typed Reparent/Delete/Rename/Create 契约及其底层收敛、对账、目标位置回传和 Operation Runtime 已为可靠批处理提供基础，但未提供一个能表达“多对象到单目标”“多个已确认对象可恢复删除”“显式 ID 到新名称映射”或“单个父级下创建多个同类对象”、统一预检并返回 partial outcome 的边界。Delete 的风险高于 Reparent：同一请求不得同时包含某容器及其后代，也不得把 partial 结果伪装成全量成功或以宽泛恢复掩盖已进入回收站的对象。Rename 不应以名称搜索或通用替换规则选择目标，且首版需拒绝同一父级的名称交换/循环，避免引入临时名 mutation。Create 在成功前不存在对象 ID，必须以每项输入序号和创建后的精确分配 ID 对账；它的授权模型还依赖 UT-005 对 Create/Write 的最终矩阵。Copy/Move 不属于可直接批量化的单一 hierarchy mutation：Copy 本身包含经验证的目标创建、内容写入与 fidelity/read-back，Move 还必须在 Copy 成功后执行可恢复源删除。若批量化，会把每项的新旧 ID、内容保真、阶段依赖与 source-delete partial recovery 交叉放大，难以保持可解释、fail-closed 的控制面，故本轮不纳入。把多次 `after_*_id` 操作组合成批量 Reorder 也会把最终顺序、依赖、Page 缩进与中间状态耦合在一起；按单父节点的完整直接子序列进行 deterministic Sort 更符合“整理”的意图。Sort 不应接受任意属性路径或名称选择目标，而应只允许当前 hierarchy 对各类型共同投影的少量稳定键。Page 不能逐项打散排序：应把一级 Page 与其缩进子页视为同一排序块，保留块内顺序和层级。
- **改进决策**：已接受五项彼此独立的方向。其一是仅限同一 Notebook、exact-ID、bounded 的批量 Reparent：请求可包含多个同类待重挂对象，但挂载目标只能是一个；具体公开 tool 拆分、最大数量和 Page scope schema 待设计。服务端须在任何 COM mutation 前完成 notebook containment、权限、身份、确认字段、循环、重复、祖先/后代或 Page scope 重叠等 fail-closed 预检。其二是仅限同一 Notebook、exact-ID、bounded 的批量 Delete：只覆盖 Page、Section 与 SectionGroup 的现有**非永久**、可恢复删除；每项仍须确认身份/父级/修改时间，且须在全部预检通过后才开始删除。请求不得混合容器与其任何后代、重复 ID、回收站对象、Notebook、PageContentObject 或永久删除；缺少 `Deletes` 时零 backend call 拒绝。其三是同一 Notebook、同类型、exact-ID、bounded 的批量 Rename：每项包含精确 ID、现有确认字段和显式新名称；不得由名称、模式或表达式选择目标，首版拒绝名称交换/循环。其四是同一已确认父级、同类型、bounded 的批量 Create：只复用现有 SectionGroup、Section 或 Page 的 typed 创建语义，每项携带名称/标题及该类型当前支持的安全参数；服务端预检父级、类型、输入重复、名称可用性和预算，并按输入序号返回新分配的精确 ID。现状 Create 仍由 `Writes` 保护；实现必须等待或同时落地 UT-005 对 Create 与 Writes 的逐 Tool 授权矩阵，不能以批量入口绕过该迁移。前四种 batch 都不是事务：执行按受控顺序逐项复用既有 operation runtime、convergence 与 reconciliation；失败或不确定时必须停止后续项、保留逐项 applied/failed/not-attempted 与可行动恢复信息，不得宽泛 rollback、名称重新解析或盲目重试。不实现批量 Copy 或 Move：它们是多阶段组合操作，Copy 包含目标创建、内容写入与 fidelity/read-back，Move 再依赖已验证 Copy 后的可恢复源删除；本轮不以 batch 方式承担其交叉 ID 映射、步骤依赖和 partial recovery。其五是不加入批量 Reorder，改为独立 Sort：一次只处理一个父节点的完整 active 直接子序列，参数包含 `key` 与排序方向。`key` 首版限定为 `name`（显示名称）、`created`（创建时间）或 `modified`（修改时间），不接受任意字段、路径或表达式；同键值的子项保留当前相对顺序。若所选时间键在任一参与项缺失或不可解析，整次调用 fail closed。Notebook/SectionGroup 的实际直接容器子序列，以及 Section 的一级 Page block，均须分别保持其合法 hierarchy 关系。
- **验收标准**：批量 Reparent 的公开接口与响应明确最大对象数、每个对象的 exact-ID/precondition、唯一目标、逐项状态及最终 live hierarchy 摘要；缺少 Writes + Organize 时在 backend 调用前拒绝。批量 Delete 的接口与响应明确最大对象数、每个对象的 exact-ID/precondition、非永久删除、逐项回收站结果和 partial recovery 指引；缺少 Deletes 时在 backend 调用前拒绝，任何预检失败均不得删除任一项。批量 Rename 的接口与响应明确最大对象数、每项 exact-ID/confirmation/new name、逐项旧新名称和结果；缺少 Writes 时在 backend 调用前拒绝。批量 Create 的接口与响应明确唯一 confirmed parent、资源类型、最大对象数、每项输入序号和新分配 ID；授权矩阵必须与 UT-005 同步，缺少该 Tool 所需的 Create/Writes 任一 gate 时在 backend 调用前拒绝。Sort 的公开接口明确唯一 `parent_id`、受控 `key` 枚举、排序方向、当前完整子序列的 optimistic confirmation 与同键值稳定规则；仅在当前子节点集合与确认一致、每项均有可比较的所选键时执行，并以最终完整顺序及 Page block 层级完成收敛验证。自动化覆盖同 Notebook/同类多对象到单目标的 Reparent、Page `id_map`、冲突/循环/重叠/过大请求拒绝、失败后的停止与 partial outcome；批量 Delete 的 Page/Section/SectionGroup、gate 拒绝、确认/回收站/重复/祖先-后代/跨 Notebook/永久删除拒绝与 partial outcome；批量 Rename 的 Page/Section/SectionGroup、确认/名称规范化/重复或交换/循环拒绝与 partial outcome；批量 Create 的三种类型、父级/名称/预算/授权/分配 ID/partial outcome；以及 Sort 的每种键/升降序、重复键稳定性、缺失或不可解析时间拒绝、容器直接子序列、Page block 与并发确认不一致拒绝。所有生产 hierarchy mutation 的 read-back 依 UT-007 保持 content-free hierarchy 验证；对应具名 manual-validation scenario 仍逐 Page 比较内容。每个新增 mutation-policy 真实执行路径均需具名 manual-validation scenario；用户使用 fresh disposable Notebook 前台确认成功、拒绝和 partial-failure 证据后才能关闭本项。
- **实现与验证状态**：本轮记录了用户确认的批量 Reparent/Delete/Rename/Create 与独立 Sort 方向；尚未定义公开 tool 名称/schema，未修改生产代码、自动化或 manual-validation scenario，也不存在修复后真实证据。批量 Create 的最终授权要求受 UT-005 依赖约束。实现时如新增公开 tool，必须同步 Registry、policy/authorization matrix、tool contracts、README、Operation Runtime 文档和 TODO 037。
- **范围边界**：不实现批量 Reorder、Copy 或 Move；不得引入 raw XML、按名称或其他 `key` 选择 mutation 目标、无界 hierarchy scan、跨 Notebook batch、批量 Notebook/PageContentObject/永久 Delete、通用 Rename pattern/template、自动恢复或宽泛 rollback；不放宽 Writes、Organize、Deletes、UT-005 确定后的 Create/Write 组合、confirmation、收敛、对账或现有 default fail-closed 门限。Sort 的 `key` 只决定既有精确 ID 子序列的顺序，不能用于选择 mutation 目标。

### UT-005：新增 Create 权限，按创建、写入与删除收敛 Copy/Move 授权

- **问题描述**：2026-08-17，用户在审阅授权体系时指出独立的 Copy 类别与其实际副作用不一致。当前 `copy_*` 同时要求 Writes 和 `LOCAL_ONENOTE_ENABLE_COPY`；Move 则在此基础上再要求 Deletes。项目的公开权限中目前没有独立的 Create gate，Create 与写入均由 Writes 保护；但 Copy 实际是在已验证的目标中创建并写入内容，Move 则是在该 Copy 成功后执行可恢复的源删除。
- **问题分析**：额外 Copy gate 不对应独立的副作用类别，反而使调用方为 Copy/Move 申请一项不能从操作结果解释的权限。应将“创建对象”从 Writes 中独立出来：Copy 的风险模型是“创建 + 写入”，Move 的风险模型是“创建 + 写入 + 删除”。这不是放宽权限：新增 Create、现有 Writes 和 Deletes 都继续默认关闭；Copy 缺少 Create 或 Writes 时必须零 backend call 地拒绝，Move 缺少其中任一权限时也必须在 backend 调用前拒绝。
- **改进决策**：新增独立的 Create authorization category，并移除独立 Copy authorization category 及其环境变量。`copy_*` 固定要求 Create + Writes；`move_page`、`move_section`、`move_section_group` 固定要求 Create + Writes + Deletes，不再要求 Copy。所有现有 `create_*` Tool 也必须按其是否 materialize 新对象及是否写入初始内容，经过独立矩阵审查后绑定 Create 与必要的 Writes；不得仅因名称包含 create 而作隐式授权推断。Copy/Move 的 typed ID、confirmation、copy fidelity、source-delete、partial failure、convergence、reconciliation 与 default fail-closed 合同均不因授权收敛而放宽。
- **验收标准**：Registry、policy、health capability、authorization matrix、tool descriptions、README、客户端示例和 design 文档均声明新增 Create 类别与更新后的权限组合；`LOCAL_ONENOTE_ENABLE_COPY` 不再作为生产授权开关或静默兼容别名。所有 Copy Tool 在 Create/Writes 任一为 false 时零 backend call 拒绝，二者为 true 时才可进入既有 Copy 安全门；所有 Move Tool 在 Create/Writes/Deletes 任一为 false 时零 backend call 拒绝，三者为 true 时才可进入既有 Copy/Move 流程。纯合同覆盖所有受影响 Tool 的允许/拒绝矩阵、Create Tool 的精确绑定及原有 fidelity/partial failure 不变量；每条受 mutation policy 保护的真实执行路径仍由既有具名 manual-validation scenario 覆盖，用户前台复测后才可关闭本项。
- **实现与验证状态**：本轮只记录用户确认的授权收敛方向；尚未命名新的环境变量，未修改 `MutationPolicy`、Registry、公开 Tool 契约、客户端配置、测试或 manual-validation 静态 policy，也不存在变更后的真实证据。实施时必须审查所有 `copy_enabled`/`require_copy` consumer、当前 Create Tool 的 Writes 绑定，以及整个公开授权矩阵，并同步更新 TODO 037 的证据链。
- **范围边界**：不新增默认授权、不把 Copy/Move 变为纯 read、不允许绕过 Create/Writes/Deletes、不引入通用 mutation、raw XML、名称选择或跨 Notebook Reparent；复制与重建式 Move 的既有内容保真、最小权限、可恢复删除和 fail-closed 边界保持有效。

### UT-006：online-backed Notebook 创建 SectionGroup 后出现 OneNote 同步

- **观察**：2026-08-17，用户在 OneNote Desktop 已打开的 online-backed Notebook 中调用 `create_section_group` 后，观察到 OneNote 的同步行为被触发。
- **证据边界**：这是用户前台对 OneNote UI 行为的观察；未保存 bridge audit、网络信息、同步状态机数据或可重复的环境矩阵。因此不能据此断言 MCP 调用了 `request_notebook_sync`、触发了云 API 或能够控制/等待远端同步完成。
- **决策**：仅作为 `reliability` / `environment` 观察保留。本轮不改生产代码、授权、Tool schema、Operation Runtime、manual-validation scenario、同步 contract 或用户文档；当前 local-only 边界与 `request_notebook_sync` 仅证明请求接受而非同步完成的契约保持不变。
- **后续条件**：只有当用户明确要求解释、抑制、等待或验证该现象时，才设计 content-free、local-only 的证据采集与最小变更；不得为了复现而添加隐式同步、联网检测、遥测或后台轮询。

### UT-007：所有层级变更的生产 read-back 不应逐 Page 读取正文

- **问题描述**：2026-08-17，用户在重挂载 Section 或 SectionGroup 等重型结构时观察到耗时很长。最初代码审阅确认，生产 `reparent_*` 在 mutation 前后都会对目标 Notebook 的每一个 Page 调用完整 XML 读取，并以 Page 内容摘要和内容对象映射完成 read-back；即使移动的是一个容器，该成本也包含不相关 Section 的 Page。Reparent 子范围已修复；本次扩展核验还发现 `_reorder_container` 仍在生产 read-back 前后对受影响容器子树的每一个 Page 调用 `PageService.xml(..., "all")` 并比较 digest。层级 mutation 的验证边界因此不一致，且容器 Reorder 的延迟仍会随正文规模增长。
- **问题分析**：`UpdateHierarchy` 的 production payload 只表达 typed hierarchy，不包含 Page 正文；生产成功的必要后置条件是稳定观察到对象身份、父级、完整子树/缩进与 sibling order，而非在调用延迟路径中对全 Notebook 或容器子树作一次正文保真验收。这一原理适用于现有及未来所有 typed hierarchy mutation，而不仅是 Reparent。Page 正文由 OneNote 管理，不能仅凭 API 分层断言绝不会受影响，因此逐 Page 比对不能被伪称为不再需要；应保留为 human-gated、disposable Notebook 上的真实兼容性证据，而不是每次生产调用的同步门限。
- **改进决策**：生产中的任何 typed hierarchy mutation（包括 Reparent、Page/Section Reorder，以及未来 Sort）只捕获有界、content-free hierarchy snapshot，并以稳定 bookend、typed ID、父级、完整子树/缩进、合法 sibling order 与 Page ID remap（如适用）完成对账。生产 `id_map` 对 Page 仅承诺 Page ID 映射；响应的 `verified` 不得声明 rich content 或 Page content 已验证。任何具名 manual-validation hierarchy scenario 继续在正向与恢复步骤前后读取其 scoped Page，并比较富内容语义、稳定内容摘要及内容对象，保留失败现场。不得新增隐式 Sync、close/reopen、raw XML 或名称选择。
- **验收标准**：生产 hierarchy mutation 的成功、execute-error reconciliation、not-applied 和 partial 路径均不得调用 `get_page_content` / `PageService.xml` 或同等逐 Page 正文读取；仍必须 fail closed 于 hierarchy ID、父级、scope、缩进、关系、direct-child 集合或顺序异常，并不得重放 mutation。确定性合同必须覆盖 Reparent、Page Reorder、Section Reorder 和未来 Sort 的零 Page XML read，以及 Page ID-only mapping（如适用）。manual-validation runner 的既有逐 Page before/after/restore comparator 不得删除或降级；新增或受影响的 hierarchy mutation scenario 仍须由用户在 fresh disposable Notebook 前台重新验证，方可关闭本项。
- **实现与验证状态**：Reparent 子范围已完成：`MutationService._capture_reparent_snapshot` 已改为 content-free hierarchy bookend；Page、Section 与 SectionGroup validator 均删除生产 Page XML/内容对象比较，Page `id_map` 收敛为 Page ID。`tests/test_reparent_section.py` 已覆盖 Section 容器和 Page Reparent 的零 Page XML read，并固定 Page execute-error content-only 差异在 hierarchy postcondition 已成立时为 reconciled success。该部分与 `tests/manual_validation/tests/test_reparent_scenarios.py` 为 `49 passed`；完整 pytest 其余基线为 `1200 passed, 1 deselected`（唯一排除的是用户暂存 Grok 授权 profile 与既有静态期望的不一致）。本次扩展尚未修改 `_reorder_container` 的 `_page_digests` 调用、相关生产合同、canonical 文档或 manual-validation scenario；未来 Sort 也尚未实现。因此 UT-007 从仅 Reparent 的 `completed` 回到整体 `accepted`。
- **用户真实复测证据**：用户于 2026-08-17 明确确认 `reparent-section`、`reparent-page`、`reparent-page-with-level` 与 `reparent-section-group` 四个受影响的具名前台 scenario 均已通过。这些证据只覆盖已完成的 Reparent 子范围；用户未提供本次扩展后 Section Reorder 或未来 Sort 的前台复测。用户未提供 Reparent run ID、cache/fresh 模式、bridge audit 或逐 case transcript，故本文只记录已观察到的通过结论；Agent 未执行真实 runner。
- **完成判定**：仅 Reparent 子范围已具备生产性能取舍、content-free hierarchy 合同、逐 Page manual comparator、确定性自动化与四个具名前台场景的用户通过确认。要将整体 UT-007 再次标为 `completed`，还须移除现有及新增 typed hierarchy mutation 的生产逐 Page read-back、补齐确定性零读取合同和 canonical 文档，并由用户完成受影响具名 scenario 的前台复测。该结论不外推为跨版本保证。
- **范围边界**：本项降低的是所有层级变更在生产调用延迟路径中的验证范围，不改变各操作既有 gate、exact ID、confirmation、同 Notebook 限制、收敛、single-attempt/replay-never、partial/indeterminate fail-closed 或 manual validation 的正文保真门限；不将 manual evidence 外推为跨版本保证。它不改变正文 mutation 的内容验证合同。

### UT-008：`get_page_text` 不能表达 Page 富文本结构

- **问题描述**：2026-08-17，用户在读取既有 Page 时发现 `get_page_text` 默认返回 plain text，几乎不包含富文本结构。对需要理解链接、强调、字体/颜色、列表层级、表格单元格或其他 inline/block 语义的 Agent 而言，纯可见文字不足以可靠重建原有表达。
- **代码核验**：公开 `get_page_text(page_id, max_chars=60000)` 没有 `format` 或结构投影参数。`PageService.get_text` 调用 `text_from_page_xml(self.xml(page_id, "basic"))`；该 parser 遍历 `<T>` 元素，再由 `HTMLTextExtractor` 仅收集 `handle_data` 的字符，并只为 `br/p/div/li/tr/h1`–`h6` 插入换行。HTML tag、attribute、style、hyperlink target、list/table 边界与其他富文本语义不进入响应。当前 `get_page_content_objects` 是图片/附件等 typed 对象 metadata，不补足文本结构；公开契约也明确 Raw Page XML 不属于读取降级路线。
- **改进决策**：已接受让同一读取能力支持 `plain` 与 `rich` 两种模式的方向。`plain` 保持当前默认值和字符预算语义，以避免改变既有调用；`rich` 的具体投影（受预算的 Markdown、HTML、typed block tree 或其他结构化响应）、参数名称和 response schema 尚未决定。实施时必须评估链接、对象引用、HTML 消毒、字符/响应预算、既有 `get_page_text` 兼容性与 manual validation 覆盖；不得直接暴露 raw Page XML 或绕过当前对象二进制预算。
- **实现与证据边界**：本轮只更新用户测试台账，不修改生产代码、公开 Tool schema、README/design contract、自动化测试或 manual-validation scenario。当前代码仍只有 plain-text 投影；实施后的真实 OneNote 复测只能由用户前台执行。本条不量化特定 OneNote 文档中被丢失的内容比例。

### UT-009：重建式 Move 的 `verify_copy` 等价判断常因 COM 重写结构失败

- **观察**：2026-08-17，Grok Build 在已开启 Writes、Copy、Deletes 的配置下，把既有 Page 用 `move_page` 重建式搬到 disposable 目标 Notebook。调用方能独立发出 `move_page`；多数结果是 `ok=true` 但 `partial_failure` / `outcome=copy_only`，`failed_step=verify_copy`，`source_deleted=false`。目标区已出现同名副本，源页仍在原 Section。少数页完整 `moved`。本文不记录 Notebook/Section/Page 名称、标题、正文或真实 COM ID。
- **校验位置**：生产路径在 `write_page_content` 之后、删源之前，用 shipped `page_equivalence(transform_page_for_copy(源 XML), 读回目标 XML)`。默认档 `strict_canonical` 要求四项全过：`canonical_xml`（忽略 objectID、作者/时间、选中态和根 ID/name/pageLevel 后的规范化树哈希）、`visible_text`、`content_objects`（类型计数）、`binary_sha256`（图/附件解码字节）。仅当页的能力集是 Outline/RichText/List/Tag 的子集时才降到 `semantic_list_tag`（此时 `canonical_xml` 可以不过）。含 Table 或 Image 的页留在严格档。`verify_copy` 还要求拓扑收敛；任一页等价失败即 fail closed，不删源。另有一类：copy 读回已过，但删源前源范围变化，同样停在 `copy_only`。
- **典型失败形态**（对仍存活的三对源/副本，只读 `GetPageContent` 后走同一套 shipped 函数复现；不写入新 mutation）：
  1. **Title 下多余 `T` 被合并**：提交的 `Title/OE` 有两个 `T` 子节点，COM 读回只剩一个。`canonical_xml` 与 `visible_text` 失败（可见字数差 2），`content_objects` 与 `binary_sha256` 通过。页含 Image，走严格档。
  2. **根上少一块 Outline**：提交的 Page 根有两块 Outline，读回只剩一块。可见字完全一致，`canonical_xml` 与 `content_objects`（Outline 计数 2→1）失败，`binary_sha256` 通过。页含 List+Table，不能进 `semantic_list_tag`。
  3. **表格单元格 OE 被压扁**：可见字与对象计数一致，仅 `canonical_xml` 失败。首次树差在某一 `Table/Row/Cell/OEChildren/OE` 的子节点数 2→1。页含 List+Table，同样走严格档。
- **问题分析**：这不是 Agent 选错工具或未给 confirmation。COM `UpdatePageContent` 会改写 Title 分段、空/重复 Outline 和表格单元格内部结构；严格 XML 把这些当成不等价，于是重建式 Move 在「副本已在、源未动」处停住。`binary_sha256` 常通过，说明图/附件未丢。既有 `tests/test_copying.py` 已固定：纯 List/Tag 页允许 `canonical_xml=false` 仍等价；带 Table/Image 则必须四项全过。工作日志类页面多数带 Table 或 Image，因此大量落在严格档。
- **改进决策**：接受按内容能力组合的语义验证，不因页面含 Table/Image 就直接切换为整页严格 XML 比对。标题有效值、富文本/List/Tag、表格行列与单元格语义、二进制对象哈希及非空 Outline 分项验证；仅允许标题文本节点合并、空 Outline 消除、表格单元格 OE 扁平化等已知 COM 规范化。未知结构或语义投影不完整时，仍回退严格比对并 fail closed。
- **实现边界**：当前生产代码和删源门限不变，`copy_only` 仍不得解释或重放为成功 Move。实现时须补齐内容丢失负向用例、content-free 验证摘要和具名 manual-validation scenario，并由用户前台确认不会误删源 Page。

## 单项记录最低要求

- 用户任务和预期结果；
- 客户端、测试日期、代码 commit/version 与 OneNote 前置状态；
- 开启的 7 类公开 gate 投影；
- content-free 的实际工具选择、调用次数和失败/恢复阶段；
- 预期与实际差异，以及问题分类；
- 接受、拒绝或延期的理由；
- description/schema/config/code 的具体变更位置；
- 聚焦测试、完整测试及用户真实复测证据；
- 是否影响 52 项工具面、授权矩阵、公开合同或已知限制。

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
