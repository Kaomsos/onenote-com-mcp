# 034：用户测试前 MCP 工具面收敛与不必要入口隐藏

> ID：034
> 状态：待办
> 优先级：P0
> 类型：公开工具契约 / 产品能力模型 / 安全暴露面 / User Testing
> 更新日期：2026-08-15

## 背景

当前生产 registry 默认注册 61 个工具，按源码模块分为 System、Hierarchy、Pages、Mutations、Copying 和 Operations；另有 5 个 Advanced 工具在启用 `LOCAL_ONENOTE_ENABLE_RAW_XML` 时整体注册。现有工具已经覆盖 OneNote 的层级发现、内容读取、创建、编辑、Copy/Move、删除、导出、GUI 导航和生命周期操作，但“实现模块的分类”还没有完全收敛为“用户任务的清晰产品面”。

进入用户测试前仍有以下问题需要一次解决：

- `reorder_section_group` 已有真实证据证明后端不支持，当前却仍出现在默认 `tools/list` 中，只能在执行时拒绝；
- `find_meta`、`open_hierarchy`、`update_page_xml`、`merge_sections`、`set_filing_location` 风险和用途不同，却由一个 Raw XML 开关整体带入 Advanced profile；
- `resolve_identifier`、`get_*`、`query_*`、`expand_*`、`get_parent/get_path` 等入口之间存在相邻能力，Agent 和用户缺少一张明确的“什么时候用哪一个”决策表；
- Page 元数据、正文、对象、binary 与 raw XML 虽已拆分，但 raw 表示、typed 表示和完整性诊断的用户边界仍需明确；
- Export、GUI Navigation、Sync、Close 与 OneNote 内容操作混在同一个默认 profile，是否属于首轮用户测试的核心任务尚未逐项审查；
- policy 默认拒绝只能控制“能否执行”，不能回答“客户端是否有必要看见”。当前缺少独立的 exposure contract；
- README、Tool description、`health_check` capability、自动化注册断言和 manual-validation allowlist 尚未共同投影一份冻结的用户测试工具清单。

本 TODO 是用户测试的准入门，不把当前 61/66 的数量本身视为目标。最终工具数可以减少，也可以因必要的 typed 入口调整而变化；唯一标准是用户任务覆盖完整、入口选择清晰、暴露面最小且安全门限不退化。

## 目标

1. 以用户任务而非源码文件组织公开能力，建立稳定、可解释的工具目录和对象—操作矩阵。
2. 每个常见任务只有一个推荐入口或一条明确的分阶段调用链；确有多入口时，必须写清选择条件和返回差异。
3. 默认用户 profile 只暴露已支持、具名、typed、可文档化且有相应验证证据的产品工具。
4. 不支持、仅诊断、底层 COM/raw XML、可由 service 内部组合以及首轮用户测试不需要的入口，不出现在用户 profile。
5. 将“是否注册”“是否授权执行”“能力是否稳定”建模为三个独立维度，继续保持所有 mutation policy fail closed。
6. 在用户测试前冻结工具名称、参数、description、返回 envelope、权限要求和迁移策略，并让实现、测试、README、design 与验证场景一致。

## 三个必须分开的维度

| 维度 | 回答的问题 | 约束 |
| --- | --- | --- |
| Exposure | 客户端能否在 `tools/list` 中看到 | 仅由冻结的 profile/registration 决定；不能用执行时拒绝代替隐藏。 |
| Authorization | 当前进程是否允许执行 | 继续由 Writes、Delete、Permanent Delete、Reparent、Copy、Move、Raw XML 等独立 policy 控制，默认 fail closed。 |
| Stability | 是否属于稳定用户能力 | 必须有合同测试；mutation 还必须有具名 manual scenario 和用户确认的真实证据。实验或受限能力不得伪装为稳定能力。 |

隐藏工具不等于授权工具，注册工具也不等于允许 mutation。任何 profile 设计都不得合并、绕过或默认开启现有权限门限。

## 当前基线

实施开始时先从代码生成并保存工具清单，不手工维护第二份事实来源。当前审计基线为：

| 类别 | 默认数量 | 主要能力 |
| --- | ---: | --- |
| System | 3 | 健康检查、标识解析、特殊位置 |
| Hierarchy | 14 | Notebook discovery、精确读取、Query、Path、Expand |
| Pages | 6 | Page 元数据、XML、文本、内容对象、binary、Search |
| Mutations | 20 | Create、Rename、Reorder、Reparent、Page 内容写入、Delete |
| Copying | 11 | Copy plan/execute 与三类重建式 Move plan/execute |
| Operations | 7 | Hyperlink、Parent、Publish、GUI Navigate、Sync、Close |
| Advanced | 5 | Meta/Open/raw Page XML/Merge/Filing Location；默认不注册 |

基线合计为默认 61 个、可选 Advanced 5 个。实施期间若其他 TODO 已改变 registry，应先更新本节和审计输入，不能按旧数量机械删改。

## 用户任务与能力覆盖

最终 design 必须为以下用户任务给出推荐工具或明确声明“不支持”，并列出完成任务所需的最小调用链：

1. 检查 OneNote Desktop/COM 是否可用；
2. 发现已打开的 Notebook；
3. 从精确对象浏览层级树；
4. 按名称、标题、父级或修改时间查询 hierarchy metadata；
5. 按正文搜索 Page；
6. 读取单个对象元数据、祖先关系和显示路径；
7. 读取 Page 可见文本、typed 内容对象和受控 binary；
8. 创建 Notebook、SectionGroup、Section 和 Page；
9. 修改标题/名称、Page 正文和受支持内容对象；
10. 调整 Page/Section 的同父级顺序以及三类同 Notebook Reparent；
11. Copy 与跨 Notebook 重建式 Move，并在 mutation 前生成确定性 plan；
12. 非永久删除和独立授权的永久删除；
13. 生成 OneNote hyperlink；
14. 导出本地文件、驱动 GUI 导航、请求 Sync、关闭 Notebook；
15. 诊断或开发时访问底层 COM/Raw XML 能力。

对每项任务至少记录：目标用户、输入对象类型、首选工具、必要前置读取、是否有副作用、权限门、证据级别、失败后的下一步，以及为何不需要另一个相邻工具。第 15 类不得因为“实现存在”自动进入用户 profile。

## 逐工具审计矩阵

必须覆盖实施时 registry 中的每个工具，且每个工具恰好得到一个结论：

- `keep`：保留在默认用户 profile；
- `rename_or_merge`：由更清晰的 typed 名称或统一入口替换，旧名不长期保留 alias；
- `hide`：实现可供内部 service、人工验证或开发诊断使用，但不注册到用户 profile；
- `remove`：公开 adapter 和仅为其存在的死路径删除，底层通用 service 是否保留另行说明。

矩阵至少包含以下列：

```text
tool
current_profile
user_job
object_types
effect = read | content_mutation | hierarchy_mutation | file_write | gui | lifecycle
policy_gates
automated_contract
manual_scenario
real_evidence
overlap_or_replacement
decision
reason
target_profile
```

不得用“已有测试”单独证明工具值得公开；测试只证明当前实现行为，公开性还必须由真实用户任务和最小暴露原则证明。

## 已确定的收敛边界

以下不是待讨论候选，而是本 TODO 的硬门：

- `reorder_section_group` 不得出现在用户测试 profile。后端不支持的请求应从公开能力面移除，不能保留一个必然拒绝的产品工具；底层诊断代码是否保留不得形成生产注册旁路。
- `delete_hierarchy` 和 `update_hierarchy_xml` 继续不属于任何生产 profile，不得借重组恢复。
- `find_meta`、`open_hierarchy`、`update_page_xml`、`merge_sections`、`set_filing_location` 不进入首轮用户测试 profile。
- 不再由 `LOCAL_ONENOTE_ENABLE_RAW_XML` 一个开关成组暴露用途、风险与权限不同的 Advanced 工具。若某项未来确需公开，必须有独立的 typed 产品理由、exposure 决策、policy、自动化合同、具名真实场景和文档；否则只保留为非生产诊断能力或删除。
- 用户 profile 不保留兼容 alias。项目尚未进入用户测试，应该在此阶段完成破坏性命名收敛，避免把临时名称变成长期契约。
- service/bridge 内部 operation 不因被多个公开工具复用而自动成为 MCP tool；公开层只暴露完成用户任务所需的最小 typed adapter。

## 必须重点审查的相邻入口

以下只是要求作出有证据的最终决策，不预设必须删除：

### Discovery、Get、Query 与 Expand

- `resolve_identifier` 是否仍有必要接受名称/路径，或应由 `list_notebooks`、四类 `query_*` 和 exact-ID `get/expand` 完成发现与后续操作；
- `get_notebook/get_section_group/get_section/get_page` 是否形成完整且对称的 exact-ID Get 家族，还是部分入口仅与 Query/Expand 重复；
- `get_parent`、`get_path` 与 Expand 返回的关系信息分别服务什么任务；
- typed `expand_*` 与通用 `expand_hierarchy` 是否都对用户有必要。TODO 033 的已完成决策和真实证据必须被尊重；若要改变，需给出新的用户任务证据而不是只为减少数量。

### Page 内容读取

- `get_page_text`、`get_page_objects`、`get_binary_content` 继续保持按需、typed、预算受限的最小读取；
- 评估 `get_page_xml` 是稳定用户读取能力、受限诊断能力还是应隐藏的底层表示。任何结论都不得影响 service 内部受控 Page XML 使用，也不得通过隐藏 typed 读取迫使用户改用 raw XML；
- 明确 `get_page` 与 hierarchy `query_page` 的元数据边界，避免同名 Page 入口让 Agent误读正文或重复进行宽扫描。

### Mutation、Copy 与 Move

- 对 Create、Rename、Reorder、Reparent、Page content mutation、Delete 建立对象—操作对称性；后端确实不对称时明确写成能力限制，不增加虚假对称工具；
- 审查 `plan_copy` 的通用入口与三个 typed `plan_move_*` 是否有清晰的一致模型。Plan 是 mutation 安全协议的一部分时不得仅为减少工具数而移除；
- 实验性但已注册的 mutation 工具是否进入首轮用户测试，必须同时依据产品范围、policy、合同和当前真实证据，不得只看默认是否可执行；
- `close_notebook`、`sync_notebook` 等 lifecycle 操作不能因不修改 Page 内容就被误分类为普通只读工具。

### File、GUI 与 Lifecycle

- 分别审查 `publish_object`、`navigate_to`、`navigate_to_url`、`sync_notebook`、`close_notebook` 是否属于首轮核心用户任务；
- File write、GUI focus/window 和 Notebook lifecycle 必须在 description、effect 分类和权限模型中显式可见，不能笼统归入 Operations；
- 若保留，必须说明目标路径/URL/窗口行为、幂等性、确认字段和失败恢复；若隐藏，README 示例和 health capability 同步移除，不保留不可发现的半公开契约。

## 目标工具目录与 Profile

最终结构至少区分：

1. **User profile**：默认且作为用户测试唯一入口；只包含稳定、受支持的 task-level typed 工具。
2. **Experimental capability**：只有确有用户测试目标、独立 exposure 决策和独立 policy 的实验能力才可选择性注册；不得使用笼统的 `advanced` 总开关。
3. **Internal/diagnostic operations**：不进入生产 MCP `tools/list`；由 service、bridge、纯测试或受控 manual-validation 基础设施内部使用。

具体是否引入新的 profile 环境变量必须在实现前冻结。优先保持一个简单、确定的默认用户工具面；不得为了分类而制造多组难以预测、组合后未经测试的 registry。`health_check` 只能返回 content-free 的当前 profile、工具数量和稳定 capability 摘要，不返回隐藏工具名、secret、原始配置或 OneNote 内容。

## 实施阶段

### A. 冻结产品矩阵

- 从 registry 自动生成当前清单、签名、description、effect 和 policy 映射；
- 完成 66 项或当时实际数量的逐工具审计矩阵；
- 完成用户任务最小调用链和对象—操作矩阵；
- 决定最终名称、分类、profile、隐藏/移除列表和迁移方式；
- 在动代码前审查与 TODO 029、031、033 的依赖，避免工具面重组掩盖尚未完成的可靠性或新增能力工作。

### B. 收敛注册与实现

- 以一个显式 registry 作为生产工具清单的唯一来源，消除隐藏的替代注册路径；
- 从用户 profile 移除不支持、底层或无首轮用户任务的工具；
- 删除旧 alias 和只为旧公开入口存在的 adapter，通用业务逻辑保留在 service 层；
- 为保留工具统一名称、参数中的 exact ID 术语、description、effect、policy 和 response envelope；
- Advanced 能力按最终决策移至非生产诊断路径、独立 exposure gate 或删除，不再被 Raw XML 开关成组注册；
- mutation、Delete、Permanent Delete、Reparent、Copy、Move、Raw XML 等安全门限保持相互独立且默认关闭。

### C. 同步契约与用户材料

- 更新 `docs/design/tool_contracts.md`，使其成为最终工具面、profile、参数、返回和 policy 的 canonical source；
- 更新对象模型/架构文档、根 README 工具目录、安装配置、示例 prompt、环境变量和限制说明；
- README 按用户任务呈现工具，不按 Python 模块机械罗列；
- `health_check` capability、manual-validation allowlist、smoke 配置和所有工具名引用同步；
- 搜索仓库并移除旧工具名、旧数量、旧 profile 叙述及误导性的兼容说明；历史 TODO 中作为证据保留的名称可保留，但必须保持历史语境，不得伪装成当前契约。

### D. 用户测试准入验证

- 在干净进程调用 `tools/list`，保存默认用户 profile 的精确名称、schema 和数量快照；
- 证明所有 `remove/hide` 工具均不出现在用户 profile，也不存在环境变量或导入顺序旁路；
- 对每个保留工具至少有注册/schema/description/response 合同；
- 对每个 mutation 工具有正确的自动化 policy 合同和 `tests/manual_validation/` 具名 scenario；
- 工具移除或重命名不得由兼容 alias、generic raw XML 或名称定位 mutation 绕回；
- 先运行相关纯测试，再运行完整 `.venv\Scripts\python.exe -m pytest -q`；
- 真实 OneNote scenario 只能由用户本人显式运行。Agent 只可运行纯测试、mock、保存证据检查和 `--dry-run`；
- 用户测试开始前，由用户审阅最终工具目录、典型最小调用链、默认权限和已知限制，并明确批准测试范围。

## 自动化合同

至少新增或更新以下覆盖：

- 默认 registry 精确集合、无重复名称、冻结数量和确定顺序；
- forbidden set 明确包含不支持工具、内部 bridge operation 和未授权的 Advanced 工具；
- 非默认 exposure 组合若存在，逐组合冻结精确集合，不能只断言数量；
- `reorder_section_group`、`delete_hierarchy`、`update_hierarchy_xml` 不可通过任何生产配置枚举；
- Raw XML 开关不能隐式注册 `find_meta/open_hierarchy/merge_sections/set_filing_location`；
- 每个公开 mutation tool 的 policy 元数据、真实 scenario 映射和 fail-closed 默认值完整；
- 工具 description 明确 effect、目标对象、exact ID、范围/预算和必要权限，不泄露实现细节或内容；
- 用户任务矩阵中的每个受支持任务至少映射到一个公开工具，且不存在未解释的多主入口；
- README/design/health/manual-validation 的工具集合与 registry 投影一致；
- 移除工具的 service 内部复用路径仍有聚焦测试，不能为减少暴露面破坏 Copy/Move/Reparent 等既有能力。

## 非目标与安全边界

- 不在本 TODO 中新增 OneNote 产品能力，也不实现 TODO 031 的 `start_onenote_app`；若其先完成，按相同矩阵重新审查。
- 不为追求较小数量把多个高风险操作合并成接受任意 action/raw payload 的 generic tool。
- 不用名称匹配、宽范围扫描或 raw XML 替代被移除的 typed tool。
- 不削弱 mutation policy、confirmation、预算、收敛、对账、Copy plan digest 或 partial failure 合同。
- 不引入 Microsoft Graph、Azure、OAuth、遥测、远程内容处理或直接 `.one` 文件编辑。
- 不把真实 OneNote mutation 接入 pytest、CI、hook、package/install、import、timer、watcher 或后台任务。
- 不执行真实 `run.py <scenario>` 或 `run.py all`；真实验收命令始终交给用户。

## 用户测试准入门

以下全部满足前，不进入对外或非维护者用户测试：

- 最终用户 profile 和逐工具审计矩阵已由用户审阅冻结；
- 每个公开工具都能映射到明确用户任务、effect、对象类型、policy 和证据；
- 不支持、底层、诊断和本轮不需要的入口无法从默认 `tools/list` 发现；
- 默认配置不暴露 Advanced/raw XML，也不允许任何 mutation；
- README 中的工具目录、示例和实际 `tools/list` 一致；
- 所有纯测试通过，且适用 mutation 工具已有当前实现对应的用户确认真实隔离证据；
- 测试者拿到一份简洁的支持范围、权限开启方式、已知限制和数据安全说明。

## 完成定义

- [ ] 当前全部工具完成逐项审计，并记录 keep/rename_or_merge/hide/remove 结论及理由；
- [ ] 用户任务最小调用链、对象—操作矩阵和最终 profile 设计完成并获得用户确认；
- [ ] 默认用户 registry 已按冻结清单实现，所有不必要入口和注册旁路被移除；
- [ ] `reorder_section_group` 及所有底层 raw hierarchy operation 不存在于任何生产 profile；
- [ ] Advanced 工具不再由单一 Raw XML 开关整体暴露，首轮用户 profile 不包含任何 Advanced 工具；
- [ ] 保留工具的名称、schema、description、effect、policy 和 response envelope 已统一；
- [ ] 自动化 registry/schema/policy/文档投影合同与完整 pytest 通过；
- [ ] 所有受影响 mutation scenario 仍具名存在，用户确认必要的当前版本真实隔离回归通过；
- [ ] README、design、health、manual-validation 和用户测试说明与最终工具面一致；
- [ ] 用户明确批准最终工具目录和用户测试范围后，本 TODO 才可标记为“已完成”。

## 完成证据记录

| 证据 | 结果/位置 |
| --- | --- |
| 实施前 registry 自动清单 | 待填写 |
| 逐工具 keep/merge/hide/remove 审计矩阵 | 待填写 |
| 用户任务与对象—操作矩阵 | 待填写 |
| 最终 `tools/list` 名称/schema 快照 | 待填写 |
| Forbidden/旁路/权限自动化合同 | 待填写 |
| 聚焦测试与完整 pytest | 待填写 |
| 受影响 mutation 的用户真实证据 | 待填写 |
| README/design/health/manual-validation 同步审查 | 待填写 |
| 用户测试范围最终批准 | 待填写 |

## 关联

- [公开 Tool 契约](../design/tool_contracts.md)：最终工具面、profile、参数、返回和 policy 的 canonical source。
- [当前架构](../design/architecture.md)：composition root、tools/services/bridge 边界与 local-only 约束。
- [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md)：隐藏 raw hierarchy XML 与 typed Reparent 的既有决策。
- [TODO 023](023_public_repository_release_readiness.md)：公开发布的能力矩阵必须基于本 TODO 冻结后的用户工具面。
- [TODO 029](029_mcp_mutation_readiness_and_reconciliation_hardening.md)：实验 mutation 的稳定性与 replay policy 不能由工具面重组掩盖。
- [TODO 031](031_start_onenote_desktop_tool.md)：未来新增 GUI 启动能力必须通过同一 exposure 审计。
- [TODO 033](033_notebook_structure_list_and_expand_tools.md)：最近完成的 List/Expand 语义和真实证据，若调整必须给出新的用户任务依据。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：真实 mutation 验收的人为授权边界。
