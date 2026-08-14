# 022：四层 Typed Metadata Query、原生 Scope 与 List 工具退役

> ID：022
> 状态：阻塞
> 优先级：P1
> 类型：公开工具契约 / Query 能力 / Tool 收敛 / Agent 可发现性
> 更新日期：2026-08-14

## 当前进度（2026-08-14）

当前阻塞原因已经收敛为阶段 B 的独立 List 退役批准。用户确认 `run-2026-08-14-16-49-13` 的 `query-metadata-scopes --use-cache` cold build 完整通过：root、三类 start node、关闭 Notebook 排除、Page 缩进父级、分页和动态打开 Notebook 基线均通过，默认 lifecycle 已关闭且 immutable template 未打开。Query 场景现以 `included_in_all=true` 纳入批处理；在用户另行明确批准删除五个 List 工具前，阶段 B 仍不得开始，因此本 TODO 保持“阻塞”。

阶段 A 已落地：默认 profile 注册四个 fixed-type Query，删除公开 `query_hierarchy` 且未新增 `global_query`；service 使用 root/最浅 scope 或 root `hsSections` catalog 加一个精确 native start call，实施 open-only、严格关系/时间、回收站和 `offset/page_size` live pagination。health、README 与 canonical tool contract 已同步。五个 List 按批准门继续保留。

自动化聚焦合同通过；完成审计后又补齐原生起点矩阵、空 root、XML fail-closed、schema ID/mode description、完整模拟 scenario runtime 与精确 pre-closed lifecycle receipt 合同，当时完整纯测试基线为 `974 passed`。`query-metadata-scopes` 最初以 fresh-only、`included_in_all=false` 的双 Notebook scenario 加入；两个 role 现在各自具备嵌套 Group、Notebook/Group 直属 Section、根页和缩进页，并持久化独立 typed tree、expected 投影、每个请求/响应与 bridge operation，覆盖末页和越界页。其 cache 策略的后续变更见下方 2026-08-14 记录。静态权限仅包含 fixture Writes 和读工具，Delete/Copy/Move/Permanent Delete/Raw XML 均关闭。

2026-08-14 又将该旧场景对齐到当前 scenario-owned fixture bundle validation：验证上下文显式携带 role，逐 role fail-closed 校验完整 key set、对象类型、两条 container chain、Page Section/root level/indentation parent 和每 Page 单次内容 snapshot；bundle 层另证明 source/query-b 共享同一个非空 token。Recipe 现支持 cache，并沿用 materialized fixture 的 batch-open、typed rebind、两次层级稳定和单次内容 snapshot；Query 不执行 Search 专用 close/reopen。固定 title token 只有在真实产生预期 ID 严格超集时才写 content-free collision warning 并 fail closed，不再预防性拒绝 cache。

同日首次真实运行在 `query-b` 的 Outer/Inner 已创建后，于 Deep Section 返回 `onenote_file_unavailable`。失败目标的绝对路径为 283 字符，确认旧 Recipe 在三层物理名称中重复完整 UUID、且未预算最深 `.one` 路径。现已改为由完整 token 派生 16 位紧凑物理 token，并在每个 role 的首个 fixture mutation 前对最深 Section 路径执行 240 UTF-16 units preflight。该 Query 场景不依赖正文 index，因此明确不采用 Search 专用 close/reopen checkpoint。随后又修复 root `notebook_count` 的固定值误报；用户最终确认 cache cold build 真实场景通过。当前纯测试基线为 manual-validation `560 passed`、全仓 `944 passed`。

List→Query 迁移审查清单已经固化在本文件的覆盖矩阵；当前默认 schema diff 为：删除 `query_hierarchy(resource_type,...,limit)`，新增四个固定资源类型入口；必填 discriminated `scope` 只用于后三者；`limit/truncated` 改为与 Search 同形的 `offset/page_size/has_more/next_offset`；Page 的重载 `parent_id` 拆为 `section_id/parent_page_id`；统一增加 `resource_type/query_kind/scope/pagination_consistency`。待用户真实运行并确认场景证据后，仍须取得单独的 List 退役批准，才可执行阶段 B。

## 背景与当前实现

当前默认 profile 只提供一个通用的 `query_hierarchy`：

```text
query_hierarchy(
  resource_type,
  name_equals="",
  name_contains="",
  parent_id="",
  modified_after="",
  modified_before="",
  include_recycle_bin=false,
  limit=100
)
```

该工具固定调用一次 `GetHierarchy("", hsPages)`，取得 OneNote root 下展开到 Page 的完整 hierarchy XML，再在 Python 中按 `resource_type` 和其余条件过滤。它不是逐 Notebook 调用 COM，也没有使用 `FindMeta` 执行复合查询。

现有合同存在以下问题：

- `query_hierarchy` 和 `resource_type` 都偏向内部实现术语，Agent 必须同时阅读名称、参数和返回值才能确定目标对象；
- 即使只查询 Notebook 或 Section，底层仍固定获取到 Page 层级，无法利用 `GetHierarchy` 的原生起点和最浅必要 `HierarchyScope`；
- 工具只能表达全局 root 快照，不能安全表达一个 Notebook、SectionGroup 或 Section 起点；
- Page 的 `parent_id` 同时承担直属 Section 与派生缩进父 Page 两种含义，Agent 可见合同不够明确；
- `resource_type`、时间、limit 和各类父级参数没有形成足够严格的生成 schema；
- 当前实现没有显式排除 `is_open=false` 的 Notebook 及其后代；
- `limit` 被静默夹到 `1..1000`，且名称容易使调用方误以为它能缩小 COM hierarchy 获取或本地过滤工作量；
- 返回缺少稳定的 scope、查询类型和固定资源类型说明。

当前默认 profile 还同时注册 `list_hierarchy`、`list_notebooks`、`list_section_groups`、`list_sections` 和 `list_pages`。它们虽然公开了不同形式的 `start_identifier/parent_id/section_id`，但生产实现同样固定从 `GetHierarchy("", hsPages)` 获取完整 root 快照后在 Python 中裁剪；`list_pages` 还会因先解析 Section、再列 Page 而获取两次完整快照。List 与规划中的 typed Query 在“无过滤条件下枚举固定类型”上高度重叠，并造成 Agent 在 List/Query 之间随机选择。

四个 typed Query 必须先完整覆盖 List 的受支持枚举用例。开发、自动化和真实场景完成后，只有取得用户明确批准，才进入第二阶段并从项目中整体移除全部 `list_*` 能力；在此之前 List 保持原样，不得以 TODO 推断授权提前删除。

此前计划把工具重命名为单一 `global_query`。该方案仍把四种对象、不同合法起点和不同父级关系压在一个入口中，也会继续迫使所有查询采用最大 root/`hsPages` 快照。因此本 TODO 放弃 `global_query`，改为四个按目标资源类型拆分的 typed query 工具。

## 最终公开工具面

默认 profile 最终只注册以下四个元数据查询工具：

```text
query_notebook
query_section_group
query_section
query_page
```

迁移完成后，默认 profile 不再注册 `query_hierarchy`，也不新增 `global_query`。service 可以复用一个不公开的 typed query engine，但不能长期维护等价公开别名。

在用户批准 List 退役且第二阶段完成后，默认 profile 同样不再注册任何 `list_*` 工具。无过滤条件的 `query_*` 是固定资源类型枚举入口；精确 ID 读取继续使用 `get_*`。

四个工具只读取 hierarchy 元数据，不读取 Page XML、正文、内容对象或二进制。Page 正文查询继续使用 `search_pages`。

最终公开签名为：

```text
query_notebook(
  name_equals="",
  name_contains="",
  modified_after="",
  modified_before="",
  offset=0,
  page_size=200
)

query_section_group(
  scope,
  name_equals="",
  name_contains="",
  parent_id="",
  modified_after="",
  modified_before="",
  include_recycle_bin=false,
  offset=0,
  page_size=200
)

query_section(
  scope,
  name_equals="",
  name_contains="",
  parent_id="",
  modified_after="",
  modified_before="",
  include_recycle_bin=false,
  offset=0,
  page_size=200
)

query_page(
  scope,
  title_equals="",
  title_contains="",
  section_id="",
  parent_page_id="",
  modified_after="",
  modified_before="",
  include_recycle_bin=false,
  offset=0,
  page_size=200
)
```

Agent 的选择规则为：

```text
Notebook 元数据                         -> query_notebook
SectionGroup 元数据                     -> query_section_group
Section 元数据                          -> query_section
Page 标题、直属 Section、缩进父页或修改时间 -> query_page
Page 正文内容                           -> search_pages
已知精确 ID 的单对象读取                 -> get_notebook/get_section_group/get_section/get_page
```

## Scope 合同

### `query_notebook`：固定 root

Notebook 是 OneNote hierarchy 的根对象，没有可用的 Notebook 父级 scope。`query_notebook` 不接受 `scope` 参数，固定在当前 OneNote root 下查询全部已打开 Notebook：

```text
query_notebook(
  name_equals="",
  name_contains="",
  modified_after="",
  modified_before="",
  offset=0,
  page_size=200
)
```

底层固定执行一次：

```text
GetHierarchy("", hsNotebooks, xs2013)
```

它不得扫描本地目录、备份或 `.one` 文件，也不得自动打开已关闭 Notebook。`is_open=false` 的 Notebook 必须排除；没有已打开 Notebook 时成功返回空集合。Notebook 查询不提供 `include_recycle_bin`，因为回收站不得扩大 all-open-notebooks 根集合。

### 其余三个工具：显式单一起点

`query_section_group`、`query_section` 和 `query_page` 都要求显式 `scope`：

```text
QueryScope =
  | { mode: "root" }
  | { mode: "start_node", start_node_id: NonEmptyString }
```

两个分支都使用 `extra="forbid"`、`mode` discriminator 和 trim 后非空 ID。`scope` 必填；省略 scope 不得隐式扩大为 root。

各工具允许的原生起点为：

| 工具 | `root` | `start_node` 允许类型 | COM 展开深度 | 结果类型 |
| --- | --- | --- | --- | --- |
| `query_section_group` | 全部已打开 Notebook | Notebook、SectionGroup | `hsSections` | 只保留 SectionGroup |
| `query_section` | 全部已打开 Notebook | Notebook、SectionGroup | `hsSections` | 只保留 Section |
| `query_page` | 全部已打开 Notebook | Notebook、SectionGroup、Section | `hsPages` | 只保留 Page |

`start_node_id` 必须按精确 COM ID 解析，拒绝名称、路径、Page ID、未知 ID、已关闭 Notebook 中的对象、错误类型和回收站中不可用的起点。start node 只界定向下查询的容器子树，不作为候选结果自动包含自身；因此以 SectionGroup 为起点的 `query_section_group` 返回其后代 SectionGroup，与原 `list_section_groups(parent_id=..., recursive=true)` 一致。

一次 Tool 调用只接受一个 start node，不接受 ID 数组，不合并多个离散起点，也不悄悄扩大到 root。多个离散起点由 Agent 分别调用，或请求用户选择共同祖先。

## COM 调用与边界验证

四个工具必须利用 `GetHierarchy` 的原生起点和最浅必要 scope，不得退化为逐 Notebook 循环：

- root 路线只调用一次 `GetHierarchy("", target_scope)`；
- start-node 路线先用一次不展开 Page 正文的 root container catalog 验证精确 ID、真实类型和所属 Notebook 的打开状态，再对已验证 ID 调用一次 `GetHierarchy(start_node_id, target_scope)`；
- start-node catalog 只需展开到验证 Notebook/SectionGroup/Section 所需的 `hsSections`，不得为了验证容器起点先获取全部 Page；
- 返回 fragment 必须再次与同一次验证 catalog 对齐，排除越出起点、已关闭 Notebook、未知 ID 或无法证明归属的对象；
- 任何 COM/XML/归属验证失败都明确失败，不回退到 root、名称匹配、磁盘扫描、`FindMeta` 或逐 Notebook 查询。

`FindMeta` 只提供 start ID、名称和 `include_unindexed` 等有限参数，不能完整表达固定资源类型、直属父级、修改时间、回收站、open-only 边界和准确 `total_matches`。本 TODO 不使用 `FindMeta` 代替 typed query engine。

## 参数合同

### 名称、标题与时间过滤

Notebook、SectionGroup 和 Section 查询使用：

- `name_equals`：不区分大小写的完整标题/名称匹配；
- `name_contains`：不区分大小写的标题/名称子串匹配；

Page 查询使用与 domain 字段一致的：

- `title_equals`：不区分大小写的完整 Page 标题匹配；
- `title_contains`：不区分大小写的 Page 标题子串匹配，不是正文搜索。

四个工具共享：

- `modified_after`：可选 RFC 3339 时间戳，严格匹配 `modified > modified_after`；
- `modified_before`：可选 RFC 3339 时间戳，严格匹配 `modified < modified_before`；
- `offset`：默认 0，必须大于等于 0，在完整候选集合完成过滤后应用；
- `page_size`：默认 200、允许范围 `1..200`，作为当前页最大返回数，在 offset 之后应用；默认值和最大值均与 `search_pages` 一致。

多个过滤条件按 AND 组合。时间必须带明确 offset 或 `Z`，解析后统一比较同一时区；格式非法、`modified_after >= modified_before`、offset 为负或 page_size 越界均显式拒绝，不得静默修正。

`offset/page_size` 组成与 `search_pages` 同形的无状态分页，但不是 COM scope、扫描预算或性能上限。它们不减少 `GetHierarchy` 输出大小和 Python 必须检查的候选数量。每一页都重新执行实时 hierarchy 读取，`pagination_consistency="live_hierarchy"`；返回顺序保留本次 COM snapshot 中相同资源类型的遍历顺序，不建立相关性排序或跨页冻结快照合同。该分页保证无过滤 Query 可以逐页覆盖原 List 的完整枚举用途。

### 容器直接父级

`query_section_group` 与 `query_section` 提供：

```text
parent_id=""
```

非空时只匹配直接 hierarchy 父级：

- SectionGroup 的 `parent_id` 必须是 Notebook 或 SectionGroup；
- Section 的 `parent_id` 必须是 Notebook 或 SectionGroup；
- `parent_id` 必须位于已验证 scope 内，不表示递归 subtree，也不能扩大 scope。

### Page 关系过滤

`query_page` 不再使用含义重载的通用 `parent_id`，改为：

```text
section_id=""
parent_page_id=""
```

- `section_id`：只匹配直属容器 Section；
- `parent_page_id`：只匹配由同一 Section 完整有序 Page 序列派生的直接缩进父 Page；
- 两者同时提供时按 AND 组合，并验证 parent Page 实际属于该 Section；
- 空 `parent_page_id` 表示不按缩进父页过滤，不表示只查询根 Page；
- 本工具只读取 Page metadata，不读取正文；`title_equals/title_contains` 只匹配 hierarchy 中的 Page `title`。

### 回收站

`query_section_group`、`query_section` 和 `query_page` 提供 `include_recycle_bin=false`：

- 只控制已打开 Notebook scope 内可由 COM hierarchy 证明的回收站对象；
- `true` 不能引入已关闭 Notebook、未知 Notebook、磁盘文件或越出 start node 的对象；
- start node 本身位于回收站时默认拒绝；只有对象类型和 COM 行为有稳定合同后，才可另行决定是否允许以回收站节点为起点，本 TODO 不默认开放。

## 返回合同

四个工具统一返回：

```json
{
  "items": [],
  "count": 0,
  "total_matches": 0,
  "offset": 0,
  "page_size": 200,
  "has_more": false,
  "next_offset": null,
  "pagination_consistency": "live_hierarchy",
  "resource_type": "page",
  "query_kind": "hierarchy_metadata",
  "scope": {
    "mode": "root",
    "notebook_count": 0
  }
}
```

- `resource_type` 是工具固定值，不接受调用方输入；
- `query_kind="hierarchy_metadata"` 明确区别于 `search_pages` 的正文索引搜索；
- `count=len(items)`；
- `total_matches` 是完成 scope、open-only、回收站和全部元数据过滤后、应用 offset/page_size 前的数量；
- `has_more` 与 `next_offset` 由当前 live snapshot 计算；末页 `next_offset=null`，越界 offset 成功返回空页；
- Query 不再返回旧 `query_hierarchy.truncated`；调用方统一使用与 `search_pages` 相同的 `has_more/next_offset`；
- root scope 返回 `mode="root"` 和真实 `notebook_count`，不伪造 COM root ID；
- start-node scope 返回 `mode="start_node"`、catalog 中的真实 `resource_type/id/path/notebook_id`，不信任调用方声明类型；
- 没有合法候选或没有已打开 Notebook 时成功返回空集合。

四个工具不返回 Page 正文、raw hierarchy XML、COM 查询字符串、未验证 attribute 或第二套按工具命名的重复 items 字段。

## Agent 可见描述基线

每个 Tool description 必须同时说明目标类型、合法 scope、元数据边界和正文反例。例如：

```text
query_page:
Find Page metadata by title, Section, indentation parent, or modification time
below all currently open OneNote notebooks or one exact Notebook,
SectionGroup, or Section ID. This reads hierarchy metadata only; use
search_pages for Page body text. offset/page_size paginate matches after filtering
and do not reduce GetHierarchy retrieval or metadata scanning.
```

其余三个工具采用相同结构，并替换目标类型、合法 start node 和直接父级说明。所有参数 description 必须进入生成的 Tool schema；不能只写 Python docstring 中的概括性一句话。

## List 覆盖矩阵

四个 Query 在无名称、时间和关系过滤条件时必须覆盖现有 List：

| 现有工具/调用 | 替代调用 | 额外合同 |
| --- | --- | --- |
| `list_notebooks()` | `query_notebook()`，按 `next_offset` 取尽 | 固定 open-only；不延续 Notebook recycle-bin 枚举 |
| `list_section_groups()` | `query_section_group(scope={mode:"root"})`，按页取尽 | root 下全部已打开 Notebook |
| `list_section_groups(parent, recursive=true)` | `query_section_group(scope=start_node(parent))`，按页取尽 | 排除 start node 自身，只含后代 Group |
| `list_section_groups(parent, recursive=false)` | `query_section_group(scope=start_node(parent), parent_id=parent)`，按页取尽 | 只含直属 Group |
| `list_sections()` | `query_section(scope={mode:"root"})`，按页取尽 | root 下全部已打开 Notebook |
| `list_sections(parent, recursive=true)` | `query_section(scope=start_node(parent))`，按页取尽 | 完整容器子树中的 Section |
| `list_sections(parent, recursive=false)` | `query_section(scope=start_node(parent), parent_id=parent)`，按页取尽 | 只含直属 Section |
| `list_pages(section)` | `query_page(scope=start_node(section))`，按页取尽 | `scope` 取代旧响应中的重复 `section` 字段 |
| `list_hierarchy(scope=self)` | 对应 typed `get_*` | 只接受精确 ID，不保留名称/路径 selector |
| `list_hierarchy(scope=children)` | 按父类型调用一个或两个 typed Query | 不保留混合资源类型单响应 |
| `list_hierarchy(include_xml=true)` | 无替代 | raw hierarchy XML 不再作为生产工具能力 |

原 List 返回的 `notebooks/sections/pages/section` 等不一致 envelope 不保留兼容；统一迁移为 `items/resource_type/query_kind/scope` 与分页字段。依赖完整枚举的调用方必须按 `next_offset` 取尽，并接受 `live_hierarchy` 不冻结跨页 snapshot。

## 两阶段迁移与用户批准门

仓库当前仍为 `0.1.0` alpha，执行一次协调的公开契约替换：

### 阶段 A：实现并验证 Query，保留 List

1. 新增 `query_notebook`、`query_section_group`、`query_section`、`query_page`；
2. 默认 profile 删除 `query_hierarchy`；不注册 `global_query`；
3. service 内部允许共享一个按固定资源类型分派的 query engine，但不接受来自 MCP 的任意 `resource_type`；
4. List 在本阶段继续注册，现有调用方和 manual-validation 不提前迁移或删除；
5. health check 增加稳定的 metadata-query capability，例如四个工具名、支持的 scope mode、分页和 `query_kind`；
6. 完成 Query 聚焦合同、完整纯测试、`query-metadata-scopes` dry-run，并由用户运行真实场景确认；
7. 形成 List→Query 调用迁移清单和默认 Tool schema diff，提交用户审查。

### 用户批准门

List 退役必须取得用户在 Query 实现和验证证据完成后的明确批准。以下均不构成批准：本 TODO 的存在、一般代码修改授权、阶段 A 合并、测试通过、用户运行场景或 Agent 推断。若用户尚未批准，TODO 保持“进行中”或在无其他可推进工作时标记为具体原因的“阻塞”，不得进入阶段 B。

### 阶段 B：从项目整体移除 List

用户明确批准后，执行一次完整删除，不保留 deprecated alias 或隐藏公开入口：

1. 从 tools 层和默认注册表删除 `list_hierarchy`、`list_notebooks`、`list_section_groups`、`list_sections`、`list_pages`；
2. 删除生产 service 中仅服务于这些工具的同名 `list_*` 方法，内部调用迁移到 typed query/catalog/get helper，不保留换名空壳；
3. 将 manual-validation fixture、lifecycle、scenario allowlist 和 MCP 客户端调用迁移到四个 Query 或精确 `get_*`，按 `next_offset` 取尽需要完整集合的结果；
4. 删除或改写全部 List 自动化测试、schema snapshot、README、design、dev、overview、示例和 health capability 引用；
5. `list_hierarchy(include_xml=true)` 不提供替代工具，确认 raw hierarchy XML 继续不进入生产 MCP；
6. 全仓库搜索必须证明不存在生产 `list_*` Tool 名、适配器、service 方法和过期调用；普通 Python/标准库的 `list` 用法不属于删除目标；
7. 运行迁移聚焦测试、manual-validation 纯合同和完整纯测试集，必要时由用户复跑受工具 allowlist 变化影响的真实场景。

不长期维护 `query_hierarchy`、`global_query` 或 List alias。若发布兼容性另有要求，必须在用户批准 List 删除前通过独立决策修改本 TODO，不能在实施中临时保留。

## 自动化合同

至少覆盖：

- Metadata Query 工具名只包含四个新 `query_*`，不包含 `query_hierarchy` 或 `global_query`；阶段 A 仍同时保留五个 List，阶段 B 才删除；
- 四个 schema 没有 `resource_type`，参数枚举、discriminator、ID 和时间约束均可见；`offset/page_size` 的名称、默认值及 `ge=0`、`1..200` 边界与 `search_pages` 完全一致；
- `query_notebook` 固定使用 root + `hsNotebooks`，其他工具 root 路线分别只使用一次最浅必要 `GetHierarchy`；
- start-node 路线的精确类型矩阵，以及 Page/未知/关闭/回收站/越界 ID 的 fail-closed 拒绝；
- 不接受多个 start IDs，不逐 Notebook 调用，不用 `FindMeta` 或 root fallback；
- Notebook/SectionGroup/Section 的名称、Page 标题、修改时间、空结果、offset/page_size、`total_matches`、`has_more/next_offset` 和 live hierarchy 遍历顺序；
- SectionGroup/Section 的直接 `parent_id`；Page 的 `section_id`、`parent_page_id`、两者组合和完整扁平 Page 序列派生关系；
- `include_recycle_bin=true` 不能突破 open-only 和 start-node 边界；
- `query_page` 不调用任何 Page XML、正文、对象或二进制读取；
- 固定 `resource_type/query_kind/scope` response envelope 和 health capability；分页字段与 `search_pages` 同为 `offset/page_size/has_more/next_offset/pagination_consistency`，不保留 `truncated`；
- README、当前设计文档和默认 Tool 注册表不存在过期旧名或未实施的 `global_query`。
- 无过滤 Query 逐页取尽与对应 List 返回集合等价，覆盖 recursive true/false、Page Section scope 和 start node 排除语义；
- 阶段 B 后默认注册表、生产 tools/services、manual-validation allowlist、测试与文档均不存在目标 `list_*` 能力。

## 真实后端验证场景

human-gated 场景 `query-metadata-scopes` 已经真实验收并设置 `included_in_all=true`。查询本身只读，但 fixture 使用本次 run 创建的 disposable Notebook，因此真实运行仍只能由用户显式启动；Agent 只运行纯测试和 `--dry-run`。

场景至少准备两个同时打开的 Notebook，每个包含嵌套 SectionGroup、Notebook/Group 直属 Section、根 Page 和缩进 Page，并使用 run-unique 名称和修改时间证据。验证：

1. 四个 root 查询都只返回两个打开 role 中的匹配对象；
2. Notebook、SectionGroup、Section 三类原生起点分别只返回合法子树；
3. Page 的 `section_id` 与 `parent_page_id` 过滤准确且不读取正文；
4. 一个 disposable Notebook 由 lifecycle wrapper 精确关闭后，其本身和后代不再参与 root 匹配；若 COM 仍返回 `isClosed=true` 节点，生产过滤必须排除；
5. `include_recycle_bin=true` 不重新引入已关闭 Notebook；
6. 使用足以产生多页的 fixture 验证 offset/page_size、末页、越界页和独立完整 hierarchy evidence 计算出的 `total_matches/has_more/next_offset`；
7. 保存每个请求、原始 typed hierarchy evidence、响应和独立 expected 结果，任何字段不一致都非零退出并保留现场。

场景不得扫描磁盘、打开用户 Notebook、依赖 Page 正文口令或执行查询所不需要的 mutation。fixture 创建使用现有 Writes 最小权限；永久删除、raw XML、Reparent、Copy 和 Move 均关闭。

## 实施范围

1. 建立固定资源类型的内部 typed query engine 和四个公开 tools adapter；
2. 实现 root/start-node COM 映射、open-only catalog 验证和最浅必要 `HierarchyScope`；
3. 实现严格名称、直接父级、Page 缩进父级、RFC 3339 时间、回收站与 offset/page_size live pagination 合同；
4. 删除默认 `query_hierarchy` 注册，不新增 `global_query`；
5. 返回统一的 typed query envelope，并更新 health capability；
6. 补齐自动化矩阵和 `query-metadata-scopes` 的 scenario-owned recipe、静态权限、dry-run 与 runtime evidence；
7. 取得用户对 List 退役的明确批准；未经批准不得执行后续删除；
8. 执行阶段 B，整体删除五个 `list_*` 工具及其生产、测试、manual-validation 和文档依赖；
9. 同步 canonical 设计、README、manual-validation README、overview 和 TODO 索引。

## 完成定义

- 默认 Tool 列表只公开 `query_notebook`、`query_section_group`、`query_section`、`query_page`，不再公开 `query_hierarchy` 或 `global_query`；
- 四个工具名称、description、参数 schema 和返回结构均固定表达目标资源类型与 hierarchy metadata 边界；
- 四个工具的分页参数与 `search_pages` 对齐为 `offset=0, page_size=200`，最大 `page_size=200`；响应使用相同分页字段，只有一致性值按后端分别为 `live_hierarchy` 与 `live_index`；
- `query_notebook` 使用 root/`hsNotebooks`；其余工具支持规定的 root 或一个精确原生 start node，并使用最浅必要 scope；
- 所有 root 结果只属于当前已打开 Notebook；start-node 结果同时满足 open-only、精确类型和单一子树边界；
- Page 标题、Section 容器和缩进父页均可查询，且不读取 Page 正文；
- 时间、直接父级、回收站和 offset/page_size 行为有明确 fail-closed 合同，无过滤 Query 可以通过 live pagination 取尽当前类型；
- 不逐 Notebook 扫描、不合并多个起点、不使用 `FindMeta` 模拟复合查询，也不回退到磁盘或名称解析；
- 聚焦合同和完整纯测试集通过；`query-metadata-scopes --dry-run --json` 为零副作用成功；
- 用户显式运行并确认 `query-metadata-scopes` 的 root、三类 start node、关闭 Notebook 排除、Page 缩进父级和分页证据；
- Query 实现与证据完成后，用户明确批准 List 退役；批准发生前不得声称本 TODO 完成；
- 批准后从项目中整体移除 `list_hierarchy/list_notebooks/list_section_groups/list_sections/list_pages`，不保留 alias、生产 service 空壳、manual-validation 调用或文档示例；
- 全部原 List 受支持用例已经迁移到无过滤 typed Query、精确 `get_*` 或明确取消的 raw XML/mixed-type 行为；
- 当前 design 文档、README、health check、manual-validation README、overview、TODO 索引和最终实现一致。
