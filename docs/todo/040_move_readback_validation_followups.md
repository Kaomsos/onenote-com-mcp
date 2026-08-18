# 040：Move 回读校验待解决问题

> ID：040
> 状态：已完成
> 优先级：P0
> 类型：Bug / Page Copy / Hierarchy Path / Page Equivalence / Fidelity
> 更新日期：2026-08-18

## 历史合并决定

本 TODO 原为等待首次真实 mismatch 的空占位。2026-08-18 的首次有效 `interactive-move-page-content` 已同时暴露两个不可拆分的 fidelity 问题：

- `semantic_content_v1` 的 `rich_list_tag_table_outline=false`，现有证据不足以区分 source→transformed 与 transformed→target 两段中的首次差异；
- 专用场景主动传入 `destination_title=01-Representative-Moved-<run timestamp>`，导致目标标题肉眼偏离源标题，且现有 `title=true` 只证明 transformed→target 一致，没有验证默认 source title fidelity。

两者共同决定同一次 Copy-before-delete gate 是否允许删除源 Page，必须在同一个真实 fixture、同一次公开 Move 和同一套 source→transformed→target 证据中诊断与验收。该主线最初由 [TODO 039](039_interactive_real_page_move_lossless_validation.md) 建立专用脚手架并取得真实失败证据；现按用户决定由本 TODO 统一接管。

2026-08-18 的后续真实 run 又证明默认标题 fidelity 还牵涉独立的 hierarchy path 模型问题，不能只作为富文本 comparator 的附属项处理。040 因而重新启用；同日用户进一步决定关闭 039，将真实内容写后差异和最终 Move 闭环也转结到本 TODO，避免两个 P0 台账并行维护同一 Copy-before-delete gate。

同日用户明确要求本 TODO 的验收不得放入 `interactive-move-page-content`。该场景必须继续作为 human-gated authored template 的 Move 开发验收入口（fresh 发布 template，`--use-cache` 消费 ready instance），保留显式 `--use-cache --template-instance-id` 与 run-bound UI gate 的开发约定。040 本质上是 `get_hierarchy_path`、`copy_page` 与 `move_page` 的自动 fixture 回归，因此分别扩展既有 `hierarchy-navigation`、`copy-page`、`move-page` 场景；三者自行创建 disposable fixture，不要求用户手工制作夹具。

## 从 TODO 039 接管的剩余范围

- 保留 039 已交付的 bootstrap/interactive authored-template 配对与 content-free diagnostic，不改变其开发职责；040 的自动验收只扩展既有 path/Copy/Move 场景；
- 修复默认及显式 Page title 被 filesystem leaf 规则清洗的问题，并以结构化 `path_segments` 提供可逆 hierarchy 表示；
- 让完整 typed projection 的纯 `Outline + RichText` Page 获得适用的语义 verification tier 和 source→transformed→target 分段诊断，不因缺少 Table/Image 而只能退回无类型的 strict failure；
- 实施 Table 列宽最大 `5%` 的窄 transformed→target 容差，并为 Page equivalence 输出按 content object 类型分类的稳定错误；
- 使用自动创建的特殊标题 fixture 分别完成 path、Copy 与 Move 真实回归；Move 的 RichText/三列表格源只有在 `verified/lossless/copy_contract_satisfied=true` 后才非永久删除 disposable source。

## 事项：Page Copy 标题保真与 `get_hierarchy_path` 可逆表示

### 已观察证据

2026-08-18 的一个用户前台真实 run 使用未显式传入 `destination_title` 的 `interactive-move-page-content`。文档只保留 content-free 结论；真实标题、对象 ID、Notebook 名称、精确 run 标识和投影 hash 仅保存在本地 evidence，不进入版本库。观察结果为：

- source Page 标题包含路径分隔符，目标标题中的该分隔符被替换为空格；
- `semantic_content_stages.title_override_requested=true`，source→transformed 只有 `$.title` 不同，字符数按预期减少；
- source→transformed 的正文 outline hash 完全一致，说明该标题变化发生在 Copy planning/target-name 路径，而不是 OneNote 写后规范化；
- 公开 `move_page` 调用参数中没有 `destination_title`，因此这不是 scenario 主动重命名；
- Move 仍以 `copy_only` fail closed，source untouched 且未删除，target 与两个 working Notebook 由 `--keep-worksite` 保留。

同日另一个用户前台真实 run 又用不同的代表性 Page 重现相同缺陷：source title 中的 `:` 在默认 Copy 目标标题中被清除，目标标题精确等于对 source title 应用 `safe_leaf_name` 后的结果；公开 `move_page` 同样没有传入 `destination_title`。该次 source/target 的 `Outline + RichText` capability projection、富文本 markup 计数和对象种类/数量一致，但回读因标题已在 Copy planning 阶段改变而得到 `visible_text=false`，继续以 `copy_only` 阻止删源。这证明问题不限于 `/` 路径分隔符，`:` 等仅对文件系统 leaf 非法、但对 OneNote Page title 有效的字符都会被错误清洗。

根因已锁定为 `CopyService._destination` 对 Page 与文件系统容器统一调用 `MutationService.safe_leaf_name`。该函数会把 `/`、`\\`、`:` 等文件名非法字符替换为空格，但 OneNote Page title 是逻辑名称，并不对应 `.one` 文件或目录；以文件名规则清洗 Page title 会直接破坏默认 Copy fidelity。

与此同时，当前 hierarchy `path` 使用 `/` 拼接原始名称。若 Page title 原样包含 `/`，例如：

```text
Notebook/Section/Topic / Subtopic
```

该字符串只能作为 display path，不能可靠反向拆分。`get_hierarchy_path(object_id)` 已按 exact ID 查询并返回 `item + ancestors`，所以结构身份本身不依赖扁平 path；但公开响应仍缺少明确、可逆、机器可消费的分段表示。不能通过篡改 Page title 来维持扁平 path 的表面可分割性。

### 统一改善方向

1. `CopyService._destination` 按资源类型区分命名规则：Notebook、SectionGroup、Section 继续使用 filesystem-safe leaf；Page title 必须完全绕过 `MutationService.safe_leaf_name` 及其他文件名清洗。默认 Copy 精确保留 source title；显式 `destination_title` 只经过独立的 Page-title 合法性/非空校验，不得替换或删除 `/`、`\\`、`:` 等合法 Page 标题字符。
2. Page target 创建和回读优先以 `allocated page ID + resource_type=page + exact section_id + exact title` 验证。只有 COM ID remap 时，才允许用“精确父 Section ID + 原始标题 + 本次新出现的唯一 ID”回退；不得把含分隔符的扁平 path 当 Page 身份证明。
3. `get_hierarchy_path` 增加 additive、结构化的 `path_segments`，每段至少返回 `resource_type`、exact `id` 和原始 `name/title`。`path_segments` 是规范的、可逆的 hierarchy 表示和机器消费入口，逐段保留标题中的 `/`、`\\`、`:`、`~`、`%` 和 Unicode；实现不得为了维持扁平 `path` 的可拆分性而清洗 Page title。
4. 现有 `path` 暂时保留为兼容性的 display-only 字段，不得宣称可反向解析。若仍需要单字符串可逆显示，可另增 `escaped_path`，按逐 segment 的固定规则编码（例如 JSON Pointer：`~ -> ~0`、`/ -> ~1`），不能静默改变旧 `path` 语义。
5. mutation 继续只接受 exact ID 作为主要选择器；任何 exact-path 兼容解析都必须拒绝歧义，不能根据拆分后的标题猜测目标。

### 自动化合同

- 默认 Page Copy/Move 分别对包含 `/`、`\\`、`:` 的标题证明 source→transformed→target title 全部相等，且 `title_override_requested=false`；
- 显式 Page rename 与默认保真路径分别覆盖，容器 Copy 的 filesystem-safe 行为保持不变；
- Page 创建验证覆盖标题中的 `/`、`\\`、`:`、`~`、`%`、重复空格和 Unicode，不因 display path 分隔或文件名规则产生误拒绝、字符清洗或错误 ID remap；
- `get_hierarchy_path` 的 `path_segments` 能无损重建每一级原始名称和 ID；同名、含 `/` 标题及重复 display path 仍以 ID 区分；
- legacy `path` 保持 display-only 兼容，结构化字段与 `ancestors/item` 一致，响应预算和 content-free hierarchy 边界不变；
- 负向测试证明错误 parent、错误 title、旧对象、重复候选和歧义 path 全部 fail closed。

### 完成定义

- [x] Page title 与 filesystem leaf name 已在实现和公开契约中分离；
- [x] 默认及显式改名的 Page Copy/Move 均绕过文件名清洗，并精确保留包含 `/`、`\\`、`:` 等字符的 Page title；
- [x] `get_hierarchy_path` 返回 additive、可逆的 `path_segments`，旧 `path` 明确为 display-only；
- [x] Page 创建/Copy 回读不再依赖可歧义的扁平 path，并保持 ID remap fail-closed；
- [x] 聚焦测试、完整 pytest、README、设计文档和 manual-validation 合同已同步；
- [x] 用户前台运行自动 `hierarchy-navigation`、`copy-page` 与 `move-page` 场景，证明 `path_segments`、默认 Copy 标题和默认 Move 标题均保真，且没有削弱 Copy-before-delete lossless gate。

## 事项：Table 列宽规范化与 typed Page equivalence 失败

### 已观察证据

2026-08-18 的第二个用户前台代表性内容 Copy case 已通过 v4 bootstrap，但在 `move_page` 的 Copy 回读阶段停为 `copy_only`。文档只保留 content-free 结论，不记录真实标题、对象 ID、Notebook 名称、精确 run 标识、投影 hash 或正文。证据将失败范围锁定为：

- `source → transformed` 的标题、富文本/List/Table/Outline 与 binary 全部一致，排除 Copy 转换阶段；
- `transformed → target` 的标题、可见文本、Image binary、能力集合、对象种类/数量、表格行列/单元格数量以及富文本 markup 数量均一致；
- 唯一三个 mismatch 都是同一 Table 前三列的 `Column.width`，相对变化分别约为 `0.65%`、`1.01%` 和 `1.06%`；
- 六次回读都保持同一差异，因此不是 timeout 或尚未收敛；
- source untouched 且未删除，target 保留，Copy-before-delete 门限按设计 fail closed。

根因是 `semantic_content_v1` 当前把 Table `Column.width` 作为普通稳定字符串精确比较。OneNote COM 在写入后会重新计算少量列宽，即使标题、正文、单元格语义、对象和 binary 都没有变化，也会使整个 `rich_list_tag_table_outline` check 变为 false。该问题属于 transformed→target 的写后 Table layout 规范化，不应通过忽略整个 Table 或把所有几何差异视为等价来修复。

### 已决定的比较规则

1. `source → transformed` 继续要求 Table 列宽精确保留，不允许转换代码主动改写宽度。
2. 只有 `transformed → target` 的 OneNote COM 回读允许对同一 Table、同一 ordinal Column 的 `width` 使用数值容差；判定公式固定为 `abs(target - expected) / abs(expected) <= 5%`，边界值 `5%` 视为通过。
3. `width` 必须能解析为有限、正数 Decimal。缺失、非数值、零、负值、`NaN` 或无穷值一律 fail closed，不得落入容差分支。
4. 容差只覆盖 `Column.width`。Table 数量、列数、列顺序、Row/Cell 拓扑、正文、RichText 有效样式/链接、List/Tag、Image/binary、其他属性以及新增/缺失对象仍须精确或按各自既有 typed comparator 校验。
5. 任一列超过 `5%`、无法建立同 ordinal 映射，或同时出现其他 Table 语义差异时，Page equivalence 必须失败并继续阻止 Move 删除源 Page。
6. content-free evidence 记录允许阈值、observed relative delta、Table/Column ordinal、字段名和判定结果；不得保存单元格正文、标题、raw XML 或 binary。允许记录数值宽度用于本地 run evidence，但版本库文档只保留相对变化结论。

### Typed equivalence failure 合同

当前公开失败只给出 `semantic_content=false` / `rich_list_tag_table_outline=false` 和无类型的 projection path，调用方无法判断失败来自 RichText、List、Tag、Table、Outline、Image 或其他内容对象。Page equivalence 失败必须新增有界、content-free 的 typed failure 列表，同时保留现有总布尔字段用于兼容：

```json
{
  "failed_content_object_types": ["Table"],
  "content_object_failures": [
    {
      "code": "table_column_width_out_of_tolerance",
      "content_object_type": "Table",
      "component_type": "Column",
      "field": "width",
      "table_ordinal": 0,
      "column_ordinal": 1,
      "comparison": "relative_tolerance",
      "allowed_relative_delta": 0.05,
      "observed_relative_delta": 0.08,
      "content_exposed": false
    }
  ]
}
```

具体要求：

- typed failure 至少区分 `PageTitle`、`RichText`、`List`、`Tag`、`Table`、`Outline`、`Image`、其他已验证 binary object，以及 projection incomplete/unknown；不能只返回一个复合 `rich_list_tag_table_outline` 类型；
- `code` 必须稳定并指向可行动的子类型，例如 `table_column_width_out_of_tolerance`、`table_cell_content_mismatch`、`rich_text_effective_style_mismatch`、`list_marker_mismatch`、`tag_state_mismatch`、`image_binary_mismatch`、`semantic_projection_incomplete`；
- 一个 Page 同时存在多类失败时，`failed_content_object_types` 去重排序，`content_object_failures` 按稳定的 Page/object/path 顺序输出，并受固定条数上限约束；超限必须显式记录 `reported/truncated/total`；
- mismatch path 只能作为 typed failure 的辅助定位，不得替代 `content_object_type` 和稳定 `code`；无法安全解码 path 时返回 fail-closed 的 `semantic_mismatch_unclassified`，不得静默丢弃；
- Copy/Move 的顶层 `partial_failure`、`failed_step=verify_copy`、`copy_only`、`source_untouched` 与 `source_deleted=false` 安全语义保持不变；typed failures 只提升诊断能力，不能降级失败或授权删源。

### 自动化合同

- `Column.width` 在 transformed→target 相对变化小于、等于 `5%` 时通过，大于 `5%` 时失败；覆盖正负方向、Decimal 字符串格式差异和精确边界；
- 同一宽度样本在 source→transformed 仍按精确值比较，证明容差不会掩盖转换阶段改写；
- 列数、顺序、Table/Row/Cell 拓扑或非 width 属性变化即使宽度在容差内也必须失败；
- 非法/缺失 width、零基准、非有限值和无法匹配 ordinal 的情况全部 fail closed；
- 每类既有 semantic mismatch 至少有一个 typed failure 合同，Table width 超限必须返回 `content_object_type=Table` 与 `code=table_column_width_out_of_tolerance`；
- 多类型、多对象和超限列表覆盖稳定排序、去重、截断元数据与 `content_exposed=false`；
- 既有调用方继续能读取总 `checks`、`equivalent`、`lossless` 和 `copy_contract_satisfied`，新增 typed 字段为 additive contract。

### 完成定义

- [x] transformed→target Table `Column.width` 已实现最大 `5%` 的 Decimal 相对容差，source→transformed 仍保持精确；
- [x] 超出容差及所有非 width Table 变化继续 fail closed，并阻止 Move 删除源 Page；
- [x] Page equivalence 按失败 content object 类型返回稳定、有界、content-free 的 typed errors；
- [x] 生产 Copy/Move response、manual-validation diagnostic、公开契约和设计文档已同步；
- [x] 聚焦测试、完整 pytest 与相关 dry-run 通过；
- [x] 用户以前台 `move-page` 自动创建的三列 Table fixture 复测，确认容差内列宽规范化不再误阻塞 Move，且明显列宽/内容变化仍由纯负合同拒绝。

## TODO 040 总体完成定义

- [x] Page title 完全绕过 filesystem leaf 清洗，`get_hierarchy_path.path_segments` 提供保留原始标题的可逆结构表示；
- [x] 完整投影的纯 RichText Page 获得适用的 typed semantic verification 与分段诊断，未知/不完整内容仍 fail closed；
- [x] Table 列宽容差和按 content object 类型返回的 equivalence errors 满足本文全部正负合同；
- [x] 最终自动 path/Copy/Move 三场景真实回归通过；Move 达到 `verified/lossless/copy_contract_satisfied=true` 后才非永久删除 disposable source；
- [x] 用户确认从 039 接管的剩余范围全部闭环并批准关闭本 P0。

## 2026-08-18 实现与纯验证进度

代码和公开合同已交付：Page title 使用独立的逻辑标题校验，不再经过 filesystem leaf 清洗；Page 创建与 Copy 以 allocated exact ID、exact Section ID 和 exact title 回读，仅在 allocated ID 消失时接受同父级、同原始标题、本次唯一 fresh ID remap。`get_hierarchy_path` 已 additive 返回逐段保留 exact ID 和原始名称/title 的 `path_segments`，legacy `path` 明确为 display-only。

`semantic_content_v1` 已覆盖完整投影的纯 RichText Page；source→transformed Table width 保持精确，transformed→target 仅对同 ordinal `Column.width` 应用最大 5% 的有限正 Decimal 相对容差。缺失/非法 width、列映射失败、其他 Table 属性/拓扑/单元格差异和未知投影继续 fail closed。2026-08-18 后续 representative Move 把剩余差异限定到样式 run 的两个文本分段 path，同时完整 visible text 与 style/markup 计数一致；当前 inline projection 只把边界空白的 style 中性化后合并，仍精确比较完整文本序列和每个非空白字符的有效 style/link 归属。Page equivalence additive 返回稳定、最多 24 条、带显式截断统计且 `content_exposed=false` 的 typed failures；自动 `copy-page` / `move-page` 场景消费脱敏类型、ordinal、阈值和 relative delta，并独立验证 exact target title。后续加固还让 service 按单一 content category 抛出对应 `PageReadbackMismatch` 子类，多类别和未知类别分别使用 `PageMixedContentReadbackMismatch` / `PageUnknownContentReadbackMismatch`；Move 的 `copy_only` 归一化保留原子类，MCP 顶层仍兼容为 `partial_failure`。`interactive-move-page-content` 不承担这些程序化验收；TODO 041 后它是 fresh/cache 统一入口。

自动回归由三个既有 scenario 承担。`hierarchy-navigation` recipe v4 自动创建标题 `01-Readback / Page\\:  %~界`，并以 exact Page ID 验证 `path_segments` 中 Notebook→SectionGroup→Section→Page 的原始名称和 ID；legacy `path` 只作 display。`copy-page` recipe v14 对同一特殊标题富内容父页保留既有六 case 矩阵，其中 cross-Notebook root-only 省略 `destination_title` 并强制三段标题回读。`move-page` recipe v7 的 root-only 源通过公开 `create_page(content_format=html)` 自动创建 RichText 与 3 列 × 2 行 Table；fixture snapshot 在 Move 前证明精确 Table/Column/Row/Cell 形状、有限正列宽和完整 projection，随后省略 `destination_title` 并执行严格 Copy-before-delete。其 subtree 父子页自动创建为无 Table/binary 的 RichText，fixture 与执行结果分别要求完整 projection 和逐页 `semantic_content_v1`。`interactive-move-page-content` 现为 v11 unified authored-template 场景，fresh/cache 共用同一 fingerprint，不承担本 TODO 的程序化验收。

用户前台 `run-2026-08-18-19-33-05` 已真实通过 `hierarchy-navigation`：顶层和 scenario 均为 `passed`，`hierarchy_path_segments_passed=true`，特殊字符 title 的 raw segment、exact ID 链和 metadata-only audit 全部通过，两个 disposable Notebook role 均精确关闭并保留。`move-page` 的 `run-2026-08-18-19-39-02` 也已真实通过：默认 root-only case 的自动三列表格 fixture 以完整 `semantic_content_v1` 返回 `all_equivalent=true` 并精确保留特殊标题，subtree 两页逐一通过纯 RichText 语义回读；本次 backend 列宽为精确相等，因此 content-free width-delta 列表为空，非零正负/5% 边界及超限/内容变化仍由纯正负合同覆盖，不能扩大解释为真实观察到非零规范化。两个 case 都先取得 `copy_verified=true`，再以 `source_deleted_nonpermanently=true` 删除 exact disposable source，顶层 lifecycle 精确关闭。`run-2026-08-18-19-49-05` 随后独立复跑也再次通过相同门限。

`copy-page` 的 `run-2026-08-18-19-34-01` 与 `run-2026-08-18-19-36-50` 都在首个 Copy 的 transformed→target 回读 fail closed；两次均未删源。第二次 content-free 诊断证明可见文本、对象、binary 与两条 MathML 投影相等，唯一首差异是 `Page/Title/OE` 在 canonical 属性名集合相同的前提下，其 `alignment/quickStyleIndex/style` 整体属性元组 canonical hash 不同；现有证据不声称三个值都分别变化。第一次修复新增 pairwise Title OE COM-style 规则及正确 `PageTitle/page_title_structure_mismatch` typed 诊断；`run-2026-08-18-19-47-32` 随后证明 eligibility 仍错误纳入了 canonical comparator 本就排除的 generated/volatile identity 属性，导致 title text 相等、OE 数量 `1/1`、canonical 首差异仅 `style` 时却报告 `attribute_sets_equal=false`。当前实现只改为复用既有 canonical 有效属性集合，不新增忽略项；仅当 exact title 文本、可识别 Title 结构、OE 数量和该集合均一致时归一化三个值。有效属性增删、其他属性、正文 OE style、标题文本、MathML 和其余内容仍 fail closed。

`run-2026-08-18-19-54-39` 已证明 comparator 修正有效：前四个 same/cross Section case 全部完成，cross-Notebook root-only 默认标题 case 本身也为 `verified/lossless/copy_contract_satisfied=true`，目标 item 精确保留特殊标题，DisplayEquation comparator 通过；场景只因该 tier 没有 `semantic_content_stages` 而在通用标题证据断言处 fail closed，两个 lease 均精确关闭。生产 Page result 现 additive 返回所有 tier 共用的 `title_readback_stages`：plan 独立记录显式改名意图，source→transformed 与 transformed→target title check 都参与 lossless gate；`semantic_content_stages` 继续只表达 `semantic_content_v1`。场景改为消费通用 title stages，不把 DisplayEquation 错当 semantic-content Page，也不降低 Copy fidelity。

修复后的用户前台 `run-2026-08-18-20-03-25` 已完整通过 `copy-page`：顶层与 scenario 均为 `passed`，同 Section、跨 Section、跨 Notebook 的 root-only/subtree 六个自动 case 全部 `verified/lossless/copy_contract_satisfied=true`，零 issue、零 skipped content，共映射 9 个 fresh target。每个 Page 的通用 `title_readback_stages` 两段均通过且 `content_exposed=false`；cross-Notebook root-only 明确省略 `destination_title`，回显 `title_override_requested=false`，source→transformed 与 transformed→target 默认标题检查均为 true。富内容父页继续以 `semantic_display_equation` 验证可见文本、对象、Image binary、MathML 与受限 Title OE COM-style 规范化，子页以 `semantic_list_tag` 验证；所有 collision anchor 均保持不变。场景反向非永久清理全部 9 个目标，`restored=true`，source/destination 两个 disposable Notebook 均精确关闭，working 文件与证据保留，未删除 filesystem 内容。结合已通过的 `hierarchy-navigation` 与两次 `move-page`，最终自动 path/Copy/Move 真实回归已闭合。

本轮纯验证已通过：完整 pytest `1421 passed`、manual-validation 纯合同 `636 passed`、三个专用 scenario `--keep-worksite --dry-run --json` 全部通过，`all --dry-run` 18/18。该历史验证发生在 TODO 041 统一入口之前；当前 dry-run 应证明 `interactive-move-page-content` fresh 路径包含 bootstrap，`--use-cache` 路径确定性跳过。纯合同覆盖默认 Page Copy/Move 的完整共享 Copy→语义回读链、特殊标题三段一致、三列表格形状、纯 RichText 的逐页 `semantic_content_v1`、Title OE pairwise 正负边界、path segment 原样名称与 fail-closed 负向条件。用户前台真实 `hierarchy-navigation`、`copy-page` 与 `move-page` 现已全部通过；三个场景均自行创建并机器验收 disposable fixture，没有使用 interactive template 或任何手工夹具。`interactive-move-page-content` 继续只承担 authored-template 开发验证职责。用户已确认从 039 接管的剩余范围全部闭环并批准关闭本 P0。
