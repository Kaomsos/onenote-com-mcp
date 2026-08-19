# 常见问题与故障排查

[English](../../en/user-guide/faq.md) | [文档首页](../../README.zh-CN.md)

## 一般问题

**支持 OneNote 网页版 / UWP 版 / macOS 吗？**
不支持。服务器依赖 OneNote COM API，只支持 Windows 上的 Microsoft OneNote Desktop。旧版 Windows 10 UWP 应用不暴露 COM。

**笔记内容会离开我的电脑吗？**
不会。全部访问都是本地 COM。没有 Graph、Azure、OAuth、遥测或远程处理。审计日志是 content-free 的。

**能直接编辑 `.one` 文件吗？**
不能，这是刻意的设计。所有读写都通过 COM 经由 OneNote 应用完成。

**必须安装 OneMore 吗？**
只有当你想在创建/追加 Page 时把富 Markdown 编译成 OneNote HTML 才需要。没有 OneMore 时，纯文本和已验证 HTML 仍可用。可用 `LOCAL_ONENOTE_MARKDIG_DLL` 指定自定义 assembly 位置。

## 安装配置问题

**`health_check` 报告未就绪。**
就绪要求同时存在运行中的 `ONENOTE.EXE` 和可见的 OneNote 顶层窗口。手动启动 OneNote Desktop 并保持可见；或启用 `UI Control` 门后调用 `launch_onenote_gui()`，再次 `health_check`。服务器绝不隐式启动 OneNote。

**mutation 工具返回 policy 错误。**
对应授权门处于关闭状态（默认）。在 MCP 客户端配置中把相应 `LOCAL_ONENOTE_ENABLE_*` 变量设为 `true` 并重启客户端。注意门的组合关系（见[配置](configuration.md)）——例如 Page 创建需要 Create + Writes；Move 需要 Create + Writes + Deletes。

**改了环境变量没有生效。**
policy 在服务器启动时读取一次。重启 MCP 客户端（它会重新拉起服务器）。

**大 Notebook 上工具超时。**
调大 `LOCAL_ONENOTE_MCP_TIMEOUT`（秒），以及客户端自身的工具超时。预算类拒绝是结构化错误，不是超时——见下一条。

**批量或搜索因预算错误失败。**
这是有意的有界工作量设计，不是故障。缩小范围（更小的子树、更少的 item、更短的内容），或调大[配置](configuration.md)中列出的对应预算变量。

## 行为问题

**为什么 Move 之后 Page 的 ID 变了？**
Move 是重建式的：先验证 Copy，再非永久删除源。新对象获得新 ID。指向旧对象的外部链接无法保持身份。

**Copy/Move 会保留 revision marker 和原始时间吗？**
不会。Copy/Move 会重建目标，明确不继承 source revision/authorship marker 或原始创建/修改时间；OneNote 可以生成目标自己的值。`lossless=true` 只覆盖文档声明支持的标题、内容、对象和拓扑投影。

**删除了对象——还能找回吗？**
公开删除始终非永久。到 OneNote 回收站（笔记本 → 已删除的笔记）查看。永久删除工具不对外发布。

**为什么 `search_pages` 搜不到我刚写的 Page？**
`search_pages` 使用 OneNote 的实时索引，索引异步更新，最近写入的内容可能尚未入索引。Query 工具（层级元数据）不依赖索引。

**为什么 Copy 因不支持的对象被拒绝？**
Copy 保真采用 allowlist 且证据绑定：对象类型只有在真实后端验证证明无损往返后才被接受。未验证类型 fail closed 而不是静默降级。当前边界：[copy content exclusions](../../../docs/lesson/copy_content_type_exclusions.md)。

**为什么不能排序 SectionGroup？**
观察到的后端只为 SectionGroup 暴露固定名称顺序，而非稳定可变的兄弟顺序，因此该能力被明确拒绝。

**`request_notebook_sync` 返回 ok 但 Notebook 没同步完。**
该工具证明同步请求被接受，不证明同步完成。可见的同步活动也不等于同步完成——这是观察到的 OneNote 行为。

## 报告问题

在 GitHub Issue 中提供复现步骤、Windows/OneNote Desktop 版本，以及可用的结构化错误 envelope。**绝不要把笔记内容、真实对象 ID 或个人文件路径粘贴到公开 Issue 中。**
