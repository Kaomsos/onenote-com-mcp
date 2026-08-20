# 047：并发只读 Tool 调度与共享读协调

> ID：047
> 状态：待办
> 优先级：P1
> 类型：并发 / Read 性能 / MCP Transport / OneNote COM
> 更新日期：2026-08-19

## 决策摘要

当前 operation catalog 将纯 read 标为 `SHARED`，`ReadWriteCoordinator` 也允许多个 reader 同时持有共享读锁；但公开 FastMCP tool 虽声明为 `async def`，其主体立即同步执行 `invoke()`，没有 `await` 或 worker dispatch。在常见的单事件循环 transport 路径中，长 read 会阻塞后续 coroutine，使共享读在实践中表现为串行。

本项的目标是让已经声明为安全共享读的调用获得受限、可验证的并发执行机会，同时继续让 mutation 从协调取得到收敛/finalize 期间全程独占。它不优化单次 `move_page` 内部的 COM 调用，也不允许 read 在 mutation 期间观察中间状态。

## 当前边界与风险

- 现有协调器是 writer-preferring：writer 活跃或等待时，新的 reader 必须等待；该公平性和 timeout 行为必须保留；
- Runtime、services、bridge、debug trace writer 与 `ContextVar` 的线程安全/上下文传播必须逐项证明，不能仅因 `asyncio.to_thread()` 可用就假设正确；
- OneNote COM/PowerShell bridge 的多 read 并发容量和稳定性尚无真实证据；无界 worker 创建或无界 COM 并发均不可接受；
- 只读结果仍是 live state，不建立跨调用 snapshot 一致性承诺；mutation 取得写锁前后的 generation/invalidator 语义保持不变。

## 工作范围

### A. 调度边界与资格矩阵

1. 在 transport 与 `OperationRuntime` 之间建立单一、可审查的异步 dispatch 边界；禁止在每个 tool 函数中各自添加 ad-hoc `to_thread`。
2. 只有 catalog 显式标为 `SHARED`、且 backend/thread-safety 经 allowlist 审核的 operation 才可并发调度；不得仅从 `OperationKind.READ` 自动推断资格。
3. dispatch 使用有界 worker/队列与明确的 admission、取消和 timeout 语义；不得为每个请求创建无界线程，也不得让等待中的 writer 被 worker 饥饿阻塞。
4. mutation、lifecycle、UI、filesystem effect 和任何 `EXCLUSIVE` operation 继续走受协调器保护的独占路径；是否也交由统一 worker 执行须以“避免阻塞 event loop”与 worker 饥饿测试为准，不能改变其并发语义。

### B. 共享读正确性合同

1. 两个符合资格的 blocking fake read 必须能在相同 Runtime/Coordinator 中同时进入 handler；同一时刻 `move_page` 等 mutation 不得与任一 read handler 并行。
2. writer 已等待时，新 reader 必须被拒绝进入共享区；已有 reader 退出后 writer 先取得独占权，随后 reader 才可继续。
3. 验证 `ContextVar` 中的 execution/correlation ID、per-call backend counter、Debug Trace span、JSONL writer 锁与 audit 字段在并发时不串线、不丢失、不复用 ID。
4. 覆盖 handler/backend timeout、任务取消、桥接异常、worker 饱和和 shutdown；所有锁、thread-local/context token 和文件句柄均须释放，且 mutation 不因 transport 重试被重放。

### C. 容量、可观测性与真实验证

1. 先以确定性 fake backend 测量 1、2、…、受限并发数下的排队、吞吐和 coordinator 行为；选择保守上限，不将其作为公开环境变量，除非确有用户配置需求与完整契约。
2. Debug Trace 仅增加或投影 content-free 的 queue/coordination 信息（若确有必要）；不得记录线程 ID、对象 ID、参数、页面内容、路径或 COM payload。
3. 用户在不含 mutation 的 disposable/read-only 本地场景中验证多个独立 read 的实际并发、稳定性与 OneNote 负载；如后续调度改动影响 mutation 路径，则为相关 mutation 补充并由用户执行具名 human-gated scenario。
4. 以实际测试确认该优化只提升多个独立 read 的并发吞吐，不将其宣称为 TODO 045 的单次 Copy/Move 加速手段。

## 非目标与安全边界

- 不在单次 mutation 的内部阶段并行化 COM 调用，不缩短或释放 Move 的独占保护窗口；
- 不让 read 绕过已活跃或等待中的 writer，不改变 writer-preference、generation 或 cache invalidation；
- 不建立全局跨调用 snapshot、隐式读 cache 或跨 MCP 进程协调；
- 不根据工具名称、调用参数或用户声称的“只读”推断并发资格；
- 不自动执行真实 OneNote 或 mutation 验证。

## 完成定义

- [ ] 单一有界 dispatch 边界与共享读资格矩阵已实现，并有公开/设计文档说明；
- [ ] 确定性测试证明合格 read 可并发、writer-preference 保持、所有 exclusive operation 与 read/mutation 不并行；
- [ ] 并发下 ContextVar、trace correlation/tool-call ID、backend counter、audit 与 writer 生命周期均保持调用隔离；
- [ ] 超时、取消、异常、worker 饱和和 shutdown 测试证明不会泄漏锁/线程/句柄，也不会重放 mutation；
- [ ] 用户确认只读 disposable/local 场景的实际并发与稳定性；若实施触及 mutation 调度，另有相应 human-gated 证据；
- [ ] 明确记录单次 Copy/Move 仍由 TODO 045 处理，未因本项降低 strict readback 或删源门。

## 关联

- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：现有共享读/独占写协调基线。
- [TODO 044](044_mcp_runtime_debug_tracing.md)：并发下的 content-free tool-call/correlation 取证要求。
- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：单次 Copy/Move 内部回读优化，与跨调用读并发分离。
- [Operation Runtime](../design/operation_runtime.md)：operation catalog 的 coordination mode 与 Runtime 执行边界。
