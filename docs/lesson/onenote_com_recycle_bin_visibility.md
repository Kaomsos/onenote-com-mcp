# OneNote COM 回收站可见性不是可靠的删除验收关口

> 状态：当前有效的工程经验<br>
> 观察日期：2026-08-09<br>
> 范围：Windows OneNote Desktop、本地 COM、隔离的 reconstructive Page Move 人工验证<br>
> 当前契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)<br>
> 验证流程：[`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md)

## 结论

`DeleteHierarchy(permanently=false)` 成功后，OneNote UI 可以在“已删除的笔记”中显示目标 Page，但 COM hierarchy 即使请求包含回收站，也可能不再返回该 Page 的旧 ID。因此，`is_in_recycle_bin=true` 是有价值的正向诊断证据，却不能作为非永久删除成功的必要条件。

可靠的自动验收边界是：删除调用明确使用 `permanently=false`，并且目标经过有界回读后不再处于活动 hierarchy。对于 reconstructive Move，还必须在删除前完成目标内容与拓扑验证，并在删除后确认整棵源 Page 子树均不再活动。

## 真实观察

一次隔离的 `reconstructive-move-page --keep-worksite` 运行完成了目标 Page 子树复制，随后对源 Page 子树执行非永久删除。人工检查 OneNote UI 时，已删除的 List/Tag 子 Page 能在“已删除的笔记”中看到；但程序经过多次有界 `include_recycle_bin=true` hierarchy 观察，仍无法通过旧 ID 找到该 Page，最终因缺少 `is_in_recycle_bin=true` 而误报 partial failure。

这次观察证明了下面这个组合可以真实出现：

1. 目标副本存在且内容、拓扑已通过既定验收；
2. 源 Page 已从活动树消失；
3. OneNote UI 显示源 Page 位于已删除区域；
4. COM hierarchy 不暴露可与旧 ID 对应的回收站对象。

这里的真实证据来自用户对隔离现场的 UI 确认。自动 pytest 只覆盖调整后的合同分支，不被用来证明 UI 或 COM 的真实行为。

## 工程推断

UI 的“已删除的笔记”视图与 COM hierarchy 不是可以相互替代的同一个观测面。旧 ID 在 COM 中缺失，既可能表示接口只返回活动对象，也可能表示回收站对象使用了不同的投影或身份；现有证据不足以区分这些内部机制。因此当前实现只依赖能够稳定证明的事实——对象是否仍在活动 hierarchy——而不推断 OneNote 内部的最终存储位置或可恢复性。

多次轮询仍无旧 ID、同时 UI 已能看到已删除对象，说明这个失败至少不能统一解释为短暂的 eventual-consistency 延迟。继续增加重试次数不是可靠修复；把回收站可见性降为诊断信息，才不会让不可用的观测面阻塞已经完成的非永久删除流程。

## 不能从 COM 回读推出什么

- `include_recycle_bin=true` 不保证枚举出 UI 中所有已删除对象。
- 旧 Page ID 在活动树中缺失，不足以单独证明对象可通过 COM 恢复，也不足以证明永久删除；它只证明该 ID 当前不再活动。
- 未读取到 `is_in_recycle_bin=true` 不等于删除失败。
- 即使进行更多轮询，也不能把“接口不暴露此对象”的情况转化为正向回收站证据；无界重试只会延长误报。
- 单一 OneNote 构建中的这一现象不能证明所有 Office channel 都具有完全相同的回收站投影行为。

## 曾经采用但不可靠的验收模型

旧模型要求生产执行层逐 Page 轮询回收站标记，manual scenario 再对整棵源子树执行第二层回收站枚举；只有全部旧 ID 同时回读为 `is_in_recycle_bin=true` 才成功。

这个模型把“COM 能否观察到回收站对象”错误地等同于“非永久删除是否发生”。在 UI 与 COM hierarchy 不一致时，它会把已经完成的 Move 报告成 partial failure，同时留下已创建目标和已删除源，反而增加人工判断成本。增加轮询次数无法修复观测能力缺失。

## 当前设计决策

重建式 Move 现在采用以下分层证据：

1. mutation policy 必须允许 Delete，但永久删除权限保持关闭；
2. 每个源 Page 只调用 `DeleteHierarchy(permanently=false)`；
3. 通用删除服务执行有界回读，若对象仍在活动 hierarchy 中则失败；
4. manual scenario 的 `after.json` 独立确认整个源子树不再活动；
5. 若 COM 返回 `is_in_recycle_bin=true`，记录为 `recycle_bin_verification=verified`；
6. 若对象已不活动但 COM 不暴露回收站元数据，记录为 `recycle_bin_verification=not_required_com_unavailable`，不阻塞成功；
7. `--keep-worksite` 继续保留 Notebook 和精确 source/target ID，要求用户在 OneNote UI 中人工检查与清理。

仍然保持 fail closed 的关口包括 Copy fidelity、目标拓扑、源快照重验证、非永久删除参数以及源对象从活动树消失。被移除的只有“必须由 COM 找到回收站旧 ID”这一关口。

## 对测试与排障的启示

- 合同测试应分别覆盖“取得回收站标记”和“源已不活动但回收站元数据不可见”两个成功分支。
- 删除调用失败、对象持续活动或只完成部分源子树删除，仍必须返回 partial failure，并列出 `deleted_source_ids` 与 `remaining_source_ids`。
- `recycled_source_ids` 只能包含明确带回收站标记的 ID；不能为了让结果看起来完整而推断补齐。
- UI 检查可以提升人工置信度，但不应被伪装成 COM 自动化证据。
- 不应使用永久删除、raw XML、直接编辑 `.one` 文件或无界扫描来弥补回收站可见性不足。

## 适用边界

本 Lesson 解释的是 OneNote COM hierarchy 的观测限制，不是对 OneNote 内部存储或恢复机制的完整描述。未来若新的 OneNote 构建、COM API 或可靠的 typed restore 能力提供稳定的回收站对象身份，应重新验证并更新本 Lesson；在此之前，当前公开行为仍以 [`../design/tool_contracts.md`](../design/tool_contracts.md) 为准。
