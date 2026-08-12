# Online Video Copy：缩略图相等不代表播放器语义保真

> 状态：当前有效的局限性记录
> 观察日期：2026-08-12
> 范围：Windows OneNote Desktop、本地 COM、Page XML reconstruction
> 当前 Copy/Move 契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)
> 当前对象模型：[`../design/object_model.md`](../design/object_model.md)
> 工作记录：[`../todo/018_online_video_copy_fidelity_validation.md`](../todo/018_online_video_copy_fidelity_validation.md)

## 真实观察

在 OneNote/Office `16.0.20228.20158` x64、Windows build `26200`、中文区域/中国标准时间的单一环境中，“插入 → 在线视频”没有公开独立的 `kind=OnlineVideo` 或 `kind=MediaFile`。公开对象主要是 Image 和结构节点，RichText markup 含稳定的 `a[href,v]`；span 和 OE 数量会在不同序列化中变化。

用户执行的隔离同 Section Copy 中，机器证据显示目标创建、内容写入和排序都完成，源未触碰；可见文本、对象签名和图片 binary SHA-256 均相等，没有已知 XML 内容被省略，但 canonical XML 不等。用户确认目标预览图片不可点击，也不能生成或播放 HTML 播放器；同时，外部 RichText 文本链接仍保留且可点击。

本文不记录实际 URL、页面正文、Notebook 名称、对象 ID、用户路径或二进制内容。

## 可复用结论

当前观察到的 Page 对象只有普通 `Image` 与 `RichText`；OneNote 没有公开独立的在线视频 XML 节点或 `PageContentObject.kind`。`a[href,v]` 只是 RichText markup，不足以建立新的对象类型或稳定的 Copy capability。

Page XML reconstruction 可以保留预览图、文字和外部链接，却会丢失图片上的播放器绑定。因此外观、图片 binary 和链接相等都不能证明在线视频无损复制。该内容在当前环境中不能满足项目的 lossless Copy 合同。

## 当前设计影响

- Copy 模型不定义 `OnlineVideo` kind、capability、allowlist 项、verification tier 或 comparator；
- `a[href,v]` 不触发特殊分类，内容仍按普通 `Image`/`RichText` 处理；
- read-back 使用既有严格比较，播放器相关 XML 漂移会使 Copy fail closed，不能取得 `copy_contract_satisfied=true`；
- 不保留在线视频专属 bootstrap、cache recipe 或 Copy consumer；
- Move 仍只复用共享 Copy 合同，因此失败的在线视频 Copy 不会获得源删除权限。

该观察只适用于上述环境和写入路径，不构成所有 OneNote 版本都无法复制在线视频的普遍断言。若未来真实后端能够用普通 Image/RichText 路径通过完整 lossless read-back，可按普通内容重新评审，但不得预先恢复虚拟在线视频类型。
