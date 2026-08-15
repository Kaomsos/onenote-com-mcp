# OneNote 未预启动时，短命 COM client 不能充当 Fixture hierarchy 的存活锚点

> 状态：当前有效的工程经验
> 观察日期：2026-08-14
> 观察环境：Windows `10.0.26200.0` x64、OneNote Desktop `16.0.20228.20158`、本地进程外 COM
> 生产 COM 生命周期：[`../design/architecture.md`](../design/architecture.md#6-运行时生命周期与并发)<br>
> Manual Validation 架构：[`../design/manual_validation_scenario_fixture_architecture.md`](../design/manual_validation_scenario_fixture_architecture.md)
> 相关 cache 经验：[`fixture_cache_consumer_materialization_and_live_validation.md`](fixture_cache_consumer_materialization_and_live_validation.md)
> 验证流程：[`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md)、[`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md)

## 结论

在当前观察环境中，manual-validation scenario 启动前 OneNote Desktop 是否已经运行，与 fixture hierarchy 能否跨独立 PowerShell/COM 调用保持可见具有稳定相关性：OneNote 已运行时，相同类型的 cache working copy 可以完成连续两次 hierarchy 稳定观察；OneNote 未运行时，fixture 准备会稳定停在 mutation 前，表现为 Notebook shell 长时间只有空 `children`，少数运行还会在首次完整观察后丢失整个 Notebook live ID。

这不是 `CloseNotebook(force=false)` 动作本身导致的失败。历史成功与失败路径都执行过相同的 import → exact close → same-path reopen checkpoint；决定性对照变量是 scenario 开始前是否已有 OneNote 进程。该 checkpoint 曾是问题常被观察到的身份交接边界，但不能据此倒置因果关系，当前实现已将它移除。

当前工程推断是：`OneNote.Application` 由 `ONENOTE.EXE` 进程外 COM server 承载，而项目的每次 bridge 调用都会创建一个新的非交互 PowerShell client 和一个新的 COM 对象，调用结束后该 client 进程退出。如果 scenario 需要由第一条调用冷启动 OneNote，就没有一个跨调用持有的 COM 引用或既有 Desktop 会话为刚导入的 live hierarchy 提供稳定存活锚点。后续调用可能重新取得 Notebook shell，却没有前一会话激活的 child hierarchy；更激进时，前一 live identity 整体消失。这个机制解释现有对照，但项目尚未直接测量 COM 引用计数或 OneNote 内部退出条件，因此应保留为工程推断，而不是 Microsoft OneNote 的通用平台保证。

## 真实观察

用户在同一机器、同一天进行了多组手动对照，并明确指出以下稳定规律：

- scenario 启动前 OneNote GUI 已启动时，没有出现这类 fixture failure；两个相邻 cache consumer 在 reopen 后均以两次完整、同签名 hierarchy observation 通过，声明对象分别全部完成 live ID 映射。
- scenario 启动前 OneNote GUI 未启动时，多个不同 Recipe 的 cache consumer 均在 fixture 阶段失败，覆盖 Create、Rename、Reparent Section 与 Reparent Page，而不是某一个 mutation 或 fixture shape。
- 这些失败发生在业务 mutation 前。working bytes 已 materialize，import batch 和 exact close/reopen 可以成功，immutable template inventory 也保持不变。
- 多数失败中 Notebook ID 仍可连续读取，但完整 `get_tree` 在整个有界窗口内始终返回空 child hierarchy；声明的 SectionGroup、Section 与 Page 全部缺失，缺失集合不随重复读取收敛。
- 另一次失败先得到一轮完整 hierarchy，下一轮开始同一 Notebook ID 已不可解析，后续读取均保持缺失。这是同一生命周期问题的更激进表现，不应被概括成唯一表现。
- 一次在 import-open 阶段被用户中断的运行只保留了最后一次 `open_hierarchy` 失败与随后不存在的 OneNote 进程；它没有完整失败收尾，因此只作为辅助现场，不被计入完成的成功/失败矩阵。

本文不记录真实 Notebook 名称、对象 ID、用户路径或 Page 内容。pytest、mock 与 `--dry-run` 只证明编排合同，不属于上述 OneNote 行为证据。

## 已排除或被削弱的解释

### 不是 immutable cache template 普遍损坏

同类 template 在 OneNote 已预启动时可以建立完整 working hierarchy；失败运行的 template inventory 没有变化。一次 working activation failure 因而不能反向证明 template bytes 不可信，也不应自动 quarantine 一个此前已验证的 entry。

### 不是 opaque copy 的主要耗时或正确性问题

失败发生在 working tree 已复制并交给 OneNote 之后。重复复制 template、清空合法 cache 或扩大文件系统重试不会恢复跨 COM session 的 live hierarchy。

### 不是 `CloseNotebook(false)` 单独造成

GUI 已预启动的通过运行与未启动的失败运行执行过相同 checkpoint。现有证据没有证明 close/reopen 对当前 manual runner 具有独立必要价值，因此它已从 fresh 和 materialized 路径移除；历史结果只保留为诊断对照。

### 不是延长 hierarchy polling 可以解决

失败状态不是逐步加载：连续观察得到相同的空 child 集合，或者 Notebook ID 持续不存在。更长 sleep/timeout 只延迟 fail-closed，不会创建缺失的进程生命周期锚点。

### 不能统一用 Notebook ID rebind 修复

只有少数失败丢失 Notebook ID；多数失败的 ID 和 shell 一直存在，丢失的是 child activation。只在旧 ID 不可解析时按路径寻找新 ID，既覆盖不了主要表现，也可能把新的空壳 identity误当作可 mutation 的 fixture。

## 设计影响

1. Manual validation 的 cache 安全含义仍由 immutable template 基线、run-local working copy、typed address 重绑和完整内容验证共同构成；OneNote 初始进程状态属于 working runtime readiness，不属于 template authenticity。
2. Fixture hierarchy convergence 必须保留双稳定和完整声明对象检查。Notebook shell 可读、`OpenHierarchy` 返回 ID、COM 调用成功或等待时间足够，都不能替代该门限。
3. 当前 manual runner 没有跨 bridge 调用的 COM lifecycle owner。MCP child 是长驻 Python 进程，但其 `OneNoteBridge` 每次仍启动独立 PowerShell，所以“单 MCP process”不等于“单长驻 COM session”。
4. 当前设计已把“OneNote Desktop 已预启动”落实为代码门限：公开 `health_check` 在首次 COM 读取前 fail closed；manual-validation 单项与真实 `all` 也在 Notebook lifecycle 前检查。该门限阻止已知失败路径，但不等于 runner 已具备冷启动能力。
5. 后续已选择规划显式 `launch_onenote_gui` 工具，而不是长期 COM owner；范围与安全门限见 [TODO 031](../todo/031_start_onenote_desktop_tool.md)。`health_check` 保持 check-only，不隐式启动应用。
6. 在没有长期 owner 的情况下，自动重开、重绑、重复 child activation 或增大 timeout 都不应被写成根因修复。

## 适用边界

这组结论只覆盖上述 Windows/OneNote 版本和本地 manual-validation 工作流。它不证明所有 OneNote 版本在没有 GUI 时都会退出，也不证明 GUI 窗口的可见性本身是必要条件；目前能够从证据支持的是“scenario 开始前已有 OneNote Desktop 进程/会话”与成功强相关。未来若通过受控进程取证区分“进程存在”“可见窗口存在”和“持有长期 COM reference”，应更新本文而不是保留模糊的 GUI 前置条件。

生产 COM 生命周期以 [`architecture.md`](../design/architecture.md#6-运行时生命周期与并发) 为准，Scenario/Fixture 的当前处理边界以 [`manual_validation_scenario_fixture_architecture.md`](../design/manual_validation_scenario_fixture_architecture.md) 为准。人工运行命令与授权边界仍以 [`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) 和 [`manual_validation/README.md`](../../tests/manual_validation/README.md) 为准；本文不定义新的 CLI、自动启动行为或公开 MCP 契约。
