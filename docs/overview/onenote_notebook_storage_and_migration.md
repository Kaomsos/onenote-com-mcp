# OneNote 笔记本的本地存储、云端表示与双向迁移

> 评估范围：Windows 桌面版 OneNote、OneDrive 与 SharePoint 中的现代 OneNote 笔记本。
> 最近核对：2026-08-06。
> 文档性质：存储与迁移概览，不替代本项目的工具契约或人工验证流程。

## 1. 结论先行

OneNote 的“笔记本”是逻辑容器，不是始终具有同一种磁盘表现的普通文件。理解其存储方式时，必须区分以下四类数据：

1. **活动笔记本的权威存储**：纯本地笔记本是一个目录树；云端笔记本的权威版本位于 OneDrive 或 SharePoint。
2. **OneNote 私有离线缓存**：云端笔记本在设备上的工作副本，由 OneNote 管理，不是可移植的笔记本目录。
3. **备份**：OneNote 定期生成的恢复副本，与活动笔记本和缓存分离。
4. **导出物**：`.onepkg`、下载的 ZIP 或 PDF 等快照，不会继续与源笔记本同步。

资源管理器里看到的本地笔记本目录和 OneDrive 目录里的 `.url` 并不矛盾：前者可以是权威存储，后者只是云端笔记本的打开入口。

```text
纯本地笔记本

OneNote ──直接读写──> Notebook 目录（权威存储）
                       ├─ Open Notebook.onetoc2
                       ├─ Section A.one
                       └─ Section Group\Section B.one
```

```text
云端笔记本

OneDrive 目录中的 .url ──打开──> OneNote
                                  ├──私有缓存（设备工作副本）
                                  └──OneDrive/SharePoint package（权威存储）

备份与导出物位于这条同步链路之外。
```

## 2. 纯本地笔记本如何存在

Windows 桌面版 OneNote 的纯本地笔记本通常是一个目录树：

```text
My Notebook\
├─ Open Notebook.onetoc2
├─ 工作.one
├─ 生活.one
└─ 项目\                         ← SectionGroup
   ├─ 项目 A.one                 ← Section
   ├─ 项目 B.one                 ← Section
   └─ 已归档\                    ← 嵌套 SectionGroup
      └─ 历史项目.one            ← Section
```

对应关系如下：

| OneNote 概念 | 常见本地表示 |
| --- | --- |
| Notebook | 整个目录树 |
| SectionGroup | 子目录 |
| Section | `.one` 文件 |
| Page | 所属 Section 的 `.one` 文件内部的独立 Page 对象空间，不是独立文件 |
| Subpage | 同一个 `.one` 中的 Page；通过页面顺序和缩进层级形成逻辑父子关系 |
| 页面文本、图片和附件 | 通常在 `.one` 的对象与二进制数据中；部分格式可使用配套的 `_onefiles` 目录 |
| Notebook/SectionGroup 目录及顺序信息 | `.onetoc2` revision store |

### 2.1 SectionGroup 的具体磁盘映射

SectionGroup 没有对应的 `.one` 文件。它的主要物理表示是 Notebook 目录下的一个子目录；嵌套 SectionGroup 则继续表现为子目录中的子目录。微软的 SharePoint Migration Tool 文档也以此识别传统本地 Notebook：Notebook 是带根 `.onetoc2` 的目录，SectionGroup 是其中的目录，而每个 Section 是相应目录下的 `.one` 文件。

```text
Notebook\
├─ Open Notebook.onetoc2
├─ 根级分区.one
└─ Group A\                         ← SectionGroup A
   ├─ A-1.one                       ← A 中的 Section
   ├─ A-2.one
   └─ Group B\                      ← A 中嵌套的 SectionGroup B
      └─ B-1.one
```

这层映射有几个重要含义：

- SectionGroup 可以包含 Section 和其他 SectionGroup，但不能直接包含 Page；Page 必须属于某个 `.one` Section。
- 空 SectionGroup 在内容层面可以只是一个没有 `.one` 的目录；它本身不承载页面正文。
- 文件系统中的父子目录关系表达 SectionGroup 的包含关系，但资源管理器的字母顺序不等于 OneNote 中的标签顺序。
- `.onetoc2` 保存逻辑目录、属性和顺序。不同 OneNote 版本还可能在 SectionGroup 子目录中维护额外的 `.onetoc2` 元数据；这些文件都应由 OneNote 管理，不能根据文件名、数量或所在层级推断新的 Section。
- OneNote 显示名通常反映到目录名，但文件系统非法字符、重名处理和版本差异意味着目录路径不应作为稳定对象 ID。
- 在 OneNote 中移动 Section 到某个 SectionGroup，传统本地存储通常会把相应 `.one` 移入目标子目录，并同步更新 TOC 和内部关系；嵌套、重命名或移动 SectionGroup 也应由 OneNote 完成。

因此，不能把“SectionGroup 是文件夹”理解为可以安全地在资源管理器里任意剪切该文件夹。直接文件级移动可能绕开 TOC、对象身份、内部链接和 OneNote 当前打开状态的更新。复制或迁移完整 Notebook 时，应保留所有 SectionGroup 子目录和 `.onetoc2` 元数据；结构性编辑则使用 OneNote UI、COM 或其他受支持的 OneNote 接口。

### 2.2 Page 与 Subpage 的具体磁盘映射

Page 和 Subpage 都没有对应的独立文件或目录。`.one` 才是磁盘上的 Section 存储单元，一个 Section 中的全部 Page 都位于同一个 `.one` revision store 内。微软格式规范将 Section 定义为页面、页面元数据和页面顺序的容器；在底层，一个 `.one` 可以包含 Section object space、页面序列以及多个相互独立的 Page object space。

下面是便于理解的概念图，不是把 `.one` 当作可以解压的真实目录：

```text
需求.one                              ← 一个 Section revision store
├─ Section metadata / page series
├─ PageObjectSpace: 需求概览           ← Page，层级 0
│  ├─ page ID、标题、时间和修订元数据
│  └─ Outline、文本、图片、附件等内容对象
├─ PageObjectSpace: 接口需求           ← Subpage，层级 1
│  └─ 自己的标题、正文和内容对象
├─ PageObjectSpace: 鉴权细节           ← 更深一层 Subpage，层级 2
│  └─ 自己的标题、正文和内容对象
└─ PageObjectSpace: 测试计划           ← Page，层级 0
```

这里最容易误解的一点是：**Subpage 不是存放在父 Page 正文内部的子对象**。它仍然是拥有独立 Page ID、标题、正文和修订信息的完整 Page，只是与相邻页面一起通过 Section 内的页面顺序和缩进层级形成逻辑父子关系。Microsoft Graph 也将这种关系作为 Section 页面集合的 `pagelevel` 与顺序信息暴露，而不是把 Subpage 作为父 Page 内容的一部分返回。

当前 Windows 桌面版 OneNote UI 支持两级 Subpage。这个限制影响界面中的缩进和折叠行为，但不会让 Subpage 变成额外的磁盘目录；所有层级仍存储在同一个 Section `.one` 中。

对文件变化的影响如下：

| OneNote 操作 | 本地存储层面的结果 |
| --- | --- |
| 在 Section 中新建 Page | 在该 Section 的 `.one` 中新增 Page object space 及相关元数据 |
| 修改 Page 正文 | 更新同一 `.one` 中该 Page 的对象和 revision |
| 调整 Page 顺序 | 更新同一 `.one` 中的页面序列/顺序元数据，不产生文件移动 |
| Page 降级为 Subpage 或提升为主 Page | 更新页面层级和顺序关系，仍是原 Section 中的 Page |
| 在同一 Section 内移动一组 Page/Subpage | 更新同一 `.one` 的页面序列和层级关系 |
| 把 Page 移到另一个 Section | 源 Section `.one` 与目标 Section `.one` 都会变化；这不是资源管理器中的文件移动 |
| 复制整个 `.one` | 复制该 Section 中的全部 Page/Subpage，而不是其中某一页 |

因此，文件系统无法单独复制、移动或删除一个 Page。此类操作必须通过 OneNote UI、COM、Graph 或其他理解 Page object space、页面层级和 revision store 的接口完成。移动主 Page 时还必须明确是否包含其 Subpage 子树；只处理主 Page 可能保留或改变原有 Subpage 的层级关系。

标准活动分区扩展名是 `.one`，不是 `.onex`。一个 `.one` 文件表示一个 Section，并包含其中的页面、页面顺序、元数据和内容对象。微软将 `.one` 与 `.onetoc2` 的底层二进制结构定义为 OneNote Revision Store：它通过相互引用的对象空间、修订和事务日志保存内容，不应当作 ZIP、XML 或普通数据库直接修改。

`.onetoc2` 不是正文的汇总文件。它主要保存 Notebook 或 SectionGroup 层面的目录、成员关系和显示顺序；实际页面内容仍位于相应的 `.one` Section 中。

纯本地模式下，OneNote 直接读写这套目录，因此：

```text
Notebook 目录 = 活动笔记本 = 权威存储
```

仅复制一个 `.one` 文件，只复制了一个 Section，而不是整个 Notebook。复制完整本地笔记本时，应保留整个目录结构，并在复制前关闭笔记本或确认写入已经完成。

## 3. 云端笔记本如何存在

保存到 OneDrive 或 SharePoint 后，权威存储不再是 OneDrive 同步目录中的普通 `.one` 文件树。Microsoft Graph 将云端 OneNote 笔记本表示为具有 `package` facet 的特殊 `driveItem`：它内部由文件和目录组成，但顶层由 OneDrive 和 OneNote 作为特殊 package 处理，而不是普通文件或普通文件夹。

逻辑上可以理解为：

```text
OneDrive / SharePoint
└─ My Notebook            ← 特殊 OneNote package
   ├─ SectionGroup
   ├─ Section
   ├─ Page
   └─ 内容与元数据
```

在本机同步的 OneDrive 目录中，它可能显示为：

```text
My Notebook.url
```

资源管理器隐藏扩展名时，看起来可能仍像一个叫 `My Notebook` 的文件。这个 `.url` 是指向云端笔记本的 Internet Shortcut，只负责让 OneNote 或浏览器定位并打开远端对象；它不包含页面内容，也不能通过复制该文件来备份笔记本。

这是刻意的同步边界。OneNote 自己理解 `.one` 的修订与对象模型，能够只同步变化并合并离线编辑；OneDrive 桌面客户端只负责普通文件同步。让两套同步机制同时处理活动 `.one` 文件容易产生冲突副本、重复 Section 或数据丢失，因此微软明确要求通过 OneNote 移动和同步笔记本，不要通过资源管理器把活动 `.one` 目录拖入 OneDrive、Dropbox 或 Windows Offline Files。

## 4. 云端笔记本的本地缓存

云端笔记本仍然可以离线使用，因为 OneNote 会在每台设备上维护私有缓存：

```text
OneNote 私有缓存  <──OneNote 同步──>  云端 package
    工作副本                           权威存储
```

该缓存具有以下边界：

- 位置和内部布局随 OneNote 版本、安装方式及账户类型变化；
- 不保证表现为可辨认的 Notebook 目录或一组可直接打开的 `.one` 文件；
- 可能包含尚未上传的修改，因此同步错误时不能贸然清除；
- 完全同步后关闭云端笔记本并重启 OneNote，应用可能移除对应缓存；
- 不应直接编辑、复制或解析为长期备份。

因此，`.url`、缓存和云端 package 分别是“入口”“工作副本”和“权威存储”，不能相互替代。

## 5. 备份与导出物

| 类型 | 主要用途 | 是否继续同步 | 是否是活动权威存储 |
| --- | --- | --- | --- |
| 本地 Notebook 目录 | 本地编辑 | 否 | 是 |
| 云端 OneNote package | 跨设备编辑与协作 | 是 | 是 |
| OneNote 私有缓存 | 离线编辑与加速 | 是，由 OneNote 管理 | 否 |
| OneNote Backup | 恢复历史内容 | 否 | 否 |
| `.onepkg` | 运输、归档或导入 | 否 | 否 |
| 网页导出的 ZIP | 下载快照 | 否 | 否 |
| PDF | 只读展示快照 | 否 | 否 |
| OneDrive 目录中的 `.url` | 打开云端笔记本 | 不适用 | 否 |

Windows 桌面版 OneNote 的备份位置可以在“文件 → 选项 → 保存和备份”中配置，默认通常位于：

```text
%LOCALAPPDATA%\Microsoft\OneNote\<版本>\Backup
```

备份目录和私有缓存是两套不同机制。缓存服务于持续同步，备份服务于恢复；不能因为云端笔记本可以离线打开，就认为已经拥有可靠的本地备份。

## 6. 本地 Notebook 上传到 OneDrive 或 SharePoint

### 6.1 前置检查

1. 使用当前受支持的 OneNote for Windows 打开本地 Notebook。
2. 检查所有 Section 和关键页面能够正常打开。
3. 确认 OneNote 已登录正确的个人、工作或学校账户。
4. 对重要数据额外复制一次完整本地 Notebook 目录，并保持目录结构不变。

### 6.2 受支持的迁移方式

当前 OneNote for Windows 通常使用：

```text
文件 → 保存副本 → OneDrive
```

OneNote 2016—2024 的部分界面使用：

```text
文件 → 共享 → 选择 OneDrive/SharePoint → 移动笔记本
```

由 OneNote 执行迁移时，它会：

1. 读取本地 `.one`/`.onetoc2` 目录；
2. 在目标 OneDrive 或 SharePoint 建立 OneNote package；
3. 上传内容并建立 OneNote 自己的同步关系；
4. 可能把原 OneDrive 可见位置替换成 Internet Shortcut；
5. 以后通过 OneNote 私有缓存进行离线编辑和增量同步。

### 6.3 迁移后的验收

1. 在 OneNote 中打开“文件 → 信息 → 查看同步状态”，确认没有错误。
2. 从 OneNote 网页版重新打开目标 Notebook。
3. 核对 SectionGroup、Section 数量和顺序。
4. 抽查含图片、附件、墨迹或大页面的内容。
5. 在验证完成前保留原始本地目录，不要只根据 `.url` 已出现就删除源数据。

### 6.4 禁止的文件级操作

不要把活动 Notebook 目录直接剪切或复制到 OneDrive 同步目录，期待 OneDrive 客户端把它转换成云端 OneNote Notebook：

```text
# 不推荐
Move-Item D:\Notes\MyNotebook "$env:OneDrive\Documents\MyNotebook"
```

普通文件同步不等于 OneNote Notebook 同步，也不会可靠地建立移动端、网页版和协作所需的服务端关系。

## 7. 云端 Notebook 转成本地目录

这个方向不是“下载 `.url`”，而是让 OneNote 将云端 package 物化为新的本地 Notebook。源云端 Notebook 与生成的本地 Notebook 是两个独立副本，后续不会相互同步。

### 7.1 首选：保存副本到“此电脑”

在支持该入口的 OneNote for Windows 中：

1. 打开源云端 Notebook。
2. 强制同步并解决所有同步错误。
3. 选择“文件 → 保存副本”。
4. 选择“此电脑”或“浏览”。
5. 指定一个不受 OneDrive、Dropbox 或 Windows Offline Files 同步的本地目录。
6. 使用与源 Notebook 不冲突的名称保存。
7. 重新打开生成的本地 Notebook，确认它包含 `.onetoc2`、各 Section 的 `.one` 及完整 SectionGroup 目录。
8. 比较 Section 数量并抽查图片、附件和复杂页面。

保存完成后的关系是：

```text
云端 Notebook ──继续同步──> OneDrive/SharePoint

本地 Notebook ──直接读写──> 本地 .one/.onetoc2 目录

两者不再自动传播后续修改。
```

### 7.2 可选：导出 `.onepkg` 后解包

如果当前 Windows 桌面版在“文件 → 导出”中提供 `OneNote Package (*.onepkg)`：

1. 导出范围选择整个 Notebook；
2. 保存为 `.onepkg`；
3. 双击 `.onepkg`，让 OneNote 解包；
4. 指定新的本地 Notebook 名称和目录；
5. 打开解包后的 Notebook 并完成内容验收。

`.onepkg` 是一次性运输包，不是持续同步的活动 Notebook。不同 OneNote 版本的导出界面和可用格式可能不同，应以当前 Windows 客户端实际提供的选项为准。

### 7.3 个人 OneDrive 的网页导出

个人 Microsoft 账户可以在 OneNote 网页版的 Notebook 列表中使用“导出笔记本”，下载一个包含 Notebook 目录的 ZIP。解压后，可以在 Windows OneNote 中打开目录中的 `Open Notebook.onetoc2`。

该途径有明确限制：

- 网页整本导出只适用于个人 OneDrive；
- OneDrive for Business、学校账户和 SharePoint 不支持该网页整本导出；
- 大型 Notebook 可能因空间或服务限制失败；
- 旧 OneNote Notebook Importer 已弃用，不应把它作为长期自动化方案。

### 7.4 工作/学校账户的保底方法

如果工作/学校 OneDrive 或 SharePoint Notebook 没有可用的“保存副本到此电脑”入口：

1. 在 OneNote for Windows 中选择“文件 → 新建 → 此电脑”，创建空的本地 Notebook；
2. 按原结构创建 SectionGroup；
3. 对源 Notebook 的每个 Section 使用“移动或复制”，选择“复制”到本地 Notebook；
4. 等待源 Notebook 同步稳定，同时检查本地目标内容；
5. 只有完成核对后，才考虑关闭或删除任一源对象。

这种方法较慢，但由 OneNote 自己解释和写入页面内容，比复制私有缓存可靠。

## 8. 迁移决策表

| 目标 | 推荐入口 | 结果 |
| --- | --- | --- |
| 本地 Notebook 变成可跨设备同步的云端 Notebook | OneNote“保存副本到 OneDrive”或“共享/移动笔记本” | 新的云端 package 与 OneNote 同步关系 |
| 云端 Notebook 变成可编辑的本地目录 | OneNote“保存副本到此电脑” | 独立的 `.one`/`.onetoc2` Notebook 目录 |
| 保存可恢复的运输快照 | Windows OneNote 导出 `.onepkg`（如果当前版本提供） | 不同步的 package 文件 |
| 下载个人 OneDrive Notebook 快照 | OneNote 网页版“导出笔记本” | ZIP 中的 Notebook 目录 |
| 工作/学校云端 Notebook 无整本本地导出入口 | 新建本地 Notebook，再通过 OneNote 逐 Section 复制 | 独立本地 Notebook |
| 仅需只读归档或分发 | 导出 PDF | 不可继续作为原生 Notebook 编辑 |

## 9. 与本项目实现边界的关系

`local-onenote-mcp` 坚持通过 OneNote 桌面应用及其 COM API 操作 Notebook，而不直接解析或修改 `.one`/`.onetoc2` 二进制文件。这一边界同时适用于本地和云端 Notebook：

- 对本地 Notebook，OneNote 是 revision store 的唯一写入者；
- 对云端 Notebook，OneNote 同时负责私有缓存和服务端增量同步；
- MCP 不应复制、修改或清理 OneNote 私有缓存；
- Notebook 的文件夹复制、上传、下载和删除属于显式生命周期操作，不能由普通 Page/Section mutation 隐式触发；
- 对真实 Notebook 的非只读验证必须继续遵守 [`tests/manual_validation/`](../../tests/manual_validation/) 的 human-gated、isolated 和最小权限要求。

## 10. 权威资料

- [Microsoft Open Specifications：OneNote Revision Store File Format（`.one` 与 `.onetoc2`）](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/ae670cd2-4b38-4b24-82d1-87cfb2cc3725)
- [Microsoft Open Specifications：OneNote File Format](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/73d22548-a613-4350-8c23-07d15576be50)
- [Microsoft Open Specifications：Section](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/1603b29c-1c9f-4e85-b9b9-59684122374a)
- [Microsoft Open Specifications：Example of a Section and Page](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/d88f88e2-92ee-4fbe-ad67-b6dd6603805f)
- [Microsoft Open Specifications：Table of Contents（`.onetoc2`）](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/962bee35-290f-45e7-8c82-b81e375ac0d3)
- [Microsoft Learn：How the SharePoint Migration Tool migrates OneNote folders](https://learn.microsoft.com/en-us/sharepointmigration/migrate-onenote-spmt)
- [Microsoft Learn：Get OneNote content and structure（`pagelevel`）](https://learn.microsoft.com/en-us/graph/onenote-get-content)
- [Microsoft Support：Create a section group in OneNote](https://support.microsoft.com/en-us/onenote/create-a-section-group-in-onenote)
- [Microsoft Support：Create a subpage in OneNote](https://support.microsoft.com/en-US/OneNote/onenote-help-and-learning/create-a-subpage-in-onenote)
- [Microsoft Graph：OneNote Notebook 的 package resource](https://learn.microsoft.com/en-us/graph/api/resources/package?view=graph-rest-1.0)
- [Microsoft Support：Move a OneNote notebook to OneDrive](https://support.microsoft.com/en-us/onenote/onenote-help-and-learning/move-a-onenote-notebook-to-onedrive)
- [Microsoft Support：Sync a notebook in OneNote](https://support.microsoft.com/en-US/OneNote/onenote-help-and-learning/sync-a-notebook-in-onenote)
- [Microsoft Support：Manage notebook storage in OneNote](https://support.microsoft.com/en-us/onenote/manage-notebook-storage-in-onenote)
- [Microsoft Support：How to transfer OneNote notebooks between accounts](https://support.microsoft.com/en-us/onenote/how-to-transfer-onenote-notebooks-between-accounts)
- [Microsoft Support：Export and Import OneNote notebooks](https://support.microsoft.com/en-gb/office/export-and-import-onenote-notebooks-a4b60da5-8f33-464e-b1ba-b95ce540f309)
- [Microsoft Support：Change the default storage location for backup files](https://support.microsoft.com/en-us/onenote/change-the-default-storage-location-for-backup-files)
- [Microsoft Support：OneNote Platform Data Protection & Recovery](https://support.microsoft.com/en-us/office/onenote-platform-data-protection-recovery-39b8cdbe-fa57-49de-a4ac-a38aac2af5c7)
