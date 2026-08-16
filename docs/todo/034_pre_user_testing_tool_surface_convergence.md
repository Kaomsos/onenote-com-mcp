# 034：用户测试前 MCP 工具发布面收敛

> ID：034
> 状态：已完成
> 优先级：P0
> 类型：公开工具契约 / 产品能力模型 / 安全暴露面 / User Testing
> 更新日期：2026-08-16

## 当前状态

当前 production Registry 已按冻结方案收敛为 **52 个 User Tool**，并由机器可验证的 17 类目录和 canonical Operation Registry 双向审计。13 项重命名已直接切换且没有 alias；五项 Internal & Incubating capability 已退出 MCP 注册；七个公开授权、统一 envelope、参数收敛和 `launch_onenote_gui` 均已落地。当前可执行合同以 [公开 Tool 契约](../design/tool_contracts.md) 和源码 Registry 为准。

数量收敛关系是：

```text
原 56 - 5 个转为 Internal & Incubating + 1 个 launch_onenote_gui = 52
```

用户已于 2026-08-15 批准 v1.0 方案并授权进入实现。纯自动化、文档同步、当前 52 项合同的 MCP convergence 与 `launch_onenote_gui` 真实验收均已完成；2026-08-16 用户审阅最终实现和证据后，明确批准进入用户测试、允许提交并关闭本 TODO。本项完成。

## 发布原则

工具面用用户任务组织，不按 Python 模块组织。以下三个维度必须独立：

| 维度 | 回答的问题 | 规则 |
| --- | --- | --- |
| Exposure | 客户端能否在 `tools/list` 看见 | 由 User profile 精确集合决定；执行时拒绝不能代替隐藏。 |
| Authorization | 当前进程是否允许执行 | 由最小、正交、默认关闭的 policy gate 决定。 |
| Stability | 是否可作为稳定产品能力 | 公开工具必须有合同覆盖；mutation 还必须有具名 manual scenario 和用户确认的真实证据。 |

隐藏不等于授权，注册也不等于允许执行。不得为减少工具或开关数量而引入接受任意 action、raw XML 或路径的通用危险入口。

## v1.0 User profile：52 个工具

### 1. Session（2）

- `health_check`
- `launch_onenote_gui`（已实现；详见 [TODO 031](031_start_onenote_desktop_tool.md)）

`health_check` 始终 check-only。`launch_onenote_gui` 仅在未启动时发出一次受信任的启动请求并有界观察 readiness；已运行时不重复启动。

### 2. Hierarchy Browse（7）

- `list_notebooks`
- `get_hierarchy_path`
- `expand_notebook`
- `expand_section_group`
- `expand_section`
- `expand_page`
- `expand_hierarchy`

`list_notebooks` 从 Session/System 语义移动到 Hierarchy Browse。所有 Expand 与 Notebook discovery 共同构成浏览层级，不与 metadata get 混类。

### 3. Metadata Get（4）

- `get_notebook_metadata`
- `get_section_group_metadata`
- `get_section_metadata`
- `get_page_metadata`

这是 exact-ID 的单对象元数据读取族，不执行名称查询、不读取 Page 正文。

### 4. Query & Search（5）

- `query_notebook`
- `query_section_group`
- `query_section`
- `query_page`
- `search_pages`

Query 与 Page 正文搜索归为同一发现类；Metadata Get 继续独立。Query 返回候选，后续 mutation 仍必须使用精确 ID。

### 5. Page Content Read（3）

- `get_page_text`
- `get_page_content_objects`
- `get_page_content_object_binary`

Binary 是预算受限的 typed read，由 bridge/service 的硬大小预算保护，不设独立授权开关。

### 6. Hyperlink（1）

- `get_hyperlink`

`link_type` 收敛为 `desktop | web`。

### 7. Create（4）

- `create_notebook`
- `create_section_group`
- `create_section`
- `create_page`

### 8. Rename（3）

- `rename_page`
- `rename_section_group`
- `rename_section`

### 9. Reorder（2）

- `reorder_page`
- `reorder_section`

OneNote COM 不支持稳定的 SectionGroup reorder，因此不提供虚假对称入口。

### 10. Organize（3）

- `reparent_page`
- `reparent_section`
- `reparent_section_group`

公开工具名继续使用准确的 `reparent_*`，产品分类和权限从 Reparent 更名为 **Organize**。

### 11. Page Content Mutation（4）

- `append_page_content`
- `add_page_image_from_file`
- `replace_page_body`
- `delete_page_content_object`

### 12. Recoverable Delete（3）

- `delete_page`
- `delete_section`
- `delete_section_group`

这三个工具始终执行可恢复删除，不再接受 `permanently` 布尔值。永久删除使用独立、可选、显式命名的工具，见后文。

### 13. Copy（4）

- `copy_page`
- `copy_section`
- `copy_section_group`
- `copy_notebook`

### 14. Reconstructive Move（3）

- `move_page`
- `move_section`
- `move_section_group`

Move 不再有独立权限开关。它是 Copy 与源对象 Delete 的组合语义，执行时需要 Writes、Copy、Deletes 三者同时授权。安全 planning 保留在单次 operation 内部，不公开 plan/preview/token 工具。

### 15. Export（1）

- `export_object_to_pdf`

只导出 PDF，不接受冗余 `format` 或危险的 `overwrite` 选项。

### 16. UI Navigation（1）

- `navigate_to`

### 17. Notebook Lifecycle（2）

- `request_notebook_sync`
- `close_notebook`

Sync 表达请求语义，不承诺远端同步已经完成。

## 冻结的重命名

发布面已直接完成破坏性命名收敛，不保留旧名 alias：

| 旧名称 | v1.0 名称 | 理由 |
| --- | --- | --- |
| `get_notebook` | `get_notebook_metadata` | 明确只读取元数据。 |
| `get_section_group` | `get_section_group_metadata` | 同上。 |
| `get_section` | `get_section_metadata` | 同上。 |
| `get_page` | `get_page_metadata` | 与正文/对象读取分离。 |
| `get_path` | `get_hierarchy_path` | 明确对象域。 |
| `get_page_objects` | `get_page_content_objects` | 明确返回 Page 内容对象清单。 |
| `get_binary_content` | `get_page_content_object_binary` | 明确二进制属于 Page 内容对象。 |
| `update_page_title` | `rename_page` | 与 Section/SectionGroup 命名一致。 |
| `append_to_page` | `append_page_content` | 明确内容 mutation。 |
| `add_image_to_page` | `add_page_image_from_file` | 明确本地文件来源。 |
| `delete_page_content` | `delete_page_content_object` | 避免误解为删除整个 Page。 |
| `publish_object` | `export_object_to_pdf` | 精确表达本地 PDF 导出。 |
| `sync_notebook` | `request_notebook_sync` | 避免承诺同步完成。 |

`launch_onenote_gui` 是唯一名称，不提供 `start_onenote_app` alias。

## Internal & Incubating 集中目录

以下五个当前公开入口不进入 User profile，统一进入**非 MCP 注册**的 Internal & Incubating catalog：

| 名称 | 状态 | 主要原因 |
| --- | --- | --- |
| `resolve_identifier` | incubating | 通用名称/路径解析容易形成不明确选择；公开任务由 List、Query、Search 与 exact-ID Get/Expand 完成。 |
| `get_page_xml` | incubating | Raw Page XML 是底层表示，不应成为普通用户读取主入口。 |
| `navigate_to_url` | incubating | 通用 URL 导航边界尚未成熟，用户面只保留 typed `navigate_to`。 |
| `get_special_locations` | internal_helper | 为内部能力组合服务，不是独立用户任务。 |
| `get_parent` | internal_helper | 父关系由 metadata、path 或 expand 结果表达，无需独立入口。 |

Catalog 不是隐藏 profile，也不得有批量 exposure 开关。每项至少维护：`name`、`state`、`reason`、`internal_callers`、`promotion_requirements`。自动化必须证明这些入口与 User profile 不相交，且不能通过环境变量或导入顺序重新注册。

incubating 项未来如要发布，必须逐项给出用户任务、typed schema、独立 exposure 决策、权限、预算、合同和必要的真实验证；不能因为底层实现仍存在而自动公开。

## 明确禁止的生产入口

以下能力不得出现在任何生产 MCP profile：

- `reorder_section_group`
- `delete_hierarchy`
- `update_hierarchy_xml`
- `find_meta`
- `open_hierarchy`
- `update_page_xml`
- `merge_sections`
- `set_filing_location`
- `plan_copy`
- `plan_move_page`
- `plan_move_section`
- `plan_move_section_group`
- 所有 `preview_*`

不得用 Raw XML 总开关、兼容 alias、generic action 或 service operation 旁路恢复这些入口。

## 默认授权模型：7 个开关

所有开关默认 false：

| 授权 | 环境变量 | 覆盖范围 |
| --- | --- | --- |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | Create、Rename、Page/Section Reorder、Append，以及其他组合操作的写入阶段。 |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | 内容对象删除、可恢复删除、Replace 的删除阶段、Move 的源删除阶段。 |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | 三个 `reparent_*`；同时还需要 Writes。 |
| Copy | `LOCAL_ONENOTE_ENABLE_COPY` | 四个 `copy_*`；同时还需要 Writes。 |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | 从本地文件加图、导出 PDF；加图同时需要 Writes。 |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | `launch_onenote_gui`、`navigate_to`。 |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | `request_notebook_sync`、`close_notebook`。 |

组合规则：

- `replace_page_body`：Writes + Deletes；
- `delete_page_content_object` 与三类可恢复删除：Deletes；
- `reparent_*`：Writes + Organize；
- `copy_*`：Writes + Copy；
- `move_*`：Writes + Copy + Deletes；
- `add_page_image_from_file`：Writes + Local File IO；
- `export_object_to_pdf`：Local File IO；
- `launch_onenote_gui`、`navigate_to`：UI Control；
- `request_notebook_sync`、`close_notebook`：Notebook Lifecycle。

Writes、Deletes、Organize、Copy 必须继续相互独立，不能因为调用了组合 operation 而隐式放宽。Move 不单独出现开关，原有 Reparent 权限更名为 Organize。Binary read 通过硬预算控制，不增加权限开关。

## 可选永久删除扩展

永久删除不属于默认 52。未来若单独批准，可追加三个显式工具：

- `delete_page_permanently`
- `delete_section_permanently`
- `delete_section_group_permanently`

届时目标工具总数为 55。每个工具都必须同时满足：显式 exposure、Deletes、独立 PermanentDeletes 授权、自动化合同、具名 manual scenario 和用户确认的真实隔离证据。普通 `delete_*` 不接受 `permanently` 布尔值，也不得退化到永久删除。

## 关键参数收敛

- Page 范围统一使用 `page_scope="page_only" | "indentation_subtree"`，替代含义模糊的 `include_descendants`；
- optional value 使用 `null` 表示缺省，不使用空字符串 sentinel；
- typed Expand 使用与对象类型对应的 ID 参数名；
- `create_notebook(name, base_folder=null)`；
- `create_section_group`、`create_section` 使用 `name`；
- `create_page` 移除 `new_page_style`；
- `replace_page_body` 不接受 title；
- `add_page_image_from_file` 从文件内容推断并验证格式；
- Page content object ID 的来源、稳定性和适用工具必须清晰；
- `get_hyperlink.link_type` 为 `desktop | web`；
- `export_object_to_pdf` 不接受 `format` 与 `overwrite`；
- Copy/Move 是单次调用，内部生成 live plan，不公开 plan/preview/token。

## 统一响应 envelope

成功：

```json
{
  "ok": true,
  "result": {},
  "warnings": [],
  "execution": {}
}
```

失败：

```json
{
  "ok": false,
  "error": {
    "code": "stable_typed_code",
    "message": "content-free summary",
    "details": {}
  },
  "execution": {}
}
```

业务数据只放在 `result`；失败不得同时伪装成功结果。`execution` 只包含可安全公开的 operation/runtime 元数据，不泄露 secret、原始配置、用户路径或 OneNote 内容。

## 用户任务入口规则

| 用户意图 | 首选入口 | 后续规则 |
| --- | --- | --- |
| 检查/启动桌面端 | `health_check` / `launch_onenote_gui` | 检查不启动；启动必须显式授权。 |
| 浏览层级 | `list_notebooks` + `expand_*` | 从 Notebook discovery 开始，按 exact ID 有界展开。 |
| 读取已知对象元数据 | `get_*_metadata` | 不用 Query 替代 exact-ID Get。 |
| 按属性发现对象 | `query_*` | 返回候选后固定 exact ID。 |
| 按正文发现 Page | `search_pages` | 与 Query 同属发现类，mutation 前固定 Page ID。 |
| 读取 Page 内容 | text / objects / binary 三个 typed 入口 | 不把 raw XML 作为默认降级路线。 |
| 组织层级 | `reparent_*` | 只表达 parent change；同父顺序用 Reorder。 |
| Copy/Move | 单次 `copy_*` / `move_*` | Agent 不持有 planning token。 |
| 删除 | 默认可恢复 `delete_*` | 永久删除必须走独立可选工具。 |
| 文件、GUI、Lifecycle | 各自 typed 工具 | effect 与授权在 description 中显式可见。 |

## 实施阶段

### A. 冻结产品矩阵（v1.0 方案已获用户最终批准）

- 保存当前 Registry 的精确名称、schema、description、effect 与 policy 基线；
- 把 52 个 User 工具、5 个 Internal & Incubating 入口和 forbidden set 形成机器可验证的唯一投影；
- 冻结 13 项重命名、参数收敛、统一 envelope 与 7 个授权开关；
- 保留当前合同与目标合同的清晰边界。

### B. 收敛注册与实现

- 以一个显式 Registry 作为生产工具清单的唯一来源；
- 实现 `launch_onenote_gui`，按 TODO 031 完成单次启动与有界 readiness；
- 从 User profile 隐藏 5 个通用/不成熟入口，并建立非注册 catalog；
- 完成重命名，不保留旧 alias；
- 收敛 policy gates、参数和 envelope，同时保持 fail closed；
- Copy/Move planning 只留在单次 canonical operation 内部；
- 删除所有注册旁路。

### C. 同步产品合同

- 实现落地时更新 `docs/design/tool_contracts.md` 为新的 canonical current contract；
- 同步根 README、对象模型、架构、配置、示例 prompt、`health_check` capability 和 manual-validation allowlist；
- README 按用户任务呈现工具，不按源码模块机械罗列；
- 搜索并处理旧工具名、旧数量、旧 profile 与旧权限叙述；历史证据可保留旧名，但必须保持历史语境。

### D. 用户测试准入验证

- 干净进程保存 `tools/list` 的精确名称、schema、description、数量和顺序快照；
- 证明 hidden/internal/forbidden 项没有环境变量或导入顺序旁路；
- 每个公开工具具有注册、schema、description、envelope 和 policy 合同；
- 每个 mutation 具有自动化 policy 合同和具名 manual scenario；
- 先运行相关纯测试，再运行完整 `.venv\Scripts\python.exe -m pytest -q`；
- 真实 OneNote scenario 只能由用户本人显式运行；
- 用户审阅最终工具目录、最小调用链、默认权限、已知限制与证据后，明确批准用户测试范围。

## 自动化合同

- 默认 Registry 精确等于冻结的 52 项集合，无重复名称且顺序确定；
- Internal & Incubating catalog 与 User profile 不相交，且没有批量 exposure 开关；
- forbidden set 不可由任何生产配置枚举；
- 13 个旧名称不再注册，也没有 alias；
- 7 个授权默认关闭，组合工具必须同时通过全部所需 gate；
- 52 个公开工具具有冻结的逐工具 authorization 映射；全自动 mock 矩阵逐项验证 52 个最小权限允许组合与 46 个逐一缺失必需 gate 的拒绝组合，拒绝必须停在 authorization、`backend_calls=0`、不进入 Handler 且 coordination generation 不变；
- `launch_onenote_gui` 的 UI Control 拒绝发生在任何启动副作用之前；
- `delete_*` 始终可恢复且 schema 不含 `permanently`；
- 未显式 exposure 的永久删除工具不可发现；
- description 明确 effect、目标类型、exact ID、预算和权限，不泄露实现或内容；
- README/design/health/manual-validation 与 Registry 投影一致；
- 移除公开入口不破坏 service 内部受控复用。

## 非目标与安全边界

- 不为工具面收敛引入通用 action、raw payload 或兼容注册旁路；
- 不为较小数量合并不同风险的任意 action/raw payload；
- 不用名称匹配、宽扫描或 raw XML 替代 typed tool；
- 不削弱 mutation confirmation、预算、收敛、对账或 partial failure 合同；
- 不引入 Graph、Azure、OAuth、遥测、远程内容处理或直接编辑 `.one` 文件；
- 不把真实 OneNote mutation 接入 pytest、CI、hook、import、timer、watcher 或后台任务；
- Agent 不执行真实 `run.py <scenario>` 或 `run.py all`。

## 用户测试准入门

以下全部满足前，不进入对外或非维护者用户测试：

- [x] 用户确认 v1.0 的分类、52 个默认工具、13 项重命名、5 个 Internal & Incubating 入口、7 个默认授权与可选永久删除方向；
- [x] 用户授权把当前方案写入产品规划文档；
- [x] 默认 Registry 按冻结清单实现，隐藏/禁止入口无旁路；
- [x] 名称、schema、description、effect、policy 和 envelope 全部统一；
- [x] 自动化 Registry/schema/policy/文档投影合同与完整 pytest 通过；
- [x] 适用 mutation 具有与当前实现一致的具名 scenario 和用户真实隔离证据；
- [x] README、design、health、manual-validation 与实际 `tools/list` 一致；
- [x] 用户最终审阅实现快照和证据，并明确批准开始用户测试。

## 完成证据记录

| 证据 | 结果/位置 |
| --- | --- |
| 当前 Registry | `tool_surface.USER_TOOL_NAMES` 与 production Registry 精确为 52，顺序和分类由启动审计与合同测试证明。 |
| v1.0 User profile | 本文“52 个工具”章节。 |
| 重命名与 Internal catalog | 本文对应章节。 |
| 授权矩阵 | 本文“7 个开关”章节。 |
| 最终 `tools/list` 名称/schema 快照 | `tests/test_server.py` 精确断言关键 schema；2026-08-15 运行 `scripts\smoke_mcp.py --tools-only`，真实 stdio `tools/list` 得到精确 52 项冻结顺序、无重复/缺失/意外暴露、description/input/output schema 全部完整，且 `onenote_accessed=false`；完整 transport 投影 SHA-256 为 `a35e1d9bb7a8153e4081a04de93a07954dfd355eb033521431d2ad4ebae9b9fe`。 |
| Forbidden/旁路/权限合同 | `tests/test_operation_runtime.py`、`tests/test_policy.py` 与 Registry 启动审计通过。逐工具授权矩阵独立冻结全部 52 项 authorization 映射，并以真实 authorizer + mock Handler 自动执行 98 个运行时组合：52 个精确最小权限允许 case，以及 46 个逐项缺失必需 gate 的拒绝 case；全部拒绝均要求 authorization stage、`backend_calls=0`、Handler 未执行且 coordination generation 不变。 |
| 聚焦测试与完整 pytest | 2026-08-16：`.venv\Scripts\python.exe -m compileall -q src scripts tests` 通过；权限相关聚焦组 166 passed，其中逐工具 Runtime 矩阵文件 119 passed；`.venv\Scripts\python.exe -m pytest -q`，1135 passed；其中 manual-validation 自动化 582 passed。 |
| Manual Validation 编排 | 2026-08-15：`run.py all --dry-run --verbosity normal` 18/18 passed；另行通过不进入 `all` 的 `onenote-convergence --dry-run --json` 与 `hierarchy-navigation --dry-run --json`；均未连接或修改真实 OneNote。 |
| GUI Launch 独立验收入口 | `tests/manual_validation/launch_onenote_gui_check.py` 不属于 Scenario Registry 或 `all`；顺序冻结 UI Control 关闭/仅开启 UI Control 的两个 MCP policy，验证 check-only health、authorization 零 backend call、单次启动、重复调用幂等、ready health、typed hierarchy COM 和 run-bound 人工单窗口 verdict。支持 `--verbosity quiet|normal|verbose`；MCP calls、bridge audit 与 server stderr 只流向前台终端，不写入对应 runtime 日志文件，逐阶段结构化证据继续进入 owned run。OneNote/Office 可能在隔离 TEMP 下写入自身 diagnostics/cache，这不属于 MCP runtime 日志。用户于 2026-08-16 执行 `run-2026-08-16-00-01-03` 并 `ACCEPT`：未授权为 `policy_disabled`/`backend_calls=0`；首调为 `started`/一次 launch；复调为 `already_running`/零 launch；后续 health ready、typed `list_notebooks` 读取通过并观察到 6 个 Notebook；人工确认只有一个可见 GUI，最终状态为 `passed` 且 OneNote 保持运行。 |
| 当前 Hierarchy Browse 用户真实证据 | 用户于 2026-08-15 执行 `run-2026-08-15-23-19-55`；`hierarchy-navigation` 在当前 52 项 Registry 下通过，`list_notebooks`、四种 typed Expand、跨 Notebook 根集合、Page 缩进树、`max_depth` 边界与 metadata-only 审计均通过；两个 fresh disposable Notebook 均精确关闭并保留，`run-result.json` 最终状态为 `passed`。 |
| mutation 用户真实证据 | 历史 typed operation 证据保留。当前合同的前两次执行如实保留为 runner 修复证据：`run-2026-08-15-23-19-39` 因解包丢失顶层 `execution` 而 fail closed；修复后 `run-2026-08-15-23-22-57` 已走完全套 mutation、恢复原快照并成功关闭 Notebook，又因 handoff 仍要求旧业务字段 `complete` 而未能封存 lease。两项 runner 缺陷修复并通过 575 项 manual-validation 自动化测试后，用户执行 `run-2026-08-15-23-27-37`：当前 52 项 Registry、统一 envelope 和 7 开关投影下，Sync、Notebook Create/Close、PDF Export、Navigate，以及 Page Create/Rename/Replace/Append/内容删除/Reorder/可恢复删除全部通过；每项 Runtime backend/kind、单次 mutation attempt、无 replay、双稳定 convergence、原快照恢复和 production close handoff 均闭合。最终 `run-result.json` 为 `passed`，source lease 为 `closed_preserved`，本地工作文件和证据保留。 |
| 产品文档同步审查 | README、design、overview、TODO、manual-validation 与 smoke client 已切换到实现态。 |
| 实现后用户测试准入批准 | 2026-08-16 用户明确批准进入用户测试，允许提交并关闭 TODO 034。 |
| v1.0 方案最终批准 | 2026-08-15 用户确认方案与文档修改通过并授权进入实现；2026-08-16 在实现、自动化与真实证据闭合后完成最终准入批准。 |

## 关联

- [公开 Tool 契约](../design/tool_contracts.md)：当前 52 项实现面的 canonical source。
- [OneNote 对象模型概念评估](../overview/onenote_object_model_assessment.md)：面向对象、关系、标识和能力形状的概念模型。
- [当前架构](../design/architecture.md)：composition root、tools/services/bridge 与 local-only 边界。
- [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md)：隐藏 raw hierarchy XML 与 typed Reparent 的既有决策。
- [TODO 023](023_public_repository_release_readiness.md)：公开发布必须基于本 TODO 冻结后的工具面。
- [TODO 029](029_mcp_mutation_readiness_and_reconciliation_hardening.md)：mutation 稳定性与 replay policy。
- [TODO 031](031_start_onenote_desktop_tool.md)：`launch_onenote_gui` 的当前合同与真实验收边界。
- [TODO 033](033_notebook_structure_list_and_expand_tools.md)：List/Expand 语义和真实证据。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：真实 mutation 验收的人为授权边界。
