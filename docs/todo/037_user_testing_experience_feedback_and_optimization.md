# 037：用户测试期工具调用与实现体验优化

> ID：037
> 状态：进行中
> 优先级：P0
> 类型：User Testing / Developer Dogfooding / Tool UX / 反馈驱动优化
> 更新日期：2026-08-16

## 当前状态

[TODO 034](034_pre_user_testing_tool_surface_convergence.md) 已完成用户测试准入。项目现进入开发者模拟真实用户行为的持续使用阶段：开发者通过受支持的 MCP 客户端，从用户任务出发发现、选择并调用工具，再根据实际体验提出和验证优化。

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
