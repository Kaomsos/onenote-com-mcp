# 项目 TODO 索引

本目录记录尚未实施、正在实施、阻塞、已完成或已取消的项目级工作。索引是状态和优先级的快速视图；具体范围、设计与完成定义以编号文档为准。

## 待办列表

| ID | 待办 | 状态 | 优先级 | 说明 |
| --- | --- | --- | --- | --- |
| 001 | [本地程序化 OneNote 隔离验证 Runner](001_programmatic_isolated_mutation_runner.md) | 进行中 | P1 | 扁平 scenario 唯一入口、单命令隔离闭环、gated 最小权限与保留式生命周期已实现，等待用户本人实测。 |
| 002 | [P2 四层 Copy 与 Page Move](002_p2_copy_and_reconstructive_page_move.md) | 进行中 | P2 | 实验实现与具名 Runner 场景已落地，等待真实 OneNote 分阶段验证。 |
| 003 | [Scenario 独立 Fixture 与单 MCP 进程闭环](003_scenario_scoped_mcp_and_fixtures.md) | 已完成 | P2 | 单 MCP/最小 fixture 架构、性能实测和修复后的严格 `copy_only` 安全门均已验证。 |
| 004 | [交互式 Copy/Move 未验证内容保真验收](004_interactive_copy_move_content_fidelity_validation.md) | 待办 | P2 | 由用户在隔离 fixture Page 中加入附件、墨迹、媒体和会议详情，分离完成 Copy 取证与严格 Move 发布门。 |
| 005 | [Page Copy 可选排除缩进子树](005_page_copy_without_indentation_subtree.md) | 待办 | P2 | 为 `plan_copy`/`copy_page` 增加纳入计划摘要的单页范围选项，默认仍复制完整缩进子树。 |
| 006 | [Typed Section 与 SectionGroup Reorder](006_typed_section_and_section_group_reorder.md) | 已完成 | P1 | Section typed 同父级排序已确认；SectionGroup 后端仅固定名称升序，最终契约明确不支持并拒绝。 |
| 007 | [跨版本兼容性证据与环境元数据](007_cross_version_compatibility_evidence.md) | 待办 | P3 | 后续设计非阻塞、local-only 的环境识别与跨版本验证矩阵；当前场景不要求用户填写版本或 channel。 |
| 008 | [全部已打开 Notebook 的全局 Page 搜索](008_all_open_notebooks_search_scope.md) | 待办 | P1 | 为 `search_pages` 增加 `all_open_notebooks` scope，以单次全局预算支持类似 Desktop `Ctrl+E` 的跨 Notebook 检索。 |
| 009 | [Typed Reparent 工具与隐藏 Raw Hierarchy XML](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) | 待办 | P1 | 注册三类同 Notebook typed `reparent_*`，并从所有生产 MCP profile 移除可提交任意 XML 的 `update_hierarchy_xml`。 |

## 文件命名

- 格式：`NNN_<short_slug>.md`，例如 `002_example_task.md`；
- `NNN` 是从 `001` 开始、只增不减的三位数字 ID；
- 文件创建后不得因排序、状态或优先级变化而重新编号；
- 删除或取消待办时不得复用其 ID。

## 状态

| 状态 | 含义 |
| --- | --- |
| `待办` | 范围已记录，尚未开始实施。 |
| `进行中` | 已开始实施，尚未满足完成定义。 |
| `阻塞` | 因明确依赖或决策暂停；具体文件必须记录阻塞原因。 |
| `已完成` | 完成定义和必要验证均已满足。 |
| `已取消` | 明确决定不再实施；具体文件必须记录原因。 |

## 优先级

| 优先级 | 含义 |
| --- | --- |
| `P0` | 阻塞核心使用、安全或发布，需最先处理。 |
| `P1` | 重要能力或近期基础设施，应优先规划实施。 |
| `P2` | 有价值但不阻塞当前核心流程。 |
| `P3` | 低紧迫度的改进、探索或清理工作。 |

## 维护流程

1. 创建待办时取当前最大 ID 加一，并同时新增编号文件和索引行。
2. 编号文件顶部必须包含 `ID`、`状态`、`优先级` 和 `更新日期`。
3. 状态、优先级、标题或路径变化时，在同一变更中更新本索引。
4. 标记 `已完成` 前，逐项核对编号文件中的完成定义和验证结果。
5. `已完成`、`已取消` 的文档继续保留在本目录，确保链接和历史可追踪。
