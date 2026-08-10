# Agentic 开发中的敏感权限测试：在安全与自动化之间建立边界

在软件开发中，当我们需要测试会修改真实数据、调用外部服务或产生现实后果的功能时，往往很快就会触及真实的权限边界。比如，一个 OneNote 工具要验证页面移动和删除，就必须实际修改笔记本；邮件助手要验证发送流程，就可能真的发出邮件；运维工具要验证部署和回滚，则需要访问云资源、密钥和生产级 API。普通查询可以安全地在 CI 中反复执行，但一旦涉及写入、删除、付款、外部通信或不可逆操作，测试本身也会成为风险来源。这个问题可以概括为：**如何在验证敏感操作在真实系统中的有效性与保留测试自动化效率之间，建立可审计、可约束且不会被意外触发的授权边界。**

这并不是 Agent 出现之后才需要考虑的安全问题。即使完全由人编写和运行测试，最小权限、环境隔离、显式审批、临时凭证、精确目标和可验证清理，也应当是敏感操作测试的基本要求。但当测试涉及真实后端和外部副作用时，Agentic 开发让这一边界变得更加棘手：Agent 必须获得完成任务所需的权限，其动态选择工具、调整参数和延长操作链路的能力，又增加了控制误操作及其连锁影响的难度。约束过松，真实数据和外部系统可能暴露在不可控的操作链路中；约束过严，Agent 则只能频繁停下来等待人工介入，自动化带来的效率提升也会随之减弱。

因此，面向 Agentic 开发去设计受控的授权与验证机制，不只是为了“阻止危险”，也是为了“安全地释放自动化”。开发者负责决定何时授权、允许哪些能力以及可以影响哪些资源，Agent 则在明确边界内完成环境检查、场景执行、证据采集、结果验证和清理。这样既不需要因为风险而把敏感功能永久排除在自动化之外，也不必为了追求效率而向 Agent 交付不受约束的完整权限。

## 从项目实践出发：两种互补的验证方式

在开发 local-onenote-mcp 的过程中，我最先形成了两种可操作的方法：测试替身与契约验证，以及门控式实后端验证。它们并不是相互替代的两套方案，而是分别回答两个不同的问题：前者回答“代码是否按约定做出了正确决策”，后者回答“这些决策作用于真实 OneNote 后端时，是否真的产生了预期结果”。

### 测试替身与契约验证

项目中的默认自动化测试不会连接 OneNote，而是用 Mock bridge 替代真实 COM 后端，验证工具注册、参数转换、权限分支、服务编排和错误返回。需要写权限的测试还通过单独的 `write_contract` 标记运行，以便明确区分普通只读回归和 mutation 合同测试。对于 Copy 和 Move，这一层可以稳定覆盖计划摘要是否确定、计划过期后是否在 mutation 前拒绝、预算是否提前生效、未知 XML 节点是否被报告、部分失败是否返回已创建对象，以及删除门是否只在内容验证通过后打开。

这里更准确的名称是“测试替身与契约验证”，而不只是 Mocking。Stub 可以返回预设结果，Mock 可以检查调用方式，Fake 可以提供一个简化但可运行的实现；契约测试则进一步约束调用方与提供方对接口的共同理解。Google 的测试建议也强调：真实实现的保真度最高，其次是 Fake，只有无法使用前两者时才选择 Mock，因为 Mock 最容易与真实依赖的行为发生漂移。[Google Testing Blog：Increase Test Fidelity By Avoiding Mocks](https://testing.googleblog.com/2024/02/increase-test-fidelity-by-avoiding-mocks.html)

这种方式速度快、结果稳定，适合进入默认 pytest 和 CI，也能够主动构造超时、拒绝和部分失败等真实环境中难以安全复现的分支。但它证明的是“我们的代码遵守了自己定义的契约”，不能证明 OneNote COM 的真实鉴权、XML 往返、页面层级、副作用和回收站语义也与契约一致。Mock 测试全部通过，最多说明真实验证已经具备了一个可信的起点。

### 门控式实后端验证

为了补上真实后端证据，项目在 `tests/manual_validation/` 中建立了一个统一 runner。真实 mutation 不会由 pytest、CI、hook、安装脚本、import 或前台/后台 Agent 执行；用户必须本人在终端明确选择 `create`、`rename`、`reparent-section` 等具名场景。每个扁平的 `run.py <scenario>` 本身都是完整隔离闭环，会创建全新 Notebook、准备 fixture、运行所选场景（`create` 仅保留预设 fixture）、报告并关闭或保留 Notebook；`validate` 和诊断辅助 action 均不是公开入口。运行这条命令本身就是对该场景的一次授权，不再要求用户逐步点击确认；Agent 只能准备代码、运行纯合同测试并把命令交给用户。

授权之后，runner 不会给任何 Agent 或测试进程开放笼统的“完整权限”。每个 scenario 最多启动一个 MCP 子进程，其静态权限与 tool allowlist 只覆盖该场景的最小 fixture、mutation、证据读取和 restore/cleanup 闭包，并在 fixture 前通过 `health_check` 核对每个权限位；不同场景之间不使用权限并集。源 Notebook create/get/close 由只暴露生命周期操作的窄 wrapper 完成，并通过 lifecycle lease 绑定精确 ID、名称和本地路径。永久 OneNote Delete 始终关闭；Raw XML 只在两个不进入 `all`、不接受外部 XML 的 advanced `reparent-*` 能力场景中启用；Copy、Delete 和 Move 也只在对应具名场景启用。

真正的执行过程仍然是自动化的：runner 采集 before 快照，调用一次 mutation，回读 after 状态，验证对象 ID、父子关系、页面顺序和内容摘要，然后对可恢复操作执行恢复或清理并生成 restored 证据。非幂等 mutation 不自动重试；如果 Copy 只完成了一部分，报告会保留 `created_ids`、`id_map` 和剩余状态，让用户基于证据处理，而不是让 Agent 猜测性地再次修改数据。

因此，它不是传统意义上的“手工测试”，也不是可以无人值守运行的自动测试，而是一种**人工触发、能力受限、自动执行并自动取证的真实后端验证**。GitHub Environments 采用了相似的门控思想：受保护任务在审批通过前不能取得环境密钥，还可以禁止发起者自我审批。[GitHub Docs：Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) 不同之处在于，本项目把授权进一步绑定到了一个固定场景、一组精确资源和一个短生命周期子进程。

## 补齐两者之间的验证手段

测试替身和真实 mutation 之间并非只有一次跳跃。调研中可以看到，还有一些方法能够在不立即承担完整副作用的情况下提高保真度；另一些方法则适合在功能已经通过隔离验证后控制发布风险。按照从低风险到高真实性的顺序，它们可以放在以下位置。

### 1. 权限策略与安全不变量测试

第一类补充不是模拟业务后端，而是直接测试安全门本身。例如，自动检查所有写工具是否声明写权限、删除工具是否额外要求删除权限、Move 是否同时依赖 Copy 和非永久删除，以及任何默认配置是否都无法开启 Raw XML 或永久删除。还可以检查 manual runner 是否只有一个入口、每个场景的权限矩阵是否固定、mutation 是否禁用自动重试。

这类测试很适合本项目，因为它完全不接触 OneNote，却能防止一次普通重构意外拆掉安全边界。它不能验证业务结果，但可以作为所有其他测试的前置条件：如果无法证明权限门默认关闭，就不应该继续讨论如何安全地打开它。

### 2. Hermetic Fake 与本地仿真

Hermetic test 会把完整受测系统和可控依赖封装在本地环境中，不连接外部共享服务。Google 将能够在单机、无外部网络条件下启动的完整服务称为 Hermetic Server；这种方式比逐个 Mock 方法更接近真实组件间交互。[Google Testing Blog：Hermetic Servers](https://testing.googleblog.com/2012/10/hermetic-servers.html)

如果目标是 HTTP API、数据库或消息队列，本项目可以考虑实现一个内存层级树和 Page XML Fake，让相同的工具编排同时运行在 Fake 与真实 bridge 上，并用一组契约样例约束二者。但 OneNote COM 没有官方 emulator，许多关键行为——例如 OneNote 如何重新分配对象 ID、规范化 XML、处理内嵌二进制和移动回收站——正是我们需要验证的未知量。自己实现一个“看起来像 OneNote”的 Fake，可能只是把假设写了两遍。因此它适合扩大编排和状态机覆盖率，不适合用来宣称 Copy 或 Move 已经获得真实保真验证。

### 3. Record/Replay 与轨迹回放

Record/Replay 先在受控环境记录真实请求、响应或 Agent 工具轨迹，经过脱敏和版本化后，在自动测试中重放。Playwright 的 HAR 测试就是典型例子：先记录实际网络交互，后续测试直接从 HAR 提供响应，不再访问真实 API。[Playwright：Mock APIs](https://playwright.dev/docs/mock)

本项目可以保存脱敏后的 hierarchy 快照、规范化 Page XML 样本、调用 envelope 和已知 COM 错误，用它们回归解析器、Copy planner、内容能力识别和报告生成。对于二进制内容，只保留类型、长度和 SHA-256，而不保存附件正文。它能比手写 Mock 更真实地覆盖历史复杂输入，但仍然不能回放“写入后 OneNote 会怎样改变状态”；录制样本还会随着 Office 版本和 XML schema 演进而老化。因此 Record/Replay 应被视为高保真的离线回归，而不是实后端 mutation 的替代品。

### 4. Plan、Preview 与 Dry-run

Plan/Execute 两阶段把“将要做什么”和“真正执行”分开。Terraform `plan` 会读取远端现状并生成可审查的变更计划，但不会执行变更；其文档也提醒，最终 apply 前应重新检查计划，因为目标状态可能已经变化。[HashiCorp：terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan) Kubernetes 的 server-side dry-run 更进一步：请求会经过真实鉴权、默认值、校验和 admission 流程，只在最终持久化前停止。[Kubernetes API Concepts：Dry-run](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run)

OneNote COM 并没有等价的 server-side dry-run，因此本项目不能虚构一个“调用了真实 Move 但不会落盘”的模式。不过 Copy 已经采用了可审计的 `plan_copy → copy_*` 流程：计划阶段读取真实源树和完整 Page XML，估算预算、检查名称冲突，并把源快照、目标快照和选项绑定进 `plan_digest`；执行阶段重新计算摘要，状态有变化就在任何 mutation 前拒绝。它验证不了最终写入语义，却能显著缩小真实执行前仍未发现的风险。

### 5. 逐调用审批与监督执行

对于开放式 Agent 工作流，任务目标可能在运行中变化，开始时的一次授权未必足够。OpenAI Agents SDK 的 Human-in-the-loop 流程允许工具声明 `needs_approval`，在敏感调用发生时暂停，并把具体工具和参数交给用户批准后再恢复运行。[OpenAI Agents SDK：Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)

这种方式适合发送邮件、购买、跨系统运维等无法预先固定完整轨迹的任务。对本项目的具名验证 runner，它反而不是首选：场景、目标和权限在启动前已经静态确定，每一步再询问一次既降低可重复性，也把用户训练成机械点击“同意”。更合适的边界是“一次授权一个固定场景”；只有当执行参数偏离计划、目标身份变化或需要处理未声明的部分失败时，才停止并把控制权交回用户。

### 6. Shadow 与渐进发布

Shadow testing 会把真实流量复制给候选系统，但不让候选结果影响用户。AWS SageMaker 的 Shadow Test 就是让新模型和现有模型同时接收请求，仅返回现有模型的结果，再离线比较候选输出。[AWS：Shadow tests](https://docs.aws.amazon.com/sagemaker/latest/dg/shadow-tests.html) 对 OneNote mutation 而言，完全真实的 Shadow 很难成立：如果不执行 COM 写入，就无法观察真实副作用；如果执行，又已经不再是 Shadow。可行的变体是让候选 Agent 针对只读快照生成计划和工具调用，与人工操作或旧版本结果比较，但禁止 dispatch mutation。

Canary 或渐进暴露则适合功能通过隔离实测之后的发布阶段。Azure Well-Architected Framework 建议先向小范围用户开放新版本，观察稳定后再逐步扩大范围。[Microsoft Azure：Safe deployment practices](https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/safe-deployments) 本项目不是持续承载线上流量的服务，因此不需要按请求百分比做传统 Canary；但可以按能力渐进开放：先只读，再开放可恢复写入，随后是实验性 Copy，最后才是会改变 Page ID 的 Move。同时按 OneNote 版本和 Office channel 记录实测范围，避免一次机器上的成功被误写成普遍保证。

## 从单点技巧到分层测试体系

这些方法的关键不在于选出一个“最安全”或“最真实”的方案，而在于让不同层级分别消除不同的不确定性。可以把敏感权限测试整理为下面五层：

| 层级 | 要回答的问题 | 主要方法 | 所需权限 | 本项目中的例子 |
| --- | --- | --- | --- | --- |
| L0 安全不变量 | 危险能力是否默认不可达 | 权限矩阵、策略测试、静态检查、入口检查 | 无真实权限 | 验证 mutation 默认关闭、永久删除不能被场景开启 |
| L1 离线逻辑与契约 | Agent 和工具是否按约定决策 | Test Double、Fake、契约测试、错误注入 | 无真实权限 | Mock bridge、`write_contract`、计划过期与部分失败测试 |
| L2 真实输入与无副作用预检 | 面对真实结构时，计划是否仍然合理 | 只读探测、Record/Replay、Plan、Preview、可用时的 server-side dry-run | 只读或不持久化权限 | 读取 hierarchy/Page XML、Copy 预算、冲突检查和 `plan_digest` |
| L3 隔离真实副作用 | 真实后端是否产生并保留预期语义 | Disposable sandbox、门控式实后端验证、自动回读和恢复 | 场景级短期最小权限 | 专用 Notebook、独立 MCP 子进程、before/after/restored 证据 |
| L4 受控发布 | 已验证能力在更真实分布下是否仍然安全 | Shadow、监督执行、Canary、能力开关和可回滚发布 | 逐步扩大的受控权限 | 实验能力默认关闭，按操作类型和 Office 版本逐级开放 |

这套分层有四条基本原则。

第一，**高层不能替代低层，低层也不能冒充高层**。真实 mutation 测试昂贵且危险，不适合承担所有边界条件回归；Mock 测试再完整，也不能形成“真实后端已经验证”的结论。

第二，**权限应绑定场景，而不是绑定 Agent 身份**。Agent 能做什么，不应取决于一次长期的“信任这个 Agent”，而应取决于当前任务、精确目标、允许的动作、时间预算和停止条件。任务结束后，权限随子进程或短期凭证一起失效。

第三，**风险越高，证据要求越强**。L1 可以用断言和调用记录证明；L2 应保存计划、快照摘要和冲突检查；L3 必须提供 before、after、restored 或 remaining-state，并对不可恢复结果明确责任边界；L4 还需要观察指标、停止条件和回滚路径。

第四，**验证结论必须携带层级**。与其笼统地写“测试通过”，不如明确标记为“离线合同已验证”“真实后端预检已通过”“隔离副作用已验证”或“受控发布已观察”。这样，后续开发者和 Agent 才不会把较低层级的证据误用成更强的安全承诺。

最终形成的方法论可以概括为：先用自动化证明危险能力受到约束，再用测试替身穷举逻辑，用真实只读数据校验计划，经过用户明确授权后在隔离环境验证副作用，最后才以监督和渐进发布扩大使用范围。安全和效率并不是二选一；真正有效的设计，是让自动化程度随着证据积累而提高，让权限范围始终落后于已经得到的验证，而不是领先于它。
