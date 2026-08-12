# 004：交互式 Copy 未验证内容保真验收

> ID：004
> 状态：已完成
> 优先级：P2
> 类型：真实后端验证 / Page 内容保真
> 更新日期：2026-08-12

## 背景与证据边界

本 TODO 启动时，静态保真 allowlist 只包含 `Outline`、`Image`、`RichText`、`Table`、`List` 和 `Tag`。以下由 Page XML 转换器识别并尽力保留的内容能力需要逐类别取得真实 OneNote Copy 保真结论：

| 待验证能力 | 对应 OneNote XML/对象 | 验证重点 |
| --- | --- | --- |
| 墨迹 | `InkDrawing`（内部可能含 `Ink`） | 可见笔迹、位置/尺寸和可提取数据；COM 重写对象 ID 时不得误判。 |
| UI 形状 | 公开 `kind=InkDrawing` + XML `ShapeInfo`；箭头另含 `AnchorPoint` | 复用 InkDrawing 语义 comparator，并额外证明形状 marker、样式、位置与尺寸保持。不得伪造字面量 `kind=Shape`。 |
| 媒体 | `MediaFile`（内部可能含 `MediaPlaylist`） | 本轮 v8 通过 OneNote“插入 → 录制视频”创建 1–2 秒 synthetic 录像，验证其显示、可播放性、文件元数据及可取得的二进制 hash；既有音频证据作为历史基线保留。 |
| 插入文件 | `InsertedFile` | 复用既有 ready recipe/cache，要求可读本地 source/cache 路径、strict canonical 比较，并由用户实际打开目标附件确认内容。 |

未知 namespace、未来扩展节点和 `unsupported_nested_page_node` 不属于本 TODO 的放行范围。它们继续 fail closed，不得通过一次人工点击确认加入静态 allowlist。`Title`、`PageSettings`、`QuickStyleDef` 和 `TagDef` 是支撑节点，也不作为独立内容类型验收。

## 当前实施状态

截至 2026-08-11，代码取证入口已经按类型拆分并保持 `included_in_all=False`：

- `bootstrap-ink-drawing-fixture` / `interactive-copy-ink-drawing --use-cache`；
- `bootstrap-media-file-fixture` / `interactive-copy-media-file --use-cache`；
- `bootstrap-shape-fixture` / `interactive-copy-ui-shape --use-cache`；
- `bootstrap-inserted-file-fixture` / `interactive-copy-inserted-file --use-cache`（复用已有 ready fixture，不重复 bootstrap）。

交互 Copy consumer 使用固定 ready cache instance、root-only Page Copy 和无 Delete 的 `COPY_NO_DELETE_POLICY`。机器门同时检查 source/target 的公开 `kind` detector、去除生成 ID 后的稳定对象签名、无 omitted content；评审结果只能在完整 comparator 通过后静态登记为 lossless，未知能力仍为 `content_type_unverified`。MediaFile 使用 strict canonical；InkDrawing/UIShape 使用各自有界几何语义 tier。场景本身从不在运行时修改静态类型集合，Copy target 保留在 disposable working artifact。

真实 run `run-2026-08-11-21-26-11` 已证明 Ink target 创建成功、source untouched、`visible_text/content_objects/binary_sha256=true` 且用户事后报告肉眼等价，但生产 strict canonical 为 false，旧场景在正式 run-bound verdict 前提前退出。后续 `run-2026-08-11-21-41-37` 的 exact partial admission、detector、对象签名、binary SHA 与 run-bound 人工 verdict 均通过；唯一差异是 COM 将 `Position.x` 量化 `0.0000457763672`、`Size.width` 量化 `0.00001525878906`，其余 Position/Size、Ink 节点和数据一致。代码据此冻结 `1e-4` 有界几何门；最终 `run-2026-08-11-21-53-24` 的 detector、partial admission、`semantic_ink_drawing`、binary SHA 和 run-bound 人工 verdict 全部通过，最大几何 delta 为 `0.0000457763672`，因此 InkDrawing Copy 证据闭合并在最终静态评审中进入生产 allowlist。

UI Shape discovery 也已取得两种真实 UI 操作证据：用户确认 `run-2026-08-11-21-57-18` 绘制矩形，公开对象新增 `kind=InkDrawing` 且 XML projection 新增 `ShapeInfo`；`run-2026-08-11-22-00-08` 绘制箭头，同样新增 `kind=InkDrawing + ShapeInfo`，并额外出现箭头结构的 `AnchorPoint`。两次均由旧 discovery 正确保持 `evidence_only`，没有发布 cache。代码随后把共同表示冻结为 `UIShape` 复合 capability，recipe version 提升到 5，并增加 cache-only `interactive-copy-ui-shape`；其最终 Copy 证据见下文。

MediaFile 首次真实 bootstrap `run-2026-08-11-22-26-30` 精确观察到一个公开 `kind=MediaFile`，以及录制音频必需的顶层 `MediaPlaylist`、`MediaIndex` 和 `MediaReference`。旧 projection 因后面三项未建模而在发布 cache 前正确失败并保留现场。代码据此先把 `MediaPlaylist` 加为支撑根并将 recipe version 提升到 5；第二次 `run-2026-08-11-22-30-44` 进一步证明 MediaIndex 并非 Playlist/File 的后代，因此仍在确认后 fail closed。对该打开现场的 content-free 只读 XML 路径检查得到 `Page/MediaPlaylist/MediaReference`、同一含 MediaFile 的 Outline 中的 `OE/MediaIndex/MediaReference` 和 `OE/MediaFile/MediaReference`。最终 projection 仅在这些关联上下文接受两类节点；以同一 live Page 复核得到 `complete=true`、`capabilities=[MediaFile, Outline]`、无 unknown/unsupported。

第三次 `run-2026-08-11-22-35-53` 的 authored detector、用户 run-bound verdict、close/stage/publish/materialize 都通过，但第二份 working copy live validation 出现 `RichText=1`，因此 v5 entry 被 quarantine 且不可命中。只读 content-free 检查确认 OneNote 在 materialize 后将录音时间轴规范化为一个直接子节点恰为 `MediaIndex + T` 的 OE，T 只含 `span`；同一 Outline 的另一个 OE 持有 MediaFile。recipe version 6 仅把这种精确关联的 span T 作为媒体支撑，普通富文本标签、额外 OE 子节点或没有同 Outline MediaFile 的 RichText 仍 fail closed。以同一 materialized live Page 复核 v6 projection 得到 `complete=true`、`capabilities=[MediaFile, Outline]`、无 unknown/unsupported。前三个 run 都只算 representation/cache round-trip 校准证据；用户仍需重新运行 v6 bootstrap。

v6 bootstrap `run-2026-08-11-22-44-14` 随后完成 authored detector、人工 verdict、发布、materialized live revalidation 和默认关闭，ready template 已可命中。首个 consumer `run-2026-08-11-22-46-10` 在 mutation 前停止：三次 plan 的源/目标资源、Page `modified`、能力和对象计数完全一致，但生产 planning 仍把 raw COM Page XML SHA-256 纳入 `plan_digest`，MediaFile 暴露的 OneNote 自有 cache/view 元数据令每次 raw hash 不同。修复后 plan schema v3 使用已有的稳定 in-place Page content digest 绑定 stale-plan 门限，继续保留对象 ID、正文、格式和二进制；raw `page_xml_hashes` 只写诊断证据。易变 metadata 可连续稳定，而真实正文变化仍改变 digest 并 fail closed。该 run 未执行 Copy，不算 MediaFile Copy 成功证据。

后续 consumer `run-2026-08-11-22-55-36` 与 `run-2026-08-11-22-58-47` 均证明 plan schema v3 已生效；后者两次稳定内容 hash/plan digest 相同，而 raw XML hash 不同，并在第二次 plan 后进入真实 Copy。Copy 创建了唯一空 target，但 `write_page_content` 返回官方 `hrInsertingFile (0x80042016)`，source untouched，场景拒绝把 write partial 纳入 comparator。对保留现场的 content-free 只读检查确认 `MediaFile.pathCache` 指向存在的 OneNote 本地媒体文件，`pathSource` 已不存在；旧转换器却删除有效 `pathCache` 并保留失效 `pathSource`。转换器现在仅在原始 `pathSource` 不存在且 `pathCache` 指向现存文件时，把 cache 路径提升为 outbound `pathSource`；两种机器本地路径均从稳定/canonical 比较中排除，媒体结构、引用、metadata 和二进制门不放宽。这两个 run 均未完成媒体写入，不算 Copy 成功证据。

修复后的同 Section consumer `run-2026-08-11-23-03-40` 首次闭合音频 MediaFile Copy：唯一 target、source untouched、stable plan、strict canonical、source/target detector、公开对象签名、人工显示/播放 verdict 和默认关闭全部通过；生产 `content_type_unverified` 仍保留，因此不自动进入 allowlist/Move。为增加目标拓扑覆盖，recipe version 7 将同一个 `interactive-copy-media-file` 场景扩展为顺序执行同 Section 与新建 run-bound Section 两个 root-only case。第二个 case 必须证明 source 和第一个 target 未变化；两个 target 分别通过完整机器 comparator，最后一次人工 verdict 同时绑定两者。在 v7 尚未发布新 cache 前，用户决定改用 OneNote“插入 → 录制视频”收集录像证据，因此 recipe version 8 明确要求 1–2 秒 synthetic video recording。bootstrap 仍以 `kind=MediaFile` 和 fail-closed projection 为门；视频出现新节点时不得沿用音频别名或发布 cache，而应保留现场后按真实结构补模。v8 fingerprint 会使既有音频 cache 明确 miss，用户需重新 bootstrap 后再收集双 case 录像证据。

录像 bootstrap `run-2026-08-11-23-21-38` 随后以 v8 fingerprint 发布 ready template，并完成 materialized live revalidation：公开对象为恰好一个 `kind=MediaFile`，projection 为 `MediaFile + Outline`，无 unknown/unsupported。consumer `run-2026-08-11-23-23-16` 从 `validated_hit` 顺序完成同 Section 与跨 Section 两个 root-only Copy；两个 case 都是 `strict_canonical`、machine comparator passed、source/target detector passed、对象签名等价、无 omitted content，用户对两个可播放录像给出同一个 run-bound accepted verdict，源保持未删除，working Notebook 默认关闭。用户随后在 OneNote UI 中人工删除源 Section，并确认两个已复制录像仍然可用。结合既有 InkDrawing `run-2026-08-11-21-53-24` 和 UIShape `run-2026-08-11-22-23-29`，静态 `VALIDATED_COPY_CONTENT_TYPES` 已加入 `InkDrawing/UIShape/MediaFile`；生产 Copy 正式采用 `semantic_ink_drawing`、`semantic_ui_shape` 与 MediaFile strict canonical。

2026-08-12，InsertedFile 的 cache-only consumer `run-2026-08-12-12-34-58` 在修复 outbound 本地文件路径后完成真实同 Section root-only Copy。source/target detector 均精确观察到 `InsertedFile=1`，strict canonical 的 canonical XML、可见文本、内容对象和 binary 项全部通过，无 omitted content，机器 comparator passed；用户实际打开复制后的附件确认合成内容一致并提交 run-bound ACCEPT，working Notebook 正常关闭。`InsertedFile` 据此加入静态 validated Copy 集合并可通过共享 Copy 合同参与 Move。未知节点、越界几何以及未验证的 FileAttachment/MeetingInfo 仍 fail closed；Embedded Spreadsheet（内嵌电子表格）尚无公开 `kind`/XML 证据，按产品能力类别明确 unsupported，同样不能满足 Copy 合同。

Shape consumer 校准 run `run-2026-08-11-22-18-48` 使用用户在 v5 bootstrap 中绘制的单个“坐标系”形状。Copy 创建唯一 target、source untouched，source/target detector 均为 `InkDrawing + ShapeInfo=1`，shape marker、完整 Ink/Shape 子树、非几何属性、数据 hash、二进制 SHA、可见文本和对象数量全部一致，且用户 run-bound UI verdict 为 accepted。唯一机器失败是 OneNote 将 `Position.x/y` 与 `Size.width/height` 重算，最大绝对 delta 为 `0.0168457031250`，超过从自由墨迹继承的 `0.0001`。该 run 只作为 Shape 几何 comparator 校准证据；代码据此为 `semantic_ui_shape` 冻结独立 `0.02` 上限，自由墨迹仍保持 `1e-4`，超过 `0.02`、非数字或结构/data/marker 差异继续 fail closed。最终 `run-2026-08-11-22-23-29` 从同一 v5 template `validated_hit`，唯一 target、source untouched、detector、marker、结构/data、二进制 SHA、`semantic_ui_shape` 和 run-bound 人工 verdict 全部通过，最大 delta 仍为 `0.0168457031250`，默认 lifecycle 正常关闭。UI Shape Copy 证据据此闭合并在最终静态评审中进入生产 allowlist。

## 目标

建立不进入 `all` 的扁平、具名、human-gated 场景，让用户在 Runner 创建并绑定精确 ID 的 disposable fixture Page 中，通过 OneNote UI 手动加入待验证内容：

1. 每个已知静态 `kind` 使用独立 bootstrap 与 `interactive-copy-<type>`，只取得 Copy 保真证据，不获得 Delete 权限；
2. UI Shape 只接受经两次 discovery 冻结的 `InkDrawing + ShapeInfo` 复合表示；普通自由墨迹、缺失/多个 `ShapeInfo` 或未知节点均在发布/Copy 前失败；
3. 逐类别验证只负责 Copy 内容合同；Move 不建立第二套类别清单或逐类型场景，直接复用生产 Copy 的 `copy_contract_satisfied` 和既有非永久删除安全链。

这些场景都必须保持 local-only、单 scenario/单 MCP、静态最小权限和全程有界。Agent、pytest、CI、hook、timer、watcher 或后台任务不得执行真实命令；只有用户可以在终端显式启动并完成交互确认。

## 提议的交互模型

### 1. Copy 取证场景

当前公开命令：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ink-drawing --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-media-file --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ui-shape --use-cache --dry-run --json
```

三个场景都设置 `included_in_all = False`，特殊入口 `all` 永远不得透传或自动选择交互场景。cache miss/invalid 在打开 Notebook 前返回对应具名 bootstrap 提示；consumer 绝不自动进入 stdin authoring。

Runner 的有界状态机：

```text
lifecycle materialize one ready authored fixture working copy
→ start exactly one scenario-scoped MCP process
→ live-rebind and validate the exact source Canvas Page and Section
→ fresh-read the exact source Page and reject missing, extra, ambiguous, or misplaced content
→ persist before snapshot, plan and execute Copy without Delete permission
→ show the exact copied Page title and wait for a per-type human verdict
→ persist machine comparison plus human-acceptance.json
→ retain the Copy target in the disposable working artifact; close by default or keep open with --keep-worksite
```

每个类型使用独立 Page，避免一个宽松 comparator 掩盖另一个稳定类型的丢失。用户只能在 manifest 绑定的 fixture Page 中添加内容；Runner 不接受外部 Notebook/Page ID，也不按名称猜测目标。墨迹和形状只使用最小、可辨识的 synthetic 图样；MediaFile v8 使用 OneNote“插入 → 录制视频”创建 1–2 秒 synthetic 录像，不附加或拖放已有媒体文件，也不把账号、私人链接或业务内容写入证据。

交互等待必须有独立、可配置但有上限的 timeout。EOF、超时、取消、确认短语不匹配、检测不到所选类型或出现额外未知节点时，都必须在 Copy 前 fail closed，保持 Notebook 打开并保存 checkpoint 和失败交接。Bridge audit 继续 content-free；允许保存内容的本地 evidence 必须明确标注为 synthetic fixture，并留在忽略目录中。

Copy 取证阶段允许预期的 `content_type_unverified`，因为它的职责正是收集候选类型证据；该结果不能在运行时修改生产集合。用户的 UI verdict、自动比较和 Office 环境信息共同形成后续静态代码评审输入。评审后只有通过完整机器 verification tier 的类型才能进入 lossless 集合并得到 `copy_contract_satisfied=true`；Move 只消费这一共享结论。

### 2. Comparator 与静态 allowlist 评审

每种类型必须单独决定验证 tier，不能因为 UI 看起来相同就全局放宽：

- 能稳定往返的 XML 与对象结构继续使用 `strict_canonical`；
- 墨迹需要保留可见结果，并基于实际证据决定 XML、二进制或几何语义投影；
- 形状必须先记录 GUI 操作产生的真实公开 `kind` 和 content-free XML capability projection，再决定几何/样式语义投影；实际表示不明确时继续 fail closed，不伪造 `Shape` 类型；
- MediaFile 优先要求录音对象元数据、显示与播放行为等价；COM 暴露的二进制必须同时通过 SHA-256 比较；
- 任一无法建立可自动回读 invariant 的类型继续保持 unverified，不进入生产 Copy 保真 allowlist。

只有在用户确认 Copy UI、机器证据可重复、相应 comparator 具有自动化合同测试之后，才能通过代码变更把该类型加入 `VALIDATED_COPY_CONTENT_TYPES`。该变更必须同步更新当前 tool 契约、manual-validation README 和用户 README；不得由 scenario 输入或 evidence 文件动态决定生产 allowlist。

## 实施范围

1. 为 InkDrawing、MediaFile、UI Shape 分别注册独立 Copy-only consumer；全部设置 `included_in_all = False`，不得新增 `prepare/resume/inspect` 等公开 helper action。
2. 为交互 checkpoint、run-bound confirmation、timeout、取消和 stdin EOF 建立可测试的 runtime abstraction，合同测试不得真实等待用户输入。
3. 为 `InkDrawing`、UI `Shape` 和 `MediaFile` 分别创建独立 scaffold Page 和 exact-ID manifest；`InsertedFile` 复用既有 ready recipe/cache。fixture 构建本身不得伪造 raw XML 内容，待验证对象必须由用户在 OneNote UI 中加入。Shape 在首次真实观察前只表示 UI 操作类别，不得预注册未经证实的公开 `kind`。
4. 新增内容检测器，输出 requested/observed/missing/unexpected 类型和对象计数；检测不到精确类型时禁止 Copy。
5. Bootstrap/discovery 写 checkpoint 和 authored/detection evidence；Copy 取证写入 `before.json`、稳定 plan、`copy-result.json`、逐类型 `machine-comparison.json`、`human-acceptance.json` 与 `worksite.json`。
6. 基于真实证据分别实现或收紧墨迹、形状、MediaFile 和 InsertedFile comparator，并覆盖成功、规范化差异、稳定字段不一致、对象丢失和未知节点分支。
7. 在独立代码评审中更新静态 Copy allowlist；Move 直接复用这一类别门禁，不增加专有发布门。
8. 更新 `tests/manual_validation/README.md`、`docs/design/tool_contracts.md`、根 README 和相关开发文档，明确已验证的 OneNote/Office 版本范围。

## 自动化合同要求

- dry-run 不创建目录、不启动 MCP、不读取 stdin、不访问 OneNote，并显示交互阶段、timeout、静态 policy、tool allowlist 和 Copy budget；
- 四个逐类别 Copy 场景都不进入 `all`，且 `all` 不接受或透传交互参数；
- Copy 场景没有 Delete、Permanent Delete、Raw XML 或 Move 权限；
- timeout、EOF、取消、错误确认短语、缺失/额外类型和未知节点均在 mutation 前 fail closed；
- 所有 target 都来自本次 manifest 的精确 ID，禁止名称匹配和外部 ID；
- 用户拒绝任一类型时不得把该类型记为通过或加入生产 Copy allowlist；
- `--keep-worksite` 记录精确目标、清理要求并保持源 Notebook 打开，默认 cleanup/close 行为仍有覆盖；
- 任何 Copy 部分失败都保留 evidence 与现场，不自动重试；
- Bridge audit 不含内容、参数、路径或二进制；本地 synthetic evidence 与 content-free audit 明确分离。

## 人工验收记录要求

每个候选类型至少保留一次用户明确确认的成功 Copy 证据，并记录：

- run ID、OneNote/Office 完整版本、Office channel、Windows 版本与时区；
- source/target 精确 ID、content capability、对象计数和所用 verification tier；
- canonical/semantic/binary 各检查项及其差异；
- 用户在 OneNote UI 中对显示、播放、书写或形状视觉结果的逐项 verdict；
- cleanup 状态，以及失败或不确定时保留的 Notebook/worksite；
- 结论只适用于已记录的 OneNote/Office 组合，不外推为普遍 COM 保证。

## 完成定义

- [x] `InkDrawing`、OneNote UI `Shape`、`MediaFile` 和 `InsertedFile` 均已获得隔离的逐类型 Copy 证据，并已通过静态代码评审进入生产保真 allowlist；
- [x] FileAttachment/MeetingInfo/Embedded Spreadsheet 的专属测试入口和完成门已排除，原因与证据边界已记录；三者继续保持 unverified/unsupported、不进入 Copy fidelity 集合，因此也不能通过 Move 复用的统一门；其中 Embedded Spreadsheet 尚未观察到公开 `kind`/XML，不能与 Table、InsertedFile 或 FileAttachment 建立别名；
- [x] InkDrawing/MediaFile/UI Shape/InsertedFile 的独立 Copy-only consumer、静态最小权限、timeout、失败保留和 content-free evidence schema 已有纯合同测试；
- [x] UI Shape 真实 discovery 后已按 `InkDrawing + ShapeInfo` 表示补齐专属 detector/comparator/Copy consumer，并取得隔离 Copy 证据；
- [x] 每个获准类型都有针对实际 COM 规范化行为的自动 comparator，不能仅依赖用户点击确认；
- [x] 只有满足 Copy 证据、自动 comparator 和文档要求的类型进入静态 allowlist；
- [x] Move 不维护逐类别场景或专有类别门禁，统一复用生产 Copy 的 `copy_contract_satisfied` 结论与既有非永久删除安全链；
- [x] 未通过、未运行或证据不确定的类型继续产生 `content_type_unverified`，因此不能通过生产 Copy 保真门；
- [x] 当前设计文档、manual-validation 流程、根 README 和 TODO 索引同步更新；
- [x] 用户确认真实后端证据后，本 TODO 标记为“已完成”。
