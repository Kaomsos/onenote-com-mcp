# Advanced/低层操作

本文定义 Local OneNote MCP 的 advanced profile：用于开发、诊断和受控能力探测的低层入口。它不是默认 typed 对象模型的一部分，因此其中的工具不参与对象—操作矩阵的 `T/E/X` 评级，也不能据其存在推导对应对象操作已经成为产品契约。

## 1. 注册与安全边界

只有在进程启动前设置 `LOCAL_ONENOTE_ENABLE_RAW_XML=true`，`tools.advanced` 中的 7 个工具才会注册。注册只改变可见工具面，不授予 mutation 权限；service 层仍按操作复核 Writes、Deletes、Permanent Deletes 与 raw XML policy。

Advanced 工具允许路径、generic identifier 或原始 XML 等低层输入，不具有默认 typed mutation 的统一精确 ID、对象类型、confirmation fields 和操作后语义验证合同。不得用 advanced 工具绕过 typed 工具的拒绝结论，也不得将 raw XML 成功等同于稳定对象能力。

## 2. 工具目录

| 工具 | 低层用途 | 执行门限与边界 |
| --- | --- | --- |
| `find_meta` | 从已解析的 hierarchy 起点调用 OneNote metadata search，并返回底层 XML 映射结果 | 只读诊断；不是 typed Query/Search 的替代品 |
| `open_hierarchy` | 按路径打开 hierarchy；指定创建类型时也可创建对象 | 创建或未找到后继续打开时要求 Writes；允许路径定位，不作为 typed mutation 目标合同 |
| `delete_hierarchy` | 使用 generic hierarchy 标识符删除对象 | 要求 raw XML 与 Deletes；永久删除还要求 Permanent Deletes；Notebook 删除仍被拒绝 |
| `update_page_xml` | 直接提交原始 Page XML | 要求 raw XML 与 Writes；绕过 typed Page 操作形状，但不绕过 policy |
| `update_hierarchy_xml` | 直接提交原始 hierarchy XML | 要求 raw XML 与 Writes；调用方负责提供完整、合法且有界的 XML |
| `merge_sections` | 调用底层 Section 合并操作 | 要求 raw XML 与 Writes；没有稳定 typed Merge 契约 |
| `set_filing_location` | 设置 OneNote filing location | 要求 Writes；仅因属于 advanced 工具集而受 advanced profile 注册条件限制 |

当前注册列表以 `src/local_onenote_mcp/tools/advanced.py` 为准；profile 组合逻辑位于 `src/local_onenote_mcp/tools/__init__.py`。公开 typed 工具的参数和 policy 见 [工具参数与返回格式](tool_contracts.md)。

## 3. Reparent 探针与产品能力边界

Page 与 SectionGroup Reparent 已由用户在全新隔离 Notebook 中通过人工场景：

- Page 在两个 Section 之间换父级，并允许 OneNote 对 Page 和内容对象 ID 做一对一重映射；RichText、Table、List、Tag 与 Image 按场景合同保持；
- SectionGroup 覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup 三条同 Notebook 路线，并验证后代关系和 Page 内容。

这两个场景使用由 runner 根据 disposable manifest 精确 ID 生成的受控 `update_hierarchy_xml`，不接受用户传入的任意 XML。它们证明当前环境的底层 COM 能力，但当前仍没有独立的 `reparent_page` 或 `reparent_section_group` typed 工具。因此：

- 对象—操作矩阵中的 Page/SectionGroup Reparent 保持 `X：无 typed 工具`；
- 已验证的人工证据继续记录在评估和 manual-validation 文档中；
- 若未来升级为 `E`，必须新增独立 typed 工具、confirmation/read-back 合同、独立安全门和对应自动化合同，而不能直接暴露 raw XML。

人工验证的精确场景与权限边界见 [隔离 mutation 验证](../dev/isolated_mutation_validation.md) 和 [`tests/manual_validation/README.md`](../../tests/manual_validation/README.md)。

## 4. 与默认 typed profile 的关系

默认 profile 始终只注册经过对象化封装的工具。Advanced profile 是附加工具集，不替换默认工具，也不会把 `X` 能力自动升级为 `E`：

- `T`：默认注册的稳定 typed 契约；
- `E`：默认注册、由独立实验 policy 保护的 typed 契约；
- `X`：没有 typed 产品契约或明确拒绝；
- advanced/低层操作：在本文件单独记录，不进入上述评级。

对象级能力总览见 [OneNote 对象模型评估](../overview/onenote_object_model_assessment.md)，静态对象字段见 [OneNote 对象模型](object_model.md)。
