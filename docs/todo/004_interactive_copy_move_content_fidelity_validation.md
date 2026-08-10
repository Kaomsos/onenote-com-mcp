# 004：交互式 Copy/Move 未验证内容保真验收

> ID：004
> 状态：待办
> 优先级：P2
> 类型：真实后端验证 / Page 内容保真
> 更新日期：2026-08-09

## 背景与证据边界

当前静态保真 allowlist 已包含 `Outline`、`Image`、`RichText`、`Table`、`List` 和 `Tag`。以下由 Page XML 转换器识别并尽力保留的内容能力仍没有真实 OneNote Copy/Move 保真结论：

| 待验证能力 | 对应 OneNote XML/对象 | 验证重点 |
| --- | --- | --- |
| 普通附件 | `FileAttachment` | 文件名、显示图标、大小与提取后二进制 hash；不得只比较 callback/object ID。 |
| 插入文件 | `InsertedFile` | 插入文件的显示与打开行为、文件名、大小及二进制 hash。 |
| 墨迹 | `InkDrawing`（内部可能含 `Ink`） | 可见笔迹、位置/尺寸和可提取数据；COM 重写对象 ID 时不得误判。 |
| 媒体 | `MediaFile`（内部可能含 `MediaPlaylist`） | 音频/视频对象的显示、可播放性、文件元数据及可取得的二进制 hash。 |
| 会议详情 | `MeetingInfo` / `MeetingInfoItem` | 标题、时间、地点和合成的非敏感参与者字段；先观察 COM 规范化行为，再决定比较 tier。 |

未知 namespace、未来扩展节点和 `unsupported_nested_page_node` 不属于本 TODO 的放行范围。它们继续 fail closed，不得通过一次人工点击确认加入静态 allowlist。`Title`、`PageSettings`、`QuickStyleDef` 和 `TagDef` 是支撑节点，也不作为独立内容类型验收。

## 目标

建立两个不进入 `all` 的扁平、具名、human-gated 场景，让用户在 Runner 创建并绑定精确 ID 的 disposable fixture Page 中，通过 OneNote UI 手动加入待验证内容：

1. `interactive-copy-content`：只取得 Copy 保真证据，不获得 Delete 权限；
2. `interactive-move-content`：仅在对应类型已基于 Copy 证据进入静态 allowlist 后，验证严格 Move 和非永久源删除门。

两个场景都必须保持 local-only、单 scenario/单 MCP、静态最小权限和全程有界。Agent、pytest、CI、hook、timer、watcher 或后台任务不得执行真实命令；只有用户可以在终端显式启动并完成交互确认。

## 提议的交互模型

### 1. Copy 取证场景

提议的公开命令：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-content --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-content `
  --content-types file-attachment,inserted-file,ink-drawing,media-file,meeting-info `
  --office-version "<version>" `
  --office-channel "<channel>" `
  --keep-worksite
```

这只是目标 CLI，实施前不构成当前可用命令。场景设置 `registered_for_all = False`，特殊入口 `all` 永远不得透传或自动选择交互场景。

Runner 的有界状态机：

```text
lifecycle create fresh disposable Notebook
→ start exactly one scenario-scoped MCP process
→ create one source Page per requested content type plus one exact target Section
→ write checkpoint.json and show exact Notebook/Page IDs and synthetic-content instructions
→ wait for the user to add content in OneNote UI and enter a run-bound confirmation phrase
→ fresh-read each exact source Page and reject missing, extra, ambiguous, or misplaced content
→ persist before snapshot, plan and execute Copy without Delete permission
→ navigate/show exact copied Pages and wait for a per-type human verdict
→ persist machine comparison plus human-acceptance.json
→ perform the scenario's declared cleanup, or preserve the exact worksite when --keep-worksite is explicit
```

每个类型使用独立 Page，避免一个宽松 comparator 掩盖另一个稳定类型的丢失。用户只能在 manifest 绑定的 fixture Page 中添加内容；Runner 不接受外部 Notebook/Page ID，也不按名称猜测目标。附件和媒体必须使用无敏感信息的 disposable 本地文件；会议详情只能使用合成标题、地点和参与者，不得把真实会议或联系人写入证据。

交互等待必须有独立、可配置但有上限的 timeout。EOF、超时、取消、确认短语不匹配、检测不到所选类型或出现额外未知节点时，都必须在 Copy/Move 前 fail closed，保持 Notebook 打开并保存 checkpoint 和失败交接。Bridge audit 继续 content-free；允许保存内容的本地 evidence 必须明确标注为 synthetic fixture，并留在忽略目录中。

Copy 场景允许预期的 `content_type_unverified`，因为它的职责正是收集候选类型证据；该结果绝不能触发源删除，也不能在运行时修改生产 allowlist。用户的 UI verdict、自动比较和 Office 环境信息共同形成后续代码评审输入，而不是即时放权。

### 2. Comparator 与静态 allowlist 评审

每种类型必须单独决定验证 tier，不能因为 UI 看起来相同就全局放宽：

- 能稳定往返的 XML 与对象结构继续使用 `strict_canonical`；
- 附件/媒体优先要求文件名、对象元数据和二进制 SHA-256 等价；
- 墨迹需要保留可见结果，并基于实际证据决定 XML、二进制或几何语义投影；
- `MeetingInfo` 需要先记录 COM 规范化差异，再定义字段级语义投影；
- 任一无法建立可自动回读 invariant 的类型继续保持 unverified，不进入 Move 放行集合。

只有在用户确认 Copy UI、机器证据可重复、相应 comparator 具有自动化合同测试之后，才能通过代码变更把该类型加入 `VALIDATED_COPY_CONTENT_TYPES`。该变更必须同步更新当前 tool 契约、manual-validation README 和用户 README；不得由 scenario 输入或 evidence 文件动态决定生产 allowlist。

### 3. Move 发布门场景

提议的公开命令：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-move-content --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-move-content `
  --content-types <only-statically-validated-types> `
  --office-version "<version>" `
  --office-channel "<channel>" `
  --keep-worksite
```

该场景拥有独立静态 spec，才可启用 Writes、Experimental Copy、Deletes 和 Move；永久删除与 raw XML 始终关闭。它重复创建全新 Notebook 和全新交互 fixture，不复用 Copy 取证场景的 Notebook、run-dir、MCP、policy 或运行时状态。

用户完成 fixture 编辑后，Runner 必须重新检测精确内容类型并生成 fresh plan。只有 `copy_report.lossless=true`、`verified=true`、每个 Page 按其静态 tier 等价、源快照未变化且不存在 issue 时，才允许执行 `DeleteHierarchy(permanently=false)`。成功后还必须证明整棵源子树从活动 hierarchy 消失；回收站 UI 可见性继续作为人工诊断，不替代活动树回读。

## 实施范围

1. 新增两个独立 `Scenario` 子类并显式注册，均设置 `registered_for_all = False`；不得新增 `prepare/resume/inspect` 等公开 helper action。
2. 为交互 checkpoint、run-bound confirmation、timeout、取消和 stdin EOF 建立可测试的 runtime abstraction，合同测试不得真实等待用户输入。
3. 为每种类型创建独立 scaffold Page 和 exact-ID manifest；fixture 构建本身不得伪造 raw XML 内容，待验证对象必须由用户在 OneNote UI 中加入。
4. 新增内容检测器，输出 requested/observed/missing/unexpected 类型和对象计数；检测不到精确类型时禁止 Copy/Move。
5. Copy 取证写入 `checkpoint.json`、`before.json`、`copy-result.json`、逐类型机器比较、`human-acceptance.json`、`worksite.json` 和报告。
6. 基于真实证据分别实现或收紧附件、墨迹、媒体和 MeetingInfo comparator，并覆盖成功、规范化差异、二进制不一致、对象丢失和未知节点分支。
7. 在独立代码评审中更新静态 allowlist；随后由用户运行 Move 发布门场景。
8. 更新 `tests/manual_validation/README.md`、`docs/design/tool_contracts.md`、根 README 和相关开发文档，明确已验证的 OneNote/Office 版本范围。

## 自动化合同要求

- dry-run 不创建目录、不启动 MCP、不读取 stdin、不访问 OneNote，并显示交互阶段、timeout、静态 policy、tool allowlist 和 Copy budget；
- 两个场景都不进入 `all`，且 `all` 不接受或透传交互参数；
- Copy 场景没有 Delete、Permanent Delete、Raw XML 或 Move 权限；
- Move 场景只接受已在代码中静态验证的类型，不因用户输入扩权；
- timeout、EOF、取消、错误确认短语、缺失/额外类型和未知节点均在 mutation 前 fail closed；
- 所有 target 都来自本次 manifest 的精确 ID，禁止名称匹配和外部 ID；
- 用户拒绝任一类型时不得把该类型记为通过，Move 阶段不得删除源；
- `--keep-worksite` 记录精确目标、清理要求并保持源 Notebook 打开，默认 cleanup/close 行为仍有覆盖；
- 永久删除始终为 false；任何 Copy/Move 部分失败都保留 evidence 与现场，不自动重试；
- Bridge audit 不含内容、参数、路径或二进制；本地 synthetic evidence 与 content-free audit 明确分离。

## 人工验收记录要求

每个候选类型至少保留一次用户明确确认的成功 Copy 证据，并记录：

- run ID、OneNote/Office 完整版本、Office channel、Windows 版本与时区；
- source/target 精确 ID、content capability、对象计数和所用 verification tier；
- canonical/semantic/binary 各检查项及其差异；
- 用户在 OneNote UI 中对显示、打开/播放/书写或会议字段的逐项 verdict；
- cleanup 状态，以及失败或不确定时保留的 Notebook/worksite；
- 结论只适用于已记录的 OneNote/Office 组合，不外推为普遍 COM 保证。

对应类型进入静态 allowlist 后，还必须至少保留一次 `interactive-move-content` 成功证据，证明目标内容通过同一 tier、old→new ID 已记录、源子树从活动 hierarchy 消失且删除调用为非永久。

## 完成定义

- [ ] `FileAttachment`、`InsertedFile`、`InkDrawing`、`MediaFile` 和 `MeetingInfo` 均已获得隔离的逐类型 Copy 证据，或被明确记录为无法安全验证并继续保持 unverified；
- [ ] 两个交互场景、静态最小权限、checkpoint、timeout、失败保留和 evidence schema 均有纯合同测试；
- [ ] 每个获准类型都有针对实际 COM 规范化行为的自动 comparator，不能仅依赖用户点击确认；
- [ ] 只有满足 Copy 证据、自动 comparator 和文档要求的类型进入静态 allowlist；
- [ ] 用户本人对每个获准类型完成严格 Move 场景，源删除保持非永久且活动树回读通过；
- [ ] 未通过、未运行或证据不确定的类型继续产生 `content_type_unverified` 并阻止 Move 删除源；
- [ ] 当前设计文档、manual-validation 流程、根 README 和 TODO 索引同步更新；
- [ ] 用户确认真实后端证据后，本 TODO 才能标记为“已完成”。
