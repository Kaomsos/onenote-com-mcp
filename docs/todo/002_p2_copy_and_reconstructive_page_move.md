# P2 四层 Copy 与 Page 重建式 Move

> ID：002
> 状态：进行中
> 优先级：P2
> 更新日期：2026-08-05

## 目标

在不增加 Graph、云端 API 或文件系统级 `.one` 克隆的前提下，使用 OneNote COM 的完整 Page XML 读写能力实现：

1. Page 完整缩进子树 Copy；
2. Section、SectionGroup、Notebook 的递归 Copy；
3. 仅在目标内容与结构验证通过后将源 Page 子树移入回收站的重建式 Move。

Copy 允许尽力保留并显式报告不支持或尚未实测的内容。重建式 Move 不保持 Page ID，也不扫描复制范围外的入站链接。

## 当前状态

- 7 个 plan/execute 工具、无状态 `plan_digest`、Copy 预算、独立策略开关和部分失败返回已实现；
- Page XML 转换器会去除源身份/时钟/路径状态、保留稳定根属性与 PageSettings、改写复制范围内的已识别 ID 引用；未知根节点或未知后代节点会使所属顶层内容块整体省略并生成结构化 issue；
- 四层递归创建、Page 顺序/相对层级恢复、内容回读比较和 Move 叶到根回收已实现；
- 自动测试覆盖四层执行、计划过期、预算、权限、未知 XML、二进制 hash、链接改写、部分失败和回收站门；`tests/manual_isolated/run.py` 的五个显式场景已实现，`create` 会幂等准备富文本、表格、图片 fixture，并在 manifest 中列出附件/墨迹/媒体的人工准备要求；Runner 会独立重算 `id_map`/拓扑并持久化成功或部分失败 envelope；
- 尚未执行真实 OneNote Copy/Move 场景，因此富内容类型的保真 allowlist 仍为空，P2 不能标记完成。

## 发布门

1. 用户在专用、可丢弃 Notebook 中显式运行 `copy-page`，确认文本、格式、表格和图片；
2. 用户分别运行 Section、SectionGroup、Notebook 场景，确认递归结构、目标残留和清理边界；
3. 只有已确认的内容类型才能加入静态保真 allowlist；
4. 用户最后运行 `reconstructive-move-page`，确认 old→new ID、目标内容以及源对象只进入回收站；
5. 每次真实结果记录 OneNote 版本和 Office channel，失败时保留全部证据和目标对象，不自动继续其他场景。

## 完成定义

- 五个具名场景均由用户确认通过；
- 已验证内容类型进入保真 allowlist，未验证类型继续产生 warning 且阻止 Move 删除源；
- README、对象—操作矩阵和设计文档记录实测范围，不将单一 Office 版本的结果写成普遍 COM 保证；
- 默认测试、CI、hook、安装脚本和后台进程均不能触发真实 Copy/Move mutation；
- TODO 索引与本文件状态同步更新为“已完成”。
