# OneNote Page 对象的 `kind` 合同与附件表示差异

> 状态：当前有效的工程经验<br>
> 观察日期：2026-08-11<br>
> 范围：Windows OneNote Desktop、本地 COM、隔离的交互式 fixture bootstrap<br>
> 当前对象模型：[`../design/object_model.md`](../design/object_model.md)<br>
> Copy/Move 契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)<br>
> 验证流程：[`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md)

## 结论

公开 Page 对象模型的类型字段是 `kind`。Page XML parser 内部使用的 `type` 只是从 XML element local name 得到的中间字段，随后会被 domain mapper 映射为 `PageContentObject.kind`；manual-validation snapshot、detector 和 comparator 都必须只消费 `kind`，不能用 `type` fallback 掩盖 schema 漂移。

在本次 OneNote 环境中，用户通过菜单“插入 → 文件附件”连续尝试不同的合成文件时，已保存的 COM 回读始终把附件对象公开为 `kind=InsertedFile`，没有观察到 `kind=FileAttachment`。更早的一次拖放文件也回读为 `InsertedFile`。这说明当前环境的 UI 动作不能作为独立 `FileAttachment` XML 表示的生成方法；它不证明两个 XML kind 在所有 OneNote 版本中等价。

因此，项目已经删除 `FileAttachment` 的专属 bootstrap、Recipe、注册项和合同测试，不再尝试生成独立 fixture。生产解析仍把 `FileAttachment` 与 `InsertedFile` 视为不同 kind；两者不设别名，FileAttachment 保持 unverified，也不允许 Move 删除源。

## 真实观察与证据边界

用户报告连续四次使用 OneNote 的“插入 → 文件附件”操作添加不同文件，界面操作结果均被 detector 判断为 `InsertedFile`。保存的 artifact 中，三次在正确的 run-bound 确认后完成了新鲜 COM snapshot 和 content-free capability projection；三次结果均为：

- 请求计数：`FileAttachment=1`；
- 观察计数：`InsertedFile=1`、`FileAttachment=0`；
- 支撑节点只包含正常的 `Outline/OE`；
- projection 完整，没有未知节点或不支持的 Page 根节点；
- detector 结果为 `missing=[FileAttachment]`、`unexpected=[InsertedFile]`。

第四次菜单操作的用户观察与前三次一致，但该次 run 的确认短语不匹配，runner 在确认后 COM capture 之前停止。因此它属于用户报告的 UI 观察，不计作第四份机器 snapshot。另有一次更早的拖放文件运行留下 COM 回读证据，同样只观察到 `InsertedFile`；拖放证据与菜单证据分别记录，不能互相冒充操作来源。

后续修复 fixture cache 的 working hierarchy 加载和 live ID 重绑定后，用户于 2026-08-11 完成了一次独立 InsertedFile cache consumer 和一次新的 InsertedFile bootstrap。consumer 的 materialized live projection 精确观察到 `InsertedFile=1`，无 missing/unexpected/unknown；bootstrap 在人工 ACCEPT 后同样观察到精确 `InsertedFile=1`，发布 ready template，并在 Notebook、Section、Page ID 均由 OneNote 重建的第二份 working bundle 上再次通过 detector。两次运行都证明 template 未被打开且 working Notebook 已正常关闭。

2026-08-12，用户又执行 `interactive-copy-inserted-file` 的 `run-2026-08-12-12-34-58`：同一 ready cache materialize 后，source/target 都精确回读为 `InsertedFile=1`，strict canonical 机器比较和 detector/comparator 全部通过，无 omitted content；用户实际打开目标附件确认合成内容一致并提交 run-bound ACCEPT，working Notebook 正常关闭。该证据支持当前环境中的 InsertedFile reconstruction Copy，并通过生产共享 Copy 门参与 Move；它仍不构成独立 `FileAttachment` 表示或程序化创建证据。

pytest 和 `--dry-run` 只证明调整后的分类、证据落盘和 fail-closed 编排，不被用来证明真实 OneNote 行为。本文不记录 Page 正文、附件名称、Notebook 名称、对象 ID、用户路径或二进制内容。

## 观察环境

以下值在证据复核时从本机 OneNote executable、Office Click-to-Run configuration 和 Windows version registry 只读取得：

| 项目 | 观察值 |
| --- | --- |
| OneNote file/product version | `16.0.20228.20158` |
| Office client version | `16.0.20228.20158` |
| Office product | `O365HomePremRetail` |
| Office platform | `x64` |
| Office Audience ID | `492350f6-3a01-4f97-b9c0-c7c6ddf67d60` |
| Office update channel URL | `http://officecdn.microsoft.com/pr/492350f6-3a01-4f97-b9c0-c7c6ddf67d60` |
| Windows | `Windows 10 Pro`, display version `25H2`, build `26200.8875` |
| OS architecture | 64-bit |
| Culture / time zone | `zh-CN` / `China Standard Time` |

Audience ID 和 update channel 保留原始配置值；本文不根据 GUID 推断未被本机配置直接声明的市场渠道名称。这个单一环境中的结果不得推广为所有 Office channel 或 OneNote 构建的保证。

## `type` 与 `kind` 的字段边界

当前数据流有两个不同层次：

1. `collect_page_objects()` 遍历 Page XML，把 element local name 暂存在 parser-private record 的 `type` 字段中；这个 record 不是公开 tool 返回，也不是 manual-validation detector 的输入合同。
2. `content_objects()` 把内部 `type` 映射为 `PageContentObject.kind`。公开 `get_page_objects` 结果和 snapshot 中的 `page_objects` 只使用 `kind`。

因此，`{"type": "InsertedFile"}` 只能作为 parser 层内部形状；`{"kind": "InsertedFile"}` 才是公开模型形状。在 detector 中同时接受二者会掩盖 mapper 或 snapshot 的 schema 漂移：即使公开合同意外退回内部格式，验证仍可能误通过。正确做法是看到缺失 `kind` 或残留 `type` 就 fail closed，并把 `invalid-object-schema` 写入 detection evidence。

## `FileAttachment` 与 `InsertedFile` 的表示边界

`FileAttachment` 和 `InsertedFile` 是 OneNote Page XML 中两个不同的 element local name，映射后也是两个不同的 `PageContentObject.kind`。它们可共享部分对象字段，例如 `callback_id`、`media_type`、容器/删除目标信息，但字段结构相似不能证明内容语义或 Copy 保真合同相同。

本次观察只支持以下有限结论：当前环境中，菜单“插入 → 文件附件”和拖放文件都可以形成公开 `kind=InsertedFile`；本次没有观察到独立的 `kind=FileAttachment`。现有证据不能回答 `FileAttachment` 是否只存在于旧版 schema、其他 Office channel、特定附件来源或其他 UI 路径，也不能证明 OneNote 永远不会再返回它。

因此不能采用以下捷径：

- 不能把 parser 遇到的 `InsertedFile` 重写为 `FileAttachment`；
- 不能让 `FileAttachment` detector 接受 `InsertedFile` 并发布成功模板；
- 不能因为两个节点具有相似 callback/format 字段就共享已验证状态；
- 不能把当前环境的多次一致观察升级成跨版本的产品保证。

## 当前设计决策

- `kind` 是 Interactive/UserAuthored detector 和 comparator 的唯一类型真相；`type` 不设兼容 fallback。
- `FileAttachment` 与 `InsertedFile` 继续作为不同 XML capability 保留在 parser/copy projection 中。
- 基于当前版本 GUI 多次无法生成独立表示的证据，`FileAttachment` 已排除出当前 Copy 内容取证范围并删除专属测试入口；这是验证优先级决策，不是把它与 `InsertedFile` 合并，也不把它标记为已验证。
- `MeetingInfo` 同样不再具有专属 bootstrap、Recipe 或合同测试：它小众、GUI 生成困难且当前验证价值低。该排除不改变生产层对已出现 `MeetingInfo` 的 unverified/fail-closed 处理。
- 生产 Copy allowlist 和 Move source-deletion 权限不因上述排除而放宽。
- 若未来在另一环境中真实观察到独立 `kind=FileAttachment`，应保留环境信息和 content-free projection，按独立 comparator 完成 Copy/Move 证据后再更新状态，而不是反向复用本次 `InsertedFile` 证据。

## 对测试与排障的启示

- 测试 fixture 应使用公开形状 `{"kind": ...}`；`{"type": ...}` 只用于负向 schema 回归。
- 同一对象计数必须同时由公开对象列表和不含正文的 capability projection 交叉确认；未知节点或 projection 不完整时 fail closed。
- UI 操作说明只能证明用户被要求执行什么，实际 capability 必须由确认后的新鲜 COM 回读决定。
- 重复得到同一替代表现后，应提升诊断质量并停止把它描述成普通“错误类型”，但不能借此放宽接受集合。
- 操作来源、用户观察和机器 snapshot 是三种证据层级；确认短语错误导致没有 post-confirmation capture 时，必须明确缺少机器证据。

## 适用边界

本文记录的是上述 OneNote/Office/Windows 环境中的 Page 对象投影经验，不定义跨版本的附件序列化规范。公开字段继续以 [`../design/object_model.md`](../design/object_model.md) 为准，Copy/Move 放权以 [`../design/tool_contracts.md`](../design/tool_contracts.md) 为准，真实隔离验证的授权与流程以 [`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) 和 [`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md) 为准。
