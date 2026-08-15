# 035：Copy/Move 内部 Planning、Preview 高级能力与 Agent 职责收敛

> ID：035
> 状态：待办
> 优先级：P1
> 类型：公开工具契约 / Agent 能力模型 / Copy/Move 安全 / 工具面收敛
> 更新日期：2026-08-15

## 背景

当前 Copy/Move 公开协议要求 Agent 先调用 `plan_copy`、`plan_move_page`、`plan_move_section` 或 `plan_move_section_group`，保存返回的 `plan_digest`，再把它连同 confirmation fields 交给对应执行工具。Plan 阶段承担源/目标快照、选择范围、内容与拓扑摘要、预算和过期检测等重要安全职责，但把它实现为必经的公开 Tool 链，也使 Agent 被迫参与操作状态管理：它必须选择正确的 Plan/Execute 配对、搬运 digest，并在多次 Tool 返回之间维持流程。

引入受控操作层后，Agent 应只表达业务意图和必要确认；内部 planning、冻结状态、执行进度、identity tracking、Copy 完整性门、Move 源删除门、reconciliation 与恢复状态应由受控操作边界负责。公开 Plan Tool 不应继续作为执行前置协议。与此同时，用户或维护者仍可能需要在高风险、大范围或诊断场景中预览影响范围，因此保留独立、只读、非必经的 Preview 高级能力，而不是把 Preview 重新变成执行 token 或 Agent 状态机。

本 TODO 只冻结工具集、公开合同、Agent 职责和迁移验证要求；不设计或实现受控操作层的类、状态机、持久化方式或内部代码结构。

## 决策摘要

- 从生产 MCP 工具集中移除 `plan_copy`、`plan_move_page`、`plan_move_section` 和 `plan_move_section_group`；不保留兼容 alias。
- `copy_*` 与 `move_*` 成为单次公开调用。执行工具不再接收由 Agent 管理的 `plan_digest`，但必须在受控操作边界内保留等价或更强的 planning、冻结、过期检测、预算与安全门。
- Agent 只提交 Copy/Move 意图、精确 source/destination ID、范围、必要 confirmation 和显式权限所需信息；不保存或解释 operation plan、digest、allocated/remapped IDs、completed steps 或 replay 状态。
- Preview 作为可选、只读的高级工具类别限制开放，不进入默认用户 profile，不参与执行授权，也不是 Copy/Move 的前置步骤。
- Preview 结果只描述调用时观察到的影响范围、能力限制、预算和风险；后续执行必须重新读取 live 状态并独立建立内部计划，不能把 Preview success 写成 mutation readiness 或完成保证。
- 移除公开 Plan 协议不得削弱 Copy fidelity、完整单射 ID map、Move Copy gate、删除前源重校验、非永久删除、partial/indeterminate fail-closed 或具名真实验证门限。

## 目标工具面

### 默认用户 Profile

保留面向用户任务的 typed 执行工具：

```text
copy_page
copy_section
copy_section_group
copy_notebook
move_page
move_section
move_section_group
```

这些工具各自完成一次完整的受控操作调用。Agent 不应为了完成一次 Copy/Move 再编排公开 planning Tool。

### 移除的公开工具

```text
plan_copy
plan_move_page
plan_move_section
plan_move_section_group
```

移除包括默认 registry、其他生产 profile、公开 schema、Tool description、README 推荐调用链、Agent 示例和 `health_check` capability 投影。底层只读捕获、摘要和计划构造逻辑可继续由受控操作内部复用，但不得保留可被生产 MCP 枚举的旁路。

### 高级 Preview 能力

Preview 家族的最终名称与 typed 粒度应在实施前随对象—操作矩阵冻结；无论采用通用 `preview_copy/preview_move` 还是 typed 变体，都必须满足：

- effect 固定为 read-only，不创建、不复制、不删除、不改变 Notebook lifecycle；
- 只在独立的高级 exposure 决策下可见，不进入默认用户 profile；
- exposure 与 Copy/Move mutation authorization 相互独立；看见 Preview 不代表获得执行权限；
- 不返回执行必须消费的 `plan_digest`、capability token 或服务端 operation handle；
- 不承诺 Preview 与未来执行之间状态不变，不声称 `mutation_ready`；
- 输出保持 content-free，只公开 typed 对象范围、计数、受支持性、预算结论、风险和警告，不返回 Page 正文、raw XML、binary、secret 或完整路径；
- Agent 可以在用户明确要求“先预览”时调用，但正常 Copy/Move 不依赖 Preview。

## Agent 能力定位

### Agent 负责

- 理解用户意图，选择 Copy 或 Move；
- 提交精确 source/destination ID；
- 明确 root-only 或完整子树范围；
- 提交当前公开合同要求的名称、parent、modified 等 confirmation；
- 对 Move 明确表达用户允许非永久源删除的业务意图；
- 收到成功、partial 或 indeterminate 结果后向用户解释状态，并在需要扩大权限或人工恢复时请求用户决策；
- 仅在用户需要影响范围说明时调用高级 Preview。

### Agent 不负责

- 选择和配对 Plan/Execute Tool；
- 保存、转发、比较或解释 `plan_digest`；
- 判断计划是否过期或重新生成计划；
- 管理 allocated、created、resolved 或 remapped ID；
- 根据 COM error 自行决定 replay；
- 判断 Copy 是否足以授权 Move 删除源；
- 在 `copy_only`、`partially_applied` 或 `indeterminate` 后自动拼接下一次 mutation；
- 将 Preview 结果当成执行授权、readiness 或完成证明。

职责原则为：

```text
Agent 管理用户意图与交互决策；受控操作边界管理执行状态与安全证明。
```

## 公开执行合同要求

本 TODO 不规定受控操作层怎样实现，但单次 `copy_*` / `move_*` 调用对外必须保持以下可观察保证：

- 在任何 mutation 前重新读取 live source/destination 并建立当前调用专属的内部计划；
- 精确绑定对象类型、parent、范围、稳定内容/拓扑摘要和预算，拒绝过期 confirmation；
- Create/Copy 产生的 allocated、resolved 与 remapped identity 由服务内部持有和核对；
- Copy 只有在完整 topology、fidelity 与单射 ID map 门通过后才可返回完整成功；
- Move 只有在 Copy 门通过、源重新捕获仍与受保护语义一致后，才可开始非永久源删除；
- 任一 Copy 结果无法证明时，Move 保持 `copy_only`，不得删除源；
- partial 或 indeterminate 不得由 Agent 或工具自动重放整个 Copy/Move；
- 返回稳定、content-free、Agent 可行动的 outcome、retry safety 和 recovery 信息；
- Preview 与执行之间不存在可信 TOCTOU 绑定，执行始终以自己的 live preflight 为准。

公开参数调整应优先删除 `plan_digest`，不得用另一个由 Agent 管理的 opaque token、operation ID 或隐藏 session state 原样替代。若未来确有跨调用用户授权需求，必须另立设计并说明生命周期、失效、重放、进程重启和权限边界，不属于本 TODO。

## 与现有工具面工作的关系

- [TODO 034](034_pre_user_testing_tool_surface_convergence.md) 负责最终用户 Profile、Exposure/Authorization/Stability 三维审计和 registry 收敛；本 TODO 冻结其中 Copy/Move Plan/Preview 的专项决策。
- [TODO 029](029_mcp_mutation_readiness_and_reconciliation_hardening.md) 负责 mutation readiness/replay policy 和受控执行可靠性；本 TODO 只要求其能力边界承接内部 planning，不规定具体对象或实现。
- Copy/Move 现有 plan digest、fidelity、partial 和源删除安全合同仍是迁移基线；不能把“减少 Agent 编排”解释为减少验证步骤。

若受控操作能力尚不能覆盖当前公开 Plan 所提供的保护，必须继续保留旧协议并保持本 TODO 为待办或阻塞，不能先删 Tool 再补安全能力。

## 实施阶段

### A. 冻结迁移合同

- [ ] 列出四个 Plan Tool 当前提供的全部安全证明、公开字段、调用方和 manual-validation 依赖；
- [ ] 为每项能力确定迁移后的内部责任归属和外部可观察保证，但不在本 TODO 中规定类或状态机实现；
- [ ] 冻结七个 Copy/Move 执行工具移除 `plan_digest` 后的参数、confirmation、返回和失败合同；
- [ ] 冻结高级 Preview 的名称、typed 粒度、exposure gate、输出上限和 content-free schema；
- [ ] 用户审阅并确认 Agent 职责边界、默认工具面和 Preview 高级分类。

### B. 收敛公开工具面

- [ ] Copy/Move 执行工具改为单次公开调用，Agent 不再传递计划状态；
- [ ] 从所有生产 registry/profile 移除四个 Plan Tool 和相关公开 adapter；
- [ ] 删除 `plan_digest` 公开输入、Tool description、示例和 Agent 工作流；
- [ ] 内部规划能力继续服务 Copy/Move，且不存在可枚举的 Plan 旁路；
- [ ] Preview 若实施，只出现在冻结的高级 exposure 下，默认 profile 不可见；
- [ ] 不保留旧名 alias、兼容 wrapper 或可绕回旧两阶段协议的 generic/raw 工具。

### C. 同步验证与文档

- [ ] 更新 `docs/design/tool_contracts.md`、架构文档、根 README、`health_check` 和工具目录；
- [ ] Manual-validation Scenario 改为调用一次公开 Copy/Move Tool，同时继续保存内部安全结果的 content-free 外部证据；
- [ ] 更新 scenario 最小 allowlist，移除 Plan Tool；Preview 不得成为 mutation scenario 的必需工具；
- [ ] 更新注册/schema/description/Agent 调用链测试和 Copy/Move 聚焦合同；
- [ ] 搜索并清理当前契约中的旧 Plan Tool、`plan_digest` 和强制两阶段调用说明；历史 TODO 中作为既有证据的名称保持历史语境。

## 自动化合同

至少覆盖：

- [ ] 默认与所有生产 Profile 均不枚举四个 Plan Tool；
- [ ] 七个 Copy/Move 执行 Tool 的 schema 不包含 `plan_digest` 或替代性跨调用状态 token；
- [ ] 每个 Copy/Move 用户任务只需一次公开 mutation Tool 调用；
- [ ] Copy/Move 在 execute 前仍拒绝 stale source、stale destination、错误 scope、错误 parent 和预算超限；
- [ ] allocated/resolved/remapped identity、完整单射 ID map 和 fidelity 合同不回退；
- [ ] Move 在 Copy 未完整验证、源重校验失败或目标状态不确定时绝不删除源；
- [ ] partial/indeterminate、replay safety、completed steps 和 recovery 信息不因移除 Plan Tool 而丢失；
- [ ] Preview 默认不可见，只有显式高级 exposure 才能枚举；
- [ ] Preview 无 mutation bridge 调用、无 lifecycle 调用、无执行 token，并保持 content-free；
- [ ] Copy/Move policy 仍相互独立、默认 fail closed，Preview exposure 不会开启任何 mutation 权限；
- [ ] registry、README、design、health capability 和 manual-validation allowlist 投影一致；
- [ ] 聚焦纯测试与完整 `.venv\Scripts\python.exe -m pytest -q` 通过。

## Human-gated 真实验证

迁移完成后，现有 Copy Page、Copy Section、Copy SectionGroup、Copy Notebook、Move Page、Move Section 和 Move SectionGroup 具名场景应由用户在 disposable fresh/cache 输入上重新运行。证据必须证明：

- scenario 只调用新的单次公开 Copy/Move mutation Tool，不调用 Plan 或 Preview；
- Copy 的范围、ID map、内容 fidelity、目标位置和 cleanup/restore 合同继续成立；
- Move 的 Copy gate、源删除前重校验、非永久删除、目标复核和 partial fail-closed 合同继续成立；
- 最小 tool allowlist 不包含 Plan，Preview 也不是必需依赖；
- 默认 Profile 中无法枚举 Plan 或 Preview，高级 exposure 下的 Preview 只读且不会改变 fixture。

真实 Scenario 只能由用户显式启动。Agent 只能运行纯测试、mock、dry-run 和读取用户保存的 evidence，绝不能执行真实 `run.py <scenario>` 或 `run.py all`。

## 非目标

- 不在本 TODO 中设计或实现 `MutationOrchestrator`、Operation class、状态机、内部 token、持久化或跨进程协调；
- 不改变 Copy/Move 的对象范围、内容保真标准、ID remap 语义或非永久删除规则；
- 不把 Copy 和 Move 合并为接受任意 action 的 generic mutation Tool；
- 不把 Preview 放入默认用户 Profile，也不让 Preview 成为执行前置协议；
- 不用隐藏 session、后台 daemon、文件状态或 Agent memory 替代 `plan_digest`；
- 不放宽 Writes、Copy、Move、Delete 或 Permanent Delete 的独立安全门；
- 不引入云服务、Microsoft Graph、遥测或直接 `.one` 文件处理；
- 不由 Agent、pytest、CI、hook、timer、watcher 或后台任务启动真实 OneNote mutation 验证。

## 完成定义

- [ ] 四个 Plan Tool 已从所有生产工具面、公开 schema 和推荐调用链移除，且不存在兼容 alias 或注册旁路；
- [ ] 七个 Copy/Move 执行工具均为单次公开调用，Agent 不再提交 `plan_digest` 或其他操作状态 token；
- [ ] 现有 Plan 的全部安全能力已进入受控操作边界，并通过外部合同证明没有回退；
- [ ] Agent 的意图/确认职责与服务的执行状态/安全证明职责已写入 canonical Tool contract 和 README；
- [ ] Preview 若交付，只在独立高级 exposure 下可见，默认隐藏、只读、非必经且不产生执行 token；若本轮不交付，公开文档不得暗示它已经存在；
- [ ] 自动化 registry/schema/policy/Copy/Move/Preview 负合同与完整 pytest 通过；
- [ ] Manual-validation 最小 allowlist 和 Scenario 已迁移到单次调用，用户确认适用 fresh/cache 真实回归通过；
- [ ] TODO 034 的最终工具矩阵、design、README、health 和实际 `tools/list` 一致；
- [ ] 用户确认最终 Copy/Move 工具集和 Agent 能力定位后，本 TODO 才可标记为“已完成”。

## 关联

- [TODO 029：MCP Tool Mutation Readiness 状态建模与 Page Reparent 加固](029_mcp_mutation_readiness_and_reconciliation_hardening.md)
- [TODO 034：用户测试前 MCP 工具面收敛与不必要入口隐藏](034_pre_user_testing_tool_surface_convergence.md)
- [TODO 002：P2 四层 Copy 与 Page Move](002_p2_copy_and_reconstructive_page_move.md)
- [TODO 012：跨 Notebook Section 与 SectionGroup 重建式 Move](012_reconstructive_section_and_section_group_move.md)
- [公开 Tool 契约](../design/tool_contracts.md)
- [Manual Validation Runner](../../tests/manual_validation/README.md)
