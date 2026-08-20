# Manual Validation 嵌套 Section Cache 崩溃排查与生产隔离

> ID：052<br>
> 状态：待办<br>
> 优先级：P2<br>
> 类型：平台调研 / 安全加固<br>
> 更新日期：2026-08-20

## 背景

2026-08-20 的用户真实人工验证显示：manual-validation 将关闭的 immutable Notebook template opaque-copy 为 working Notebook 后，包含 `SectionGroup → Section` 的 hierarchy 存在两类 mutation 前失败。

1. `rename`、`move-section-group` 以及一次 `copy-notebook` 在 parent-relative 打开复制后的 Group 子 `.one` 时使 `ONENOTE.EXE` 崩溃。Windows WER 记录为 `onmain.dll`、`0xc0000005`、固定错误偏移；bridge 依次观察到 `RPC_S_CALL_FAILED` 和 `RPC_S_SERVER_UNAVAILABLE`。
2. `copy-section-group` 与 `query` 能激活 Group/Section，但嵌套 Section 内声明的 Page 在完整有界 convergence 窗口内持续缺失。

相同 cache fingerprint/byte inventory 的 `copy-notebook` 既出现过崩溃也出现过成功，其他包含同类 shape 的 Recipe 也有成功样本，因此当前不能把问题简化为固定 template 损坏、所有嵌套 hierarchy 必然失败或 persistent adapter 单独致因。可复用经验与临时门限记录在[Opaque cache working copy 中的嵌套 Section 不能假定可安全激活](../lesson/onenote_cached_nested_section_materialization_instability.md)。

## 当前安全状态

- Manual validation 中所有已知形成 `SectionGroup → Section` 的 programmatic Recipe 已暂时设置为 fresh-only。
- 显式 `--use-cache` 必须在 GUI probe、run directory、cache lookup/materialization、Notebook lifecycle 和 MCP process 之前 fail fast。
- Fresh scenario、平层 Section cache、生产公开 tool 契约和 immutable cache maintenance 规则不因本项自动扩大或放宽。
- 不允许通过自动重启 OneNote、无界等待、重复 child activation、猜测 ID/path、自动删除失败现场或 replay 未确定请求来绕过门限。

## 目标

1. 建立最小、隔离、用户启动的 fixture materialization 对照，分别覆盖：Notebook 根级 Section、单层 Group/Section、多 Section、Group/Group/Section、含 Page/无 Page，以及 cold build/validated hit。
2. 对同一冻结 workload 比较 `persistent_powershell` 与 `one_shot_powershell`，区分 PowerShell host 生命周期、COM Application proxy 生命周期、OneNote Desktop 进程状态和 opaque-copy hierarchy shape。
3. 收集 content-free 的 bridge phase、request delivery state、COM HRESULT、host generation、OneNote process/WER signature 与有界 hierarchy observations；不得落盘路径、OneNote ID、XML、正文或 binary。
4. 审计生产代码中全部 `OpenHierarchy`/`open_hierarchy_batch` 调用来源和参数形成方式，证明 copied nested path + SectionGroup parent ID 是否能从公开 MCP 请求到达。
5. 若生产调用可达或无法静态排除，增加最小 fail-closed 限制与负向自动化合同，使未知 nested materialization shape 不会通过共享 bridge 触发 OneNote 进程崩溃。
6. 明确 crash 后的 client state 与 [`TODO 051`](051_persistent_com_client_restart_refresh.md) 的分工：本项阻止/隔离 trigger，051 处理旧 proxy 的失效与刷新；两者都禁止自动重放未确定 mutation。
7. 只有原因、适用边界和安全恢复条件获得真实证据后，才按 Recipe/role shape 逐项考虑恢复 `--use-cache`，不得一次性全局解禁。

## 调查问题

- 崩溃是否要求 template 曾在同一个 OneNote/COM lifetime 中被创建并关闭，还是 validated hit 也可独立复现？
- Group 已在 hierarchy 中可见后，OneNote 是否还有不能通过公开 COM readiness signal 观察的内部 TOC/index 状态？
- `OpenHierarchy` 请求数量、父子批次间隔、Section 数量、Page 数量、Section 文件大小或嵌套深度，哪些变量与 crash/缺页相关？
- persistent host 持有的 COM proxy 是否放大竞态；在同一 frozen workload 下 one-shot 是否改变 fault bucket、成功率或 Page convergence？
- OneNote/Office 更新后 fault module、exception code 和 offset 是否保持，问题是否属于特定 build？
- 生产服务是否只对受控 Notebook lifecycle/create 路径调用 `OpenHierarchy`，还是存在可传入任意本地 copied hierarchy 的间接入口？
- 一旦收到 RPC server failure，哪些只读/写入请求状态必须标记为 indeterminate，哪些 client/host state 必须 poisoned，且如何与 051 的刷新契约一致？

## 非目标

- 不把 fixture cache 提升为生产功能，也不允许生产 MCP 直接读写 `.one` 文件。
- 不通过修改 opaque `.one`/`.onetoc2` 内容、生成新 TOC 或按名称猜测对象来修复。
- 不把某次等待成功写成固定 sleep，或以 retry 次数增加掩盖 OneNote crash。
- 不因 manual-validation bug 改变公开 mutation 参数、policy gate、execute-once/reconciliation 或 local-only 边界。
- 不由 Agent、pytest、CI、hook、timer 或后台进程运行真实 scenario；真实矩阵只能由用户前台显式启动。

## 完成定义

- [ ] 最小 shape × adapter × cache origin × OneNote/Office build 矩阵已冻结，并由用户确认至少一组真实复现/反例证据；
- [ ] 已把“真实观察”“工程推断”“已排除解释”分开记录，能够说明 crash 与 Page convergence failure 是否同源，或明确证据不足；
- [ ] 生产 `OpenHierarchy` 调用图、public-tool reachability 和参数约束已完成审阅，并有自动化负向合同防止 manual-only unsafe shape 泄漏；
- [ ] crash/RPC failure 后的 client poison、delivery state、不得 replay 与 TODO 051 的边界已写入当前设计并由纯测试覆盖；
- [ ] Manual validation 保持默认 fail-fast；任何局部解禁均有单独的 recipe shape 声明、自动化合同和用户真实验证证据；
- [ ] 完整纯测试集通过，且没有自动触发真实 OneNote mutation 或进程生命周期操作。

## 关联文档

- [Manual Validation Scenario 与 Fixture 架构](../design/manual_validation_scenario_fixture_architecture.md)
- [常驻 OneNote COM Client Bridge 状态模型](../design/persistent_com_client_bridge.md)
- [OneNote COM Bridge 运行依赖](../dev/onenote_com_bridge_runtime.md)
- [常驻 OneNote COM Client Bridge](048_persistent_com_client_bridge.md)
- [常驻 COM Client 在 OneNote 重启后的代理刷新](051_persistent_com_client_restart_refresh.md)
