# 项目 TODO 索引

本目录记录尚未实施、正在实施、阻塞、已完成或已取消的项目级工作。索引是状态和优先级的快速视图；具体范围、设计与完成定义以编号文档为准。

## 待办列表

| ID | 待办 | 状态 | 优先级 | 说明 |
| --- | --- | --- | --- | --- |
| 001 | [本地程序化 OneNote 隔离验证 Runner](001_programmatic_isolated_mutation_runner.md) | 已完成 | P1 | 扁平 scenario 唯一入口、单命令隔离闭环、最小权限、证据和保留式生命周期已交付；用户确认真实验收矩阵完成。 |
| 002 | [P2 四层 Copy 与 Page Move](002_p2_copy_and_reconstructive_page_move.md) | 已完成 | P2 | 四层 Copy 与严格 Page Move 已交付；增强后的 Section/SectionGroup 双范围与含嵌套组的 Notebook Copy 也已完成用户真实回归。 |
| 003 | [Scenario 独立 Fixture 与单 MCP 进程闭环](003_scenario_scoped_mcp_and_fixtures.md) | 已完成 | P2 | 单 MCP/最小 fixture 架构、性能实测和修复后的严格 `copy_only` 安全门均已验证。 |
| 004 | [交互式 Copy 未验证内容保真验收](004_interactive_copy_move_content_fidelity_validation.md) | 已完成 | P2 | InkDrawing、UIShape、MediaFile、InsertedFile 的逐类别 Copy 证据、生产 comparator 和静态保真 allowlist 已闭合；Move 统一复用 Copy 类别门禁。 |
| 005 | [Page Copy 默认仅复制单页，可选包含缩进子树](005_page_copy_without_indentation_subtree.md) | 已完成 | P2 | 默认单页与显式完整子树均已交付；用户已确认双 case 真实 OneNote 人工验收通过。 |
| 006 | [Typed Section 与 SectionGroup Reorder](006_typed_section_and_section_group_reorder.md) | 已完成 | P1 | Section typed 同父级排序已确认；SectionGroup 后端仅固定名称升序，最终契约明确不支持并拒绝。 |
| 007 | [跨版本兼容性证据与环境元数据](007_cross_version_compatibility_evidence.md) | 待办 | P3 | 后续设计非阻塞、local-only 的环境识别与跨版本验证矩阵；当前场景不要求用户填写版本或 channel。 |
| 008 | [全部已打开 Notebook 的全局 Page 搜索](008_all_open_notebooks_search_scope.md) | 已完成 | P1 | Index-only Tool、严格 scope、分页和预算已完成；fresh 与 validated cache hit 真实 Search 均通过，场景已纳入 `all`。 |
| 009 | [Typed Reparent 工具与隐藏 Raw Hierarchy XML](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) | 已完成 | P1 | 三个 typed 工具、统一门控与生产隐藏已交付；用户确认三个迁移后的具名场景全部通过。 |
| 010 | [Manual Validation Dry-run 自动测试用例注册](010_registered_dry_run_test_cases.md) | 已完成 | P1 | 32 个冻结 registry case、纯 plan builder、正式 parser/CLI sentinel harness 与 README 投影合同已交付；真实 `all` allowlist 未扩大。 |
| 011 | [Scenario 自管理 Fixture Recipe 与拆分集中式 Fixtures](011_scenario_owned_fixture_recipes.md) | 已完成 | P1 | 14 个 Scenario 已各自拥有唯一 recipe；中央 1439 行 fixture switch 已由增量 recorder、无分支 runtime、场景 validator 和共享 typed primitive 取代。 |
| 012 | [跨 Notebook Section 与 SectionGroup 重建式 Move](012_reconstructive_section_and_section_group_move.md) | 已完成 | P2 | typed 工具、独立门控与严格单次根删除已交付；用户确认 Section/SectionGroup 两个跨 Notebook 真实场景均通过。 |
| 013 | [Reparent Page 子树范围与 Mutation 目标位置回传合同](013_reparent_default_placement_contract.md) | 已完成 | P2 | 统一目标位置合同、Page 双范围、十场景位置矩阵和新范围场景均已交付；用户真实 fresh/cache 双 case 通过并批准将 `reparent-page-with-level` 纳入 `all`。 |
| 014 | [Recipe 驱动的不可变 Notebook 模板缓存与隔离工作副本](014_recipe_fixture_validation_and_local_notebook_cache.md) | 已完成 | P2 | 不可变 cache、单/多 role working bundle、失效恢复和四类 Interactive Copy 已闭合；用户确认最终全量真实回归全部通过。UserAuthored 完整化由 TODO 020 独立跟踪。 |
| 015 | [Mutation 目标精确定位收尾与重名 Page 回归](015_mutation_target_identity_hardening_and_duplicate_page_regression.md) | 已完成 | P1 | allocated-ID-first 与精确身份合同已交付；Create、v4 Page Copy、Move 及增强后的三个容器 Copy 均有用户真实成功证据，cleanup/close 与 cache immutability 门通过。 |
| 016 | [Page Copy 人工验证只读取证降本](016_copy_page_manual_validation_read_evidence_efficiency.md) | 已完成 | P3 | 单次 Page XML 复用和 cache 层级双稳定门已交付；用户真实 Copy Page 六 case 通过，重复 `get_page_objects`/Page XML 读取降为零并记录实际调用分类。 |
| 017 | [Page Move 可选子树与跨 Notebook 双范围验收](017_page_move_selectable_subtree_and_cross_notebook_validation.md) | 已完成 | P1 | 默认 root-only 与显式 subtree 已交付；用户确认修复后的跨 Notebook 双 case、保留后代和非永久删除均通过。 |
| 018 | [在线视频表示与 Copy 保真验证](018_online_video_copy_fidelity_validation.md) | 已取消 | P2 | 不再建立独立 OnlineVideo 类型或有损 Copy 合同；仅保留局限性 Lesson。 |
| 019 | [Manual Validation 受控 Clear Actions](019_manual_validation_clear_actions.md) | 已完成 | P2 | `clear runs/cache/all`、交互确认、实际路径快照、逐目标 receipt/summary、成功审计收敛和空 cache scaffold 清理已交付；用户真实 clear 共删除 112 个目标且无拒绝/失败。 |
| 020 | [UserAuthored Fixture 开发脚手架完整化](020_user_authored_fixture_development_scaffold.md) | 待办 | P3 | 当前骨架已够开发取证使用；完整 authoring-zone、多实例、ready/evidence-only 和失效真实矩阵延期，且不阻塞 TODO 014 或生产 Copy/Move。 |
| 021 | [Windows Fixture Cache 路径长度预算](021_windows_fixture_cache_path_budget.md) | 已完成 | P3 | 240 UTF-16 units、短 typed schema、结构化错误与一次性空壳切换已交付；用户完成升级前真实 `clear all`，v2 激活后默认全量 `890 passed`。 |
| 022 | [四层 Typed Metadata Query、原生 Scope 与 List 工具退役](022_typed_metadata_query_tools_and_native_scopes.md) | 阻塞 | P1 | 阶段 A 与真实 cache cold-build Query 已通过，场景纳入 `all`；阶段 B 仅阻塞于用户对五个 List 工具退役的独立批准。 |
| 023 | [公开仓库发布准备与来源合规](023_public_repository_release_readiness.md) | 待办 | P0 | 公开前完成品牌与 Demo、双语文档和社区规范、Credit/relicense、线性历史、原作者通知及隐私/供应链/发布验证。 |
| 024 | [Search 与 Typed Query 短时只读快照缓存](024_search_and_query_read_snapshot_cache.md) | 待办 | P2 | 规划进程内可配置 TTL（默认 15 秒）的 `GetHierarchy`/`FindPages` 缓存、mutation 前失效与 Agent 可见一致性合同；完成状态要求用户确认 `read-cache-coherence` 真实场景证据。 |
| 025 | [OneNote COM 收敛、Mutation 对账与调用协调](025_onenote_com_convergence_and_mutation_coordination.md) | 已完成 | P1 | typed HRESULT、公共收敛/对账、进程内协调与关键路径迁移已落地；918 个纯测试、dry-run 及用户前台 convergence/create/reorder/delete/copy/move 回归均通过。 |
| 026 | [Manual Validation 实时进度与 Verbosity](026_manual_validation_progress_verbosity.md) | 已完成 | P2 | 三级 content-free 实时进度与紧凑非 JSON summary 已落地；纯测试、完整基线和用户前台长/短场景展示均已确认。 |
| 027 | [Reparent 三层级人工验证矩阵与 `all` 覆盖](027_reparent_manual_validation_all_coverage.md) | 已完成 | P2 | 用户真实 `all --use-cache` 最新完整批次 15/15；Page、Section、SectionGroup 三个独立 child 全部命中验证缓存、通过、恢复并关闭，三层级矩阵与批处理资格闭合。 |
| 028 | [Reorder Section `all` 资格与 Progress 埋点](028_reorder_section_all_and_progress.md) | 已完成 | P2 | 用户真实批处理中 Section Reorder 两个正向 case、逆序 restore、progress 与最终关闭通过；SectionGroup 诊断继续排除。 |
| 029 | [MCP Tool Mutation Readiness 状态建模与 Page Reparent 加固](029_mcp_mutation_readiness_and_reconciliation_hardening.md) | 待办 | P1 | 以 Page Reparent 为纵向切片落实 execute-once 四态对账、reconciled success、恢复建议与生命周期负合同，并审计 MCP mutation tool 生态的 readiness/replay policy。 |
| 030 | [Manual Validation Cache 层级激活批处理与证据复用](030_manual_validation_cache_hierarchy_activation_batching.md) | 已完成 | P2 | 单 COM parent-first batch、Notebook 双稳定、每 Page 单次读取与 scenario-before snapshot handoff 已交付；用户真实单/多 role 及 `all --use-cache` 15/15 通过。 |
| 031 | [启动 OneNote Desktop GUI 的显式工具](031_start_onenote_desktop_tool.md) | 待办 | P1 | 规划 check-only `health_check` 之外的显式 `start_onenote_app`：可信本机 executable、single launch、可见 GUI 收敛和幂等返回；不包含长期 COM owner。 |

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
