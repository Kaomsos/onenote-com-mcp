# 009：Typed Reparent 工具与隐藏 Raw Hierarchy XML

> ID：009
> 状态：已完成
> 优先级：P1
> 类型：公开 mutation 契约 / 工具注册与安全收敛
> 更新日期：2026-08-10

## 背景

当前仓库对三类同 Notebook 换父级操作采用了不一致的产品边界：

- Section 已有 typed `reparent_section` 工具，包含精确 ID、confirmation、同 Notebook 检查和写后验证；
- Page 与 SectionGroup 已有用户确认通过的 `reparent-page`、`reparent-section-group` 人工验证场景，但场景仍通过 advanced MCP 工具 `update_hierarchy_xml` 提交原始 hierarchy XML；
- `update_hierarchy_xml` 只有在 `LOCAL_ONENOTE_ENABLE_RAW_XML=true` 时注册，并同时要求 Writes 与 Raw XML 权限，但其参数仍是调用方可自由提供的任意 XML，无法在工具签名层限定对象类型、Notebook 边界、目标父级、confirmation 或写后不变量。

底层 bridge 的 `update_hierarchy` 是 OneNote COM `UpdateHierarchy` 的必要内部原语，typed Reorder/Reparent 等实现仍需使用。需要隐藏的是接受任意 XML 的公开 MCP 工具 `update_hierarchy_xml`，不是内部 bridge operation。

## 术语与产品边界

`Reparent` 统一表示 Notebook 内部的层级换父级：

- Page：Section → 同 Notebook 的另一个 Section；
- Section：Notebook/SectionGroup → 同 Notebook 的 Notebook/SectionGroup；
- SectionGroup：Notebook/SectionGroup → 同 Notebook 的 Notebook/SectionGroup；
- 不跨 Notebook，不执行 Copy/Delete，不属于同父级 Reorder；
- 不统一承诺保持 ID：Section 和 SectionGroup 应保持自身及后代 ID；Page 必须接受并显式报告 OneNote 原生操作造成的 Page/内容对象 ID 一对一重映射。

跨 Notebook 转移不属于 Reparent。若未来支持，应按 Move 的 Copy→验证→非永久删除源语义另行设计。

## 目标工具契约

默认 MCP profile 注册以下三个 typed 工具，但注册不等于授权执行：

| 工具 | 目标父级 | 必需 confirmation | 核心写后验证 |
| --- | --- | --- | --- |
| `reparent_page` | 同 Notebook Section | `page_id`、`destination_section_id`、`expected_title`、`expected_section_id`、可选 `expected_modified` | 唯一 Page 身份或明确 ID 重映射、目标 Section、Page 拓扑、RichText/Table/List/Tag/Image 内容、无关对象不变 |
| `reparent_section` | 同 Notebook Notebook/SectionGroup | `section_id`、`destination_parent_id`、`expected_name`、`expected_parent_id`、可选 `expected_modified` | Section ID、Page ID/顺序、Page 内容、目标父级 |
| `reparent_section_group` | 同 Notebook Notebook/SectionGroup | `section_group_id`、`destination_parent_id`、`expected_name`、`expected_parent_id`、可选 `expected_modified` | Group 与全部后代 ID、父子关系、Section/Page 拓扑和 Page 内容 |

三个工具使用统一响应骨架：

- `item`：操作后当前对象；
- `previous_parent_id` 与 `destination_parent_id`；
- `id_map`：未发生重映射时至少包含目标自身的 identity mapping；Page 发生重映射时包含 Page 与可观测内容对象的 old→new 映射；
- `verified`：按对象类型报告父级、身份、拓扑、内容和无关对象不变量；
- `warnings`：只表达当前 OneNote/Office 版本证据边界，不把警告用作跳过失败验证的机制。

## 安全与策略要求

- 三个工具都必须要求 Writes，并由统一、默认关闭的 Reparent 实验门保护；实施时应将现有仅针对 Section 的策略收敛为统一命名，并同步迁移 health-check 字段、环境变量、测试和文档；
- service 在任何 COM mutation 前验证对象类型、confirmation、同 Notebook 关系和合法目标类型；
- SectionGroup 必须在 mutation 前拒绝目标为自身或自身后代，不能依赖 COM 错误阻止循环；
- Page/Section/SectionGroup 都必须从有界 before snapshot 计算验证证据，操作后按精确 ID 或明确的 ID 映射回读；
- COM 返回成功但未观察到目标父级、映射不唯一、内容变化、后代关系变化或无关对象变化时必须 fail closed；
- 不接受调用方提供 XML、XPath、任意层级片段或绕过 confirmation 的 `force` 参数；
- 不得用 Rename、Copy/Delete 或直接编辑 `.one` 文件模拟 Reparent。

## 隐藏 `update_hierarchy_xml`

完成 typed 工具迁移后：

1. 从 `ADVANCED_TOOLS` 及所有生产 MCP 注册路径中移除 `update_hierarchy_xml`；即使设置 `LOCAL_ONENOTE_ENABLE_RAW_XML=true`，MCP 客户端也不能再枚举或调用该工具；
2. 保留 bridge 内部 `update_hierarchy` operation，供受约束的 typed service 使用；
3. service 层若仍保留 raw helper，只能作为非注册内部实现，不得成为可由 MCP 参数直接传入任意 XML 的公共入口；
4. `reparent-page` 与 `reparent-section-group` 人工场景改为调用对应 typed 工具，并从场景 policy/tool allowlist 中移除 Raw XML 权限与 `update_hierarchy_xml`；
5. 自动化测试明确断言 `update_hierarchy_xml` 不属于默认工具、advanced 工具或任何生产 profile，同时断言内部 bridge operation 仍只能由受约束 service 编排；
6. 文档不再把 `LOCAL_ONENOTE_ENABLE_RAW_XML` 描述为可以开放 raw hierarchy mutation；若 `update_page_xml` 等其他 advanced 工具仍保留，应逐项准确列出剩余范围。

## 实施范围

1. 提取或新增共享 Reparent service/helper，统一同 Notebook 校验、confirmation、before/after 证据和错误 envelope；
2. 保留并迁移现有 `reparent_section`，新增 `reparent_page` 与 `reparent_section_group` typed tool adapter 和 service 方法；
3. 将三个工具加入默认 tool registry，并以统一 Reparent policy 保持 fail closed；
4. 从生产 MCP 注册表删除 `update_hierarchy_xml`，清理不再需要的 advanced tool adapter；
5. 将三个 manual-validation 场景改为只调用 typed Reparent 工具，保持现有编号 fixture、Description、三种容器父级路线、富内容证据、恢复和 `--keep-worksite` 行为；
6. 补充纯自动化合同，覆盖工具 schema、默认注册、权限矩阵、同 Notebook 限制、错误目标类型、SectionGroup 循环、confirmation 过期、Page ID 重映射、内容/拓扑不变量、COM 成功但状态未变化，以及 raw hierarchy 工具不可见；
7. 同步更新根 README、`docs/design/tool_contracts.md`、`docs/design/object_model.md`、`docs/design/architecture.md`、`docs/dev/isolated_mutation_validation.md`、人工验证 README 和对象模型评估；
8. 真实 OneNote 回归只能由用户显式运行三个具名场景，Agent、pytest、CI、hook、timer 或 watcher 不得启动。

## 兼容性与迁移

- `reparent_section` 的现有参数和成功语义应尽量保持兼容；如统一 policy 导致环境变量或 health-check 字段改名，必须作为公开配置变更记录，不保留会重新扩大权限面的隐式别名；
- `reparent_page` 的调用方必须处理 `item.id` 与原 `page_id` 不同，并以 `id_map` 继续后续操作；
- `reparent_section_group` 不得因为当前环境验证成功而宣称跨 OneNote/Office 版本普遍保证；
- 依赖 `update_hierarchy_xml` 的外部开发调用不属于稳定兼容合同。移除时应返回“工具不存在”，而不是保留一个运行时拒绝但仍可枚举的生产工具。

## 实施进度与证据

截至 2026-08-10，代码、纯合同、文档迁移和用户把关的真实验证已经完成：

- 默认 profile 已注册 `reparent_page`、`reparent_section`、`reparent_section_group`，并统一使用 `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT`；旧的 Section-only 环境变量和 health-check 字段不保留别名；
- service 在调用内部 `update_hierarchy` 前执行 typed confirmation、同 Notebook、目标类型和 SectionGroup 防循环检查，并用受 Copy budgets 限制的 Notebook/Page before/after 快照验证 ID、拓扑、富内容与无关对象；
- Page 结果返回 Page 及可观测内容对象的 `id_map`；Section/SectionGroup 返回目标 identity mapping；
- `update_hierarchy_xml` 已从 advanced adapter、service 公共入口和所有生产注册路径移除；Raw XML 开关只会注册剩余 6 个 advanced 工具；
- `reparent-page` 与 `reparent-section-group` runner 已改为 typed tool 调用，三类 Reparent policy 均不要求 Raw XML；
- 聚焦生产合同、全部 manual-validation 纯测试和完整 pytest 均通过（`305 passed`）；三个场景的 `--dry-run --json` 均证明 `raw_xml_enabled=false`、统一 Reparent 门开启且 allowlist 只包含对应 typed `reparent_*`。
- 用户明确确认迁移后的 `reparent-page`、`reparent-section`、`reparent-section-group` 三个具名场景全部通过手动验证。场景分别只调用 `reparent_page`、`reparent_section`、`reparent_section_group`，其成功结果覆盖目标父级变化、Page 的精确一对一 ID 行为、Section/SectionGroup 身份保持、内容/后代拓扑不变量以及默认恢复。

上述真实结果来自用户本人显式运行；Agent 未启动真实 `run.py <scenario>` 或 `run.py all`。证据结论只适用于本次用户环境，不扩展为跨 OneNote/Office 版本保证。至此完成定义全部满足，本 TODO 状态更新为“已完成”。

## 完成定义

- 默认 MCP profile 可枚举 `reparent_page`、`reparent_section`、`reparent_section_group`，三者具有稳定 typed schema 和统一 Reparent 语义；
- 三个工具均只允许同 Notebook 换父级，并在 COM 调用前完成类型、confirmation、Notebook 边界和循环检查；
- Page ID 重映射、Section/SectionGroup ID 保持、后代拓扑和 Page 富内容均有明确写后验证与结构化返回；
- `update_hierarchy_xml` 不再出现在默认、advanced 或其他生产 MCP 工具列表中，设置 Raw XML 开关也不能将其注册；
- 内部 `update_hierarchy` bridge operation 继续可由 typed service 使用，但不存在接受外部任意 hierarchy XML 的生产 MCP 路径；
- manual-validation 三个 Reparent 场景均不再要求 Raw XML 权限，并继续保留现有真实证据强度；
- 相关纯自动化合同和完整测试集通过；
- 用户分别运行并确认迁移后的三个 typed Reparent 场景通过，证据中记录精确工具名、父级变化、ID 行为和内容/拓扑结果；
- 当前设计、开发指南、README、对象模型评估和 TODO 索引与最终实现一致。
