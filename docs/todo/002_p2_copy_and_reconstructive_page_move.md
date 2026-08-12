# P2 四层 Copy 与 Page Move

> ID：002
> 状态：已完成
> 优先级：P2
> 类型：公开 mutation 契约 / 递归 Copy 与重建式 Page Move
> 更新日期：2026-08-11

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
- 用户已在保留现场中确认 `Outline/Image/RichText/Table` 以及 `List/Tag` 的 Page Copy 在 UI 中一致；后者会被 COM 重编号并重排布局，不能用 canonical XML 相等作为唯一保真证明。六类均已进入静态保真 allowlist，List/Tag Page 改用 `semantic_list_tag` 比较可见文本、二进制、列表种类、标签类型和完成状态，同时保留 strict 差异作诊断。用户随后明确确认 `copy-page`、`copy-section`、`copy-section-group`、`copy-notebook` 与最终 `move-page` 五个统一 fixture 场景均已完成真实成功闭环。
- 2026-08-11 的补充真实回归进一步扩展容器覆盖：`run-2026-08-11-21-33-01` 与 `run-2026-08-11-21-36-13` 分别证明 Section/SectionGroup 在 Notebook 内部和跨 Notebook 两种目标下均 `verified=true`、`lossless=true`，并完成反向 cleanup、双 Notebook restore/close；`run-2026-08-11-21-31-17` 证明 Notebook Copy 同时保留根 Section 富内容子树和新增 SectionGroup/Section/Page 子树，7 个对象全部精确映射。目标 Notebook 通过刷新后的 exact-ID confirmation 一次关闭并按 `closed_not_deleted` 保留，未再出现 stale `modified` confirmation mismatch。

## 已满足的发布门

1. 用户在专用、可丢弃 Notebook 中显式运行并确认 `copy-page` 的严格父页与 List/Tag 语义子页；`copy_report.page_results` 分别按 `strict_canonical` 与 `semantic_list_tag` tier 通过；
2. 用户分别运行并确认 Section、SectionGroup、Notebook 场景中的同一双页 fixture、递归结构、目标残留和清理边界；
3. 只有已确认的内容类型才能加入静态保真 allowlist；
4. 用户最后运行并确认 `move-page` 的 old→new ID、目标内容、源对象活动态缺席以及 `permanently=false`；回收站 UI 可见性只作为诊断，不作为自动验收关口；
5. 真实结果和本地 evidence 均按失败保留边界处理。根据 TODO 007 的当前决策，环境版本/channel 元数据保持可选且非阻塞，单环境结论不外推为普遍 COM 保证。

## 完成定义

- 五个具名场景均由用户确认通过；
- 已验证内容类型进入保真 allowlist，未验证类型继续产生 warning 且阻止 Move 删除源；
- README、对象—操作矩阵和设计文档记录实测范围，不将单一 Office 版本的结果写成普遍 COM 保证；
- 默认测试、CI、hook、安装脚本和后台进程均不能触发真实 Copy/Move mutation；
- TODO 索引与本文件状态同步更新为“已完成”。

## 完成状态

2026-08-10，用户明确确认上述五个具名场景已经全部完成真实 OneNote 验收。代码、自动化合同、静态保真门、严格非永久源删除、当前文档和用户确认的真实证据均满足完成定义，因此本 TODO 正式标记为“已完成”。未验证内容继续阻止不满足保真门的 Move 删除源。2026-08-11，TODO 004 又完成 InkDrawing、UIShape 与录像 MediaFile 的隔离 Copy 取证并将其加入静态保真 allowlist；2026-08-12，InsertedFile 的 strict canonical Copy 和人工打开附件确认也完成并加入同一集合。FileAttachment/MeetingInfo 已删除专属验证入口并保留排除原因。Embedded Spreadsheet（内嵌电子表格）也按当前产品范围明确 unsupported；因尚无公开 `kind`/XML 证据，不建立别名或专属入口。

2026-08-11，用户又完成增强后的三个容器场景：Section 与 SectionGroup 补齐同 Notebook/跨 Notebook 双 case，Notebook 补齐嵌套 SectionGroup 子树和最新 `modified` 关闭确认。三次 run 顶层均为 `passed`、均未打开 cache template 且 inventories unchanged；这是对已完成结论的增强回归，不改变 TODO 状态。
