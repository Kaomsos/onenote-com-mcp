# 046：按资源作用域的 Mutation 协调与细粒度写锁

> ID：046
> 状态：待办
> 优先级：P3
> 类型：并发 / Mutation 安全 / 层级缓存一致性 / OneNote COM / 性能
> 更新日期：2026-08-20

## 决策摘要

当前 `ReadWriteCoordinator` 是单 MCP 进程内的一把全局读写锁：所有 mutation 从取得协调权起直到收敛、后置条件和 finalize 完成前均独占；纯读可共享。这个模型优先保证 OneNote live state、cache generation、Copy/Move 的 source-drift 与删源验证不会被其他调用穿插。

本项仅评估在已证明存在跨作用域 mutation 排队成本后，是否能按精确资源作用域减少不必要的等待。优先级为 P3：OneNote COM 是否支持并行 mutation、完整调用的实际作用域、以及完整层级读取带来的全局观察依赖均未被证明。没有这些证据，不得用“不同 Page/Notebook”推断可以并行写入。

细粒度写锁不得独立于细粒度失效判断推进。每个 mutation 的 lock footprint 应同时成为其 hierarchy/Page snapshot 与可缓存 read 结果的失效 footprint；否则两个表面上无冲突的写入仍可能让后续 read 使用陈旧状态。TODO 045 已完成 operation-local、按 mutation epoch 失效的 snapshot 优化；任何更复杂的跨层级、跨调用或按资源子树复用 cache 的方案现统一并入本项，与层级化写保护锁从同一份 verified footprint 派生。原 TODO 024 的 TTL Search/Query cache 已取消；没有新的量化证据时，本项也不实现它。

## 当前边界与缺口

- 协调器以单个 `_writer`/`_readers` 状态管理整个进程，没有 Notebook、Section 或 Page 级锁；
- `move_page`、Copy/Move 容器、重排、reparent、删除和 batch mutation 的真实 footprint 往往包含多个 source、destination parent、层级位置和收敛观察，不能只按传入的一个 ID 上锁；
- `get_hierarchy` 的 root 级快照与 mutation 后收敛可能观察到广泛拓扑变化；按资源拆锁不能使这类观察自动安全；
- 当前锁不跨 MCP 进程，细粒度锁也不能取代用户在其他客户端/进程内改变 OneNote 的 drift/confirmation 防线。

## 工作范围

### A. 先建立是否值得拆锁的证据

1. 在 content-free trace/测试 ledger 中区分协调等待、handler 执行、后端等待与收敛等待；不得只从总耗时推断锁竞争。
2. 用确定性并发 fake 测试和用户本地受控观察，记录全局写锁导致的实际排队场景、等待时长与涉及的 operation 类别；真实 mutation 验证仍只能由用户执行。
3. 为每个候选 mutation 建立完整 read/write footprint 表：精确 source ID、destination parent、受影响 subtree、排序/parent relation、全局 hierarchy 观察和不可安全拆分的 COM 操作。
4. 若主要瓶颈仍是单次调用内部 COM/readback（例如 TODO 045），本项保持待办，不以拆锁替代单调用优化。

### B. 作用域锁模型（仅在 A 证明后设计）

1. 锁声明必须在 mutation 执行前由已验证的 exact ID 与 typed resource relation 导出；不得按名称、模糊搜索结果或调用方声称的作用域加锁。
2. 多资源 mutation 必须一次声明并按稳定 canonical order 获取其完整 footprint；禁止锁升级、临时反向获取或“先锁 source、后猜 destination”，以避免死锁和验证窗口。
3. 对无法预先完整声明 footprint、需要 root hierarchy 观察、跨多个受影响 parent 的操作，保留全局独占 fallback；不得为提高并发而遗漏 topology lock。
4. 只有经平台证据证明互不冲突的作用域可并行时，才允许其同时进入 backend；必要时保留全局 COM/bridge gate，即使上层 footprint 锁不同。
5. mutation invalidation、operation generation、timeout、异常释放、partial/indeterminate 分类与 Copy-before-delete 语义必须与现有全局锁等价。锁 footprint 与 cache/snapshot invalidation footprint 必须从同一份已验证的 mutation footprint 导出，不能各自猜测作用域。

### C. 渐进接入与验证

1. 先选择 footprint 单一、没有全局 hierarchy/reorder 依赖且可用 fake backend 证明的一个低风险 mutation；Copy/Move、reparent、重排、容器与 batch 不作为首个接入点。
2. 覆盖同作用域互斥、重叠作用域互斥、无冲突作用域的条件并发、writer 优先、timeout、取消、异常释放、cache invalidation 与锁序死锁回归。
3. 在任何宣称性能收益前，比较旧全局锁与候选锁下的相同 deterministic workload；报告等待时间、吞吐量和安全不变量，均不得包含对象 ID、标题或内容。
4. 若实施影响真实 mutation 时，为每个接入 operation 补充具名 manual-validation scenario；由用户在 disposable 数据上验证并发拒绝/串行与无冲突路径，Agent 不执行真实 scenario。

### D. 层级化 snapshot/cache 与写保护共同设计

1. 不单独新增 Copy/Move fast cache、容器批量 cache 或隐藏的跨调用 snapshot；候选复用必须先具有与锁相同的 typed resource/subtree footprint。
2. 锁 footprint、cache key scope 和 mutation invalidation footprint 必须由同一份 verified hierarchy relation 导出；任何一项只能证明到 Notebook/root hierarchy 时，三者都回退到全局作用域。
3. 外部 OneNote 客户端造成的变化不受进程内锁保护，因此跨调用 cache 仍须有 live generation/drift 证明和 fail-closed miss；锁命中不能被当成 cache freshness 证明。
4. 只有 A 节证明锁竞争或重复层级读取仍是实际瓶颈后，才设计这一层；TODO 045 已确认当前单次 Copy/Move 无需为性能单独推进该复杂度。

## 非目标与安全边界

- 不并行化单次 Move/Copy 内的创建、回读、删源或收敛步骤；
- 不以不同 Notebook/Page 名义绕过 exact ID、confirmation、source drift、CopyBudget 或 mutation policy；
- 不承诺跨 MCP 进程、跨 OneNote 客户端或跨机器的互斥；
- 不因为锁细化而假设 OneNote COM 的 mutation 可重入或线程安全；
- 不在没有 footprint 与平台证据时替换当前全局独占模型。

## 完成定义

- [ ] 已冻结候选 operation 的锁竞争基线、完整 read/write footprint 与不可拆分全局观察；
- [ ] 细粒度锁模型具有 canonical 获取顺序、全局 fallback 与所有异常/timeout 的无泄漏释放合同；
- [ ] 确定性并发测试证明重叠 mutation 永不并行、无冲突 mutation 仅在平台 gate 允许时并行，且现有失败/删源语义不变；
- [ ] 若实际接入 mutation：相关设计、公开契约、自动化合同和具名 human-gated scenario 已同步，且用户确认 disposable 真实证据；
- [ ] 若接入层级化 snapshot/cache：其 key、lock 与 invalidation footprint 同源，无法精确证明时统一回退到全局作用域，外部 drift 继续 fail closed；
- [ ] 若证据表明单次 COM/readback 才是主要瓶颈，记录结论并保持全局锁，不为了完成 TODO 而强行拆锁。

## 关联

- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：当前进程内读写协调、收敛与对账的已完成基线。
- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：已完成单次 Copy/Move 的 phase-local hierarchy/Page snapshot；更复杂的层级化 cache 已从 045 移交本项，与写锁/失效 footprint 共同设计。
- [TODO 024](024_search_and_query_read_snapshot_cache.md)：已取消的 TTL Search/Query cache 方案；未来若有新证据，只能通过本项的 lock/invalidation footprint 先行审查，必要时另建窄范围工作项。
- [Operation Runtime](../design/operation_runtime.md)：operation coordination、generation 与 Outcome 模型。
