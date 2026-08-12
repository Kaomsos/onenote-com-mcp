# 018：在线视频表示与 Copy 保真验证

> ID：018
> 状态：已取消
> 优先级：P2
> 类型：真实后端验证 / Page Copy 内容保真
> 更新日期：2026-08-12

## 取消结论

真实验证已经完成，但项目不再把结果实现为独立 Copy 内容类型或有损 fidelity 合同。当前 Page 对象模型只保留 OneNote 实际公开的普通 `Image` 与 `RichText`；在线视频专属 capability、comparator、recipe、bootstrap 和 consumer 已删除。

TODO ID 按台账规则保留且不得复用。真实观察、环境范围和“无法无损复制播放器语义”的限制只维护在 [`lesson/online_video_copy_loses_player_semantics.md`](../lesson/online_video_copy_loses_player_semantics.md)，当前 Copy 行为以 [`design/tool_contracts.md`](../design/tool_contracts.md) 为准。

## 决策边界

- 不制造 `kind=OnlineVideo` 或同名复合 capability；
- 不因 RichText 中出现特定 anchor 属性而选择专属 verification tier；
- 普通 Image/RichText 必须通过既有严格 read-back 才能满足共享 Copy 合同；
- Move 不增加独立类别门，只消费 `copy_contract_satisfied`。
