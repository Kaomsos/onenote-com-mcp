# OneNote Mutation Readiness 不能由只读 Preflight 证明

> 状态：当前有效的工程经验
> 观察日期：2026-08-13
> 范围：Windows OneNote Desktop、本地 COM、disposable Page Reparent fresh/cache 人工验证
> Canonical 设计：[`../design/mutation_readiness_and_call_design.md`](../design/mutation_readiness_and_call_design.md)
> 当前 Tool 契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)
> 后续实施：[`../todo/029_mcp_mutation_readiness_and_reconciliation_hardening.md`](../todo/029_mcp_mutation_readiness_and_reconciliation_hardening.md)

## 结论

OneNote COM 中“对象可完整读取”和“底层 native mutation 当前可接受”是两个不同状态。`SyncHierarchy` 返回、稳定 hierarchy snapshot、完整 Page XML 或文件存在都不能作为 mutation-ready 的完成证明。平台没有只读 readiness predicate，因此生产 tool 应建模“是否足以安全尝试一次”，再用单次 execute 后的 reconciliation 判断真实结果，而不是伪造 `mutation_ready=true`。

受控 disposable fixture 可以用 `CloseNotebook(false) → exact-path reopen → ID rebind → full live validation` 建立更强的持久化 checkpoint。该 checkpoint 解决了本次环境中的 Page Reparent 失败，但它有生命周期副作用，不能被生产业务 tool 隐式施加给用户 Notebook。

## 真实观察

本经验来自用户在同一当前环境中显式运行并保留的隔离证据。Agent 没有启动真实 scenario，也没有从 mock 推导后端行为。

1. `run-2026-08-13-21-09-17` 中，fresh Page fixture 的完整 COM hierarchy 和 Page evidence 已可读取，前置 `SyncHierarchy` 也返回成功；首次 `UpdateHierarchy` 仍失败，本地持久化结构尚未包含完整目标 Section。由此可知 logical COM visibility 不证明 native mutation readiness。
2. `run-2026-08-13-21-12-37` 中，materialized working Notebook 的 `SyncHierarchy` 同样返回成功；首个 Section 的 absolute/parent-relative open 虽返回一致 ID，global 与 exact-self 证明仍不能收敛。请求 accepted 不证明 working hierarchy active。
3. `run-2026-08-13-21-33-17` 中，fresh Recipe 改为正常关闭、原路径重开、typed ID/evidence 重绑和完整 live validation后，Page Reparent、ID remap、内容验证、恢复与最终关闭全部通过。
4. `run-2026-08-13-21-37-14` 中，新 recipe fingerprint cold-build 经过相同 checkpoint 后发布 template，再 materialize working copy；结构/evidence 重绑、业务 Reparent、恢复、template 未打开与 byte inventory 不变全部通过。

这些对照支持“close/reopen checkpoint 解决了本次 disposable fixture 的持久化窗口”，但不证明所有 OneNote 版本、所有 mutation 或所有用户 Notebook 都必须关闭重开。

## 被推翻的假设

### `SyncHierarchy` success 等于 flush complete

真实对照直接否定了该假设。调用成功只证明请求被 COM 接受，不能证明相关 Section/Page 已进入后续 `UpdateHierarchy` 可操作的持久化来源。

### 连续只读 snapshot 稳定等于 mutation-ready

连续 snapshot 可以排除 confirmation 过期和明显 hierarchy 震荡，是重要的 logical preflight；但 snapshot 观察的是可读逻辑视图，不能观察 OneNote 内部持久化提交点。

### 磁盘文件可以提供廉价 readiness probe

文件存在、大小或 mtime 只能描述外部文件系统状态，不能证明 OneNote 已完成内部 catalog/Section 提交。解析或修改 `.one` 还违反项目边界。固定等待同样没有稳定语义。

### 可以先发一个 no-op mutation 探测

`UpdateHierarchy` 本身有副作用和不确定返回语义。所谓 no-op 仍可能触发内部改写、时钟变化或错误后的未知状态，不能成为安全只读探针。

## 工程推断

在没有 readiness predicate 的 API 上，可靠性不能来自更长 sleep 或更多“看起来稳定”的读取，而应来自正确的状态分层：

```text
logical_ready：足以安全尝试一次
persistence_checkpointed：受控 lifecycle 后可重新打开并完整验证
applied/not_applied/partially_applied/indeterminate：execute 后的真实状态分类
```

`persistence_checkpointed` 仍不是对下一次 mutation 的绝对保证；外部并发、OneNote 内部状态和后端错误仍可能介入。只有 operation-specific postcondition 能证明 `applied`，只有完整 frozen pre-state 才能证明 `not_applied`。

## 当前设计决策

- 生产 mutation preflight 只能声明 `logical_ready`，不能声明 `mutation_ready=true`。
- 生产 `reparent_page` 不使用 `SyncHierarchy`、sleep、filesystem probe 或隐式 close/reopen建立 readiness。
- 主 Reparent mutation 只尝试一次；异常后读取实际状态，不根据异常本身推断未应用。
- 完整 postcondition 成立时可以把“execute 报错但已应用”收敛为成功；partial/indeterminate 禁止盲目重试。
- 只有 disposable 或未来由用户显式授权的 lifecycle 能力可以执行 close/reopen checkpoint；该能力不能隐藏在业务 tool 内。
- 最内层 HRESULT 用于分类与恢复建议，PowerShell wrapper HRESULT 不能单独决定重试。

当前权威状态模型和调用顺序见 [`mutation_readiness_and_call_design.md`](../design/mutation_readiness_and_call_design.md)。本 Lesson 解释证据和错误假设，不定义公开字段；尚未实现的生产 tool 加固由 [TODO 029](../todo/029_mcp_mutation_readiness_and_reconciliation_hardening.md) 跟踪。

## 适用边界

本结论来自单一 Windows/OneNote/Office 环境中的 disposable Page Reparent 对照。它足以否定“这些 preflight 普遍构成完成证明”，但不能推出其他版本的内部时序、固定等待值或所有 mutation 都需要 checkpoint。未来跨版本证据应继续记录环境范围，不能把本次成功提升为平台保证。
