# Advanced/低层操作

本文定义 Local OneNote MCP 的 advanced profile：用于开发、诊断和受控能力探测的低层入口。它不是默认 typed 对象模型的一部分，因此其中的工具不参与对象—操作矩阵的 `T/E/X` 评级，也不能据其存在推导对应对象操作已经成为产品契约。

## 1. 注册与安全边界

只有在进程启动前设置 `LOCAL_ONENOTE_ENABLE_RAW_XML=true`，`tools.advanced` 中剩余的 5 个工具才会注册。注册只改变可见工具面，不授予 mutation 权限；service 层仍按操作复核 Writes 与 raw XML policy。该开关不能注册 generic delete 或 raw hierarchy mutation。

Advanced 工具保留路径、底层 COM operation 或原始 Page XML 等低层输入，不具有默认 typed mutation 的完整 confirmation 与操作后语义验证合同。涉及 hierarchy mutation 的保留入口已收紧为 exact typed ID；不得用 advanced 工具绕过 typed 工具的拒绝结论，也不得将 raw XML 成功等同于稳定对象能力。

## 2. 工具目录

| 工具 | 低层用途 | 执行门限与边界 |
| --- | --- | --- |
| `find_meta` | 从已解析的 hierarchy 起点调用 OneNote metadata search，并返回底层 XML 映射结果 | 只读诊断；不是 typed Query/Search 的替代品 |
| `open_hierarchy` | 按路径打开 hierarchy；指定创建类型时也可创建对象 | existing path 必须唯一，重复 typed path fail closed；创建或未找到后继续打开时要求 Writes |
| `update_page_xml` | 直接提交原始 Page XML | 要求 raw XML 与 Writes；绕过 typed Page 操作形状，但不绕过 policy |
| `merge_sections` | 以 `source_section_id/destination_section_id` 调用底层 Section 合并操作 | 只接受两个互异 exact Section ID；要求 raw XML 与 Writes；没有稳定 typed Merge 契约 |
| `set_filing_location` | 以 `section_or_page_id` 设置 OneNote filing location | 只接受 exact Section/Page ID；要求 Writes；仅因属于 advanced 工具集而受 advanced profile 注册条件限制 |

当前注册列表以 `src/local_onenote_mcp/tools/advanced.py` 为准；profile 组合逻辑位于 `src/local_onenote_mcp/tools/__init__.py`。公开 typed 工具的参数和 policy 见 [工具参数与返回格式](tool_contracts.md)。

`delete_hierarchy` 与 `update_hierarchy_xml` 已从 `ADVANCED_TOOLS`、service 公共入口和所有生产注册路径移除；即使 Raw XML 开关为 `true`，客户端也不能枚举或调用它们。bridge 内部的同名 COM operation 仍保留，仅由 typed Delete、Move、Reorder/Reparent 等受约束 service 以精确 ID 或内部构造 XML 调用。移除 generic delete 避免删除一个 ID 后再按相同 friendly path 追删合法重名 sibling。

## 3. Reparent typed 迁移与证据边界

Page 与 SectionGroup Reparent 已由用户在全新隔离 Notebook 中通过人工场景：

- Page 在两个 Section 之间换父级，并允许 OneNote 对 Page 和内容对象 ID 做一对一重映射；RichText、Table、List、Tag 与 Image 按场景合同保持；
- SectionGroup 覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup 三条同 Notebook 路线，并验证后代关系和 Page 内容。

这些既有真实证据已经封装为默认注册的 `reparent_page` 与 `reparent_section_group`，并与 `reparent_section` 共用 Writes + Reparent 实验门。runner 只提交精确 typed ID 和 confirmation fields，不再生成或传递 hierarchy XML。当前边界为：

- 对象—操作矩阵中的 Page/Section/SectionGroup Reparent 均为 `E`；
- 三个工具只允许同 Notebook，使用有界 before/after 快照并 fail closed；
- Page 接受并报告原生 Page/内容对象 ID 一对一重映射，Section/SectionGroup 要求自身和适用后代 ID 保持；
- 用户已确认三个迁移后的 typed 场景在当前环境全部通过；单环境通过不能升级为跨版本保证。

人工验证的精确场景与权限边界见 [隔离 mutation 验证](../dev/isolated_mutation_validation.md) 和 [`tests/manual_validation/README.md`](../../tests/manual_validation/README.md)。

## 4. 与默认 typed profile 的关系

默认 profile 始终只注册经过对象化封装的工具。Advanced profile 是附加工具集，不替换默认工具，也不会把 `X` 能力自动升级为 `E`：

- `T`：默认注册的稳定 typed 契约；
- `E`：默认注册、由独立实验 policy 保护的 typed 契约；
- `X`：没有 typed 产品契约或明确拒绝；
- advanced/低层操作：在本文件单独记录，不进入上述评级。

对象级能力总览见 [OneNote 对象模型评估](../overview/onenote_object_model_assessment.md)，静态对象字段见 [OneNote 对象模型](object_model.md)。
