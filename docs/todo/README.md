# 项目 TODO 索引

本目录记录尚未实施、正在实施、阻塞、已完成或已取消的项目级工作。索引是状态和优先级的快速视图；具体范围、设计与完成定义以编号文档为准。

## 待办列表

| ID | 待办 | 状态 | 优先级 | 说明 |
| --- | --- | --- | --- | --- |
| 001 | [本地程序化 OneNote 隔离验证 Runner](001_programmatic_isolated_mutation_runner.md) | 已完成 | P1 | 扁平 scenario 唯一入口、单命令隔离闭环、最小权限、证据和保留式生命周期已交付；用户确认真实验收矩阵完成。 |
| 002 | [P2 四层 Copy 与 Page Move](002_p2_copy_and_reconstructive_page_move.md) | 已完成 | P2 | 四层 Copy 与严格 Page Move 已交付；用户确认五个统一 fixture 场景全部完成真实成功闭环。 |
| 003 | [Scenario 独立 Fixture 与单 MCP 进程闭环](003_scenario_scoped_mcp_and_fixtures.md) | 已完成 | P2 | 单 MCP/最小 fixture 架构、性能实测和修复后的严格 `copy_only` 安全门均已验证。 |
| 004 | [交互式 Copy/Move 未验证内容保真验收](004_interactive_copy_move_content_fidelity_validation.md) | 待办 | P2 | 由用户在隔离 fixture Page 中加入附件、墨迹、媒体和会议详情，分离完成 Copy 取证与严格 Move 发布门。 |
| 005 | [Page Copy 可选排除缩进子树](005_page_copy_without_indentation_subtree.md) | 待办 | P2 | 为 `plan_copy`/`copy_page` 增加纳入计划摘要的单页范围选项，默认仍复制完整缩进子树。 |
| 006 | [Typed Section 与 SectionGroup Reorder](006_typed_section_and_section_group_reorder.md) | 已完成 | P1 | Section typed 同父级排序已确认；SectionGroup 后端仅固定名称升序，最终契约明确不支持并拒绝。 |
| 007 | [跨版本兼容性证据与环境元数据](007_cross_version_compatibility_evidence.md) | 待办 | P3 | 后续设计非阻塞、local-only 的环境识别与跨版本验证矩阵；当前场景不要求用户填写版本或 channel。 |
| 008 | [全部已打开 Notebook 的全局 Page 搜索](008_all_open_notebooks_search_scope.md) | 待办 | P1 | 为 `search_pages` 增加 `all_open_notebooks` scope，以单次全局预算支持类似 Desktop `Ctrl+E` 的跨 Notebook 检索。 |
| 009 | [Typed Reparent 工具与隐藏 Raw Hierarchy XML](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) | 已完成 | P1 | 三个 typed 工具、统一门控与生产隐藏已交付；用户确认三个迁移后的具名场景全部通过。 |
| 010 | [Manual Validation Dry-run 自动测试用例注册](010_registered_dry_run_test_cases.md) | 待办 | P1 | 将文档 dry-run 命令背后的参数组合收敛为结构化 registry case，由 pytest 自动发现并以纯 plan、CLI 守卫和零副作用合同运行。 |
| 011 | [Scenario 自管理 Fixture Recipe 与拆分集中式 Fixtures](011_scenario_owned_fixture_recipes.md) | 待办 | P1 | 让每个 Scenario 显式持有自己的 fixture recipe，将 1439 行中央构建/验证 switch 拆为场景模块、共享原语和无分支 runtime。 |
| 012 | [跨 Notebook Section 与 SectionGroup 重建式 Move](012_reconstructive_section_and_section_group_move.md) | 待办 | P2 | 基于现有容器 Copy 与 typed Delete，设计只允许跨 Notebook、严格 Copy-Verify-Delete、独立门控的 `move_section` / `move_section_group`。 |
| 013 | [Reparent 默认落点与 Agent 可见顺序合同](013_reparent_default_placement_contract.md) | 待办 | P2 | 先验证并固化 Page/Section 的默认末位合同，通过 tool 描述和结构化响应告知 Agent；自定义位置继续显式调用独立 Reorder。 |

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
