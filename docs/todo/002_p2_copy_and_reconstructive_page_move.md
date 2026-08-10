# P2 四层 Copy 与 Page Move

> ID：002
> 状态：进行中
> 优先级：P2
> 更新日期：2026-08-09

## 目标

在不增加 Graph、云端 API 或文件系统级 `.one` 克隆的前提下，使用 OneNote COM 的完整 Page XML 读写能力实现：

1. Page 完整缩进子树 Copy；
2. Section、SectionGroup、Notebook 的递归 Copy；
3. Page Move：Copy、验证目标内容与结构，再将源 Page 子树非永久移入回收站。

Copy 允许尽力保留并显式报告不支持或尚未实测的内容。Move 的语义天然是重建，不保持 Page ID，也不扫描复制范围外的入站链接。

## 当前状态

- 7 个 plan/execute 工具、无状态 `plan_digest`、Copy 预算、独立策略开关和部分失败返回已实现；
- Page XML 转换器会去除源身份/时钟/路径状态、保留稳定根属性与 PageSettings、改写复制范围内的已识别 ID 引用；未知根节点或未知后代节点会使所属顶层内容块整体省略并生成结构化 issue；
- 四层递归创建、Page 顺序/相对层级恢复、内容回读比较和 Move 叶到根回收已实现；
- 自动测试覆盖四层执行、计划过期、预算、权限、未知 XML、二进制 hash、链接改写、分层 Page 回读、部分失败和非永久源删除门；`tests/manual_validation/run.py` 的五个扁平显式场景已实现。四个 Copy 场景和 `move-page` 均自动准备严格富内容父页，以及包含三个编号/项目符号与 To Do 标签混合项的语义子页；Runner 会独立重算 `id_map`/拓扑并持久化成功或部分失败 envelope；
- Move 的验收关口已调整为：逐 Page 的 `DeleteHierarchy(permanently=false)` 完成通用有界回读，且 manual scenario 的 after snapshot 证明整棵源子树不再处于活动 hierarchy。真实环境已观察到 OneNote UI 能显示已删除页面、但 COM 回收站 hierarchy 不返回其旧 ID，因此 `is_in_recycle_bin=true` 降为可选诊断证据，不再阻塞成功；
- 用户已在保留现场中确认 `Outline/Image/RichText/Table` 以及 `List/Tag` 的 Page Copy 在 UI 中一致；后者会被 COM 重编号并重排布局，不能用 canonical XML 相等作为唯一保真证明。六类均已进入静态保真 allowlist，List/Tag Page 改用 `semantic_list_tag` 比较可见文本、二进制、列表种类、标签类型和完成状态，同时保留 strict 差异作诊断。五个统一 fixture 场景的最终成功闭环仍需用户分别复跑，尤其 Section、SectionGroup、Notebook 和最终 Move 尚未完成真实验证，因此 P2 不能标记完成。

## 发布门

1. 用户在专用、可丢弃 Notebook 中显式复跑 `copy-page --keep-worksite`，确认严格父页的文本、格式、表格、图片，以及语义子页三个混合 List/Tag 项的列表种类和完成状态；核对 `copy_report.page_results` 分别为 `strict_canonical` 与 `semantic_list_tag` 后，按 `worksite.json` 的精确 ID 人工清理；
2. 用户分别运行 Section、SectionGroup、Notebook 场景，确认每个容器中的同一双页 fixture、递归结构、目标残留和清理边界；
3. 只有已确认的内容类型才能加入静态保真 allowlist；
4. 用户最后运行 `move-page`，确认 old→new ID、目标内容、源对象已从活动树消失且删除调用保持 `permanently=false`；可在 OneNote UI 的“已删除的笔记”中额外检查或清理源对象，但回收站可见性不是自动验收关口；
5. 每次真实结果记录 OneNote 版本和 Office channel，失败时保留全部证据和目标对象，不自动继续其他场景。

## 完成定义

- 五个具名场景均由用户确认通过；
- 已验证内容类型进入保真 allowlist，未验证类型继续产生 warning 且阻止 Move 删除源；
- README、对象—操作矩阵和设计文档记录实测范围，不将单一 Office 版本的结果写成普遍 COM 保证；
- 默认测试、CI、hook、安装脚本和后台进程均不能触发真实 Copy/Move mutation；
- TODO 索引与本文件状态同步更新为“已完成”。
