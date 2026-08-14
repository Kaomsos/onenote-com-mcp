# 008：全部已打开 Notebook 的全局 Page 搜索

> ID：008
> 状态：已完成
> 优先级：P1
> 类型：公开工具契约 / Search 能力
> 更新日期：2026-08-14

## 背景与当前状态

最初的 `search_pages` 只允许以一个 Notebook、SectionGroup 或 Section 为范围，无法表达 OneNote Desktop 对 root 下全部已打开 Notebook 的搜索。2026-08-10 的过渡实现已经加入 `all_open_notebooks`，并同时公开 `local_scan` 与 `onenote_index` 两个 backend；当前默认仍是 `local_scan`。

2026-08-13 已完成公开参数和 index-only 生产迁移。`search_pages` 现在直接采用 OneNote COM `FindPages`，Tool 参数中不再暴露 `backend`：

- OneNote index 是唯一公开搜索实现；
- `local_scan` 的生产代码可以保留为内部实现和诊断基础，但不能由公开 Tool 参数选择，也不能在 index 失败时自动回退；
- scope 只表达 COM `FindPages.bstrStartNodeID` 原生支持的一个起点：root、Notebook、SectionGroup 或 Section；
- 不支持多个离散 Notebook、多个子节点、混合节点集合或服务端合并多次 `FindPages` 结果。

公开契约、自动化与真实后端证据均已完成。用户已经确认 fresh 与 validated cache hit 两条路径的 OneNote index readiness、真实 COM scope、分页和预算行为，场景现已通过 `included_in_all=true` 纳入显式 human-gated 批处理。

2026-08-14 已按当前 scenario-owned fixture bundle 框架重新审计该旧场景：两个 role 由验证上下文显式绑定，不再通过 manifest key 猜测 role；构建验证覆盖完整 role key set、typed parent、Page Section/root level、每 Page 单次内容 snapshot 和 bundle ID/path 唯一性，并证明 raw probe 未写入 fixture JSON evidence。Recipe 现支持 cache；materialized working copy 在既有唯一一次 Page XML snapshot 中把模板 probe 仅重建到进程内存，不重复读 Page。固定 probe 的碰撞风险不再预防性拖慢所有运行，只有查询稳定返回预期 ID 严格超集时才写 content-free warning 并 fail closed。

同日首次真实运行证明 fixture 本身通过，但 `FindPages` 在 20 次有界 readiness 观察中始终返回零命中。针对 index activation，场景现在只在 fresh 模式的全部 Page 写入后执行一次 Search 专用的 `CloseNotebook(force=false) → exact-path reopen → typed relative-address rebind → 两次 hierarchy 稳定 → 每 Page 一次完整 snapshot`；不把 checkpoint 扩展到普通 fresh fixture、typed Query 或 cache working copy。Search execute 直接使用 Recipe 内存中的 probe，移除 snapshot 后重复的五次 `get_page_text`。用户随后确认 `run-2026-08-14-16-12-17` 与 `run-2026-08-14-17-05-06` 的 fresh Search 均通过，并确认 `run-2026-08-14-17-07-11` 的 validated cache hit 通过；三次均完成默认 lifecycle 关闭且未打开 immutable template。

## 最终公开 Tool 契约

```text
search_pages(
  query,
  scope,
  offset=0,
  page_size=200,
  include_snippets=true,
  include_recycle_bin=false
)
```

公开参数中没有 `backend`、`include_unindexed` 或 `display`。内部 COM 调用固定使用：

```text
FindPages(
  start_id,
  query,
  include_unindexed=false,
  display=false,
  schema=xs2013
)
```

- `include_unindexed=false` 保持 index-only 语义，不把未索引 Page 的额外扫描暴露为另一种隐式 backend；
- `display=false` 确保 MCP 搜索不改变用户当前 OneNote UI；
- index/COM 失败直接返回 backend error，不回退到 `local_scan`。

## Scope：直接映射 COM 起点

最终 `scope` 是必填的判别联合：

```text
SearchScope =
  | { mode: "root" }
  | { mode: "start_node", start_node_id: NonEmptyString }
```

### Root：全部已打开 Notebook

```json
{
  "query": "needle",
  "scope": {
    "mode": "root"
  }
}
```

`mode="root"` 映射为 COM `FindPages(start_id="", ...)`，表示搜索本次 OneNote root 可见的全部已打开 Notebook。该分支不得携带 `start_node_id` 或其他额外字段，也不扫描已关闭 Notebook、备份目录或磁盘上的 `.one` 文件。

在具名真实场景通过前，空 `start_id` 与 Desktop `Ctrl+E` 的完全等价性仍只是假设，不作为已经验证的跨版本事实。

### 一个原生 hierarchy 起点

```json
{
  "query": "needle",
  "scope": {
    "mode": "start_node",
    "start_node_id": "{A-REAL-ONENOTE-COM-ID}"
  }
}
```

`mode="start_node"` 将 `start_node_id` 原样映射为 COM `FindPages` 的单个 `bstrStartNodeID`。调用前必须使用同一次完整 hierarchy catalog 按精确 ID 验证：

- 允许 Notebook、SectionGroup 或 Section；
- 拒绝 Page、未知对象、空 ID、名称、路径和任何模糊解析；
- 对象必须属于当前可用的已打开 Notebook，不能通过显式 ID 绕过 root 的 local-only/open-only 边界；
- 返回的 `scope` 使用 catalog 中的真实 `resource_type`、ID 和路径，不信任调用方重复声明对象类型。

Tool schema 使用两个 `extra="forbid"` 的对象分支、`mode` discriminator，以及 `start_node_id` 的 trim 后 `minLength=1` 约束。`scope` 必填；不得把省略 scope 解释成 root，以免 Agent 漏传参数时意外扩大搜索范围。

### Agent 调用规则

- 用户要求“全部笔记本”或没有指定某个容器时，Agent 显式使用 `scope={"mode":"root"}`；
- 用户指定一个 Notebook、SectionGroup 或 Section 时，Agent 先通过 typed list/query Tool 取得精确 ID，再使用 `scope={"mode":"start_node","start_node_id":"..."}`；
- 用户指定多个离散节点时，`search_pages` 不接受 ID 数组。Agent 应说明单次 `FindPages` 只有一个原生起点，并请求用户选择共同祖先或分别发起独立搜索；不得悄悄扩大为 root，也不得在 Tool 内部合并多次搜索；
- `start_node_id` 不接受 Page ID。搜索单个 Page 正文应使用 Page 读取能力，而不是伪造 `FindPages` scope。

## OneNote Index 执行语义

每次调用执行以下固定流程：

1. 取得一次完整 hierarchy catalog，用于验证 scope、识别已打开 Notebook，并为局部结果补全 typed metadata；
2. 将 root 归一化为 `start_id=""`，或将已验证的 `start_node_id` 作为唯一 COM 起点；
3. 只调用一次 `FindPages`，不进行逐 Notebook/逐子节点调用；
4. 解析 `FindPages` 返回的局部 hierarchy XML，并用同一次 catalog 按 Page ID 补全 `notebook_id`、Section、父级和路径；
5. 排除不属于已验证范围、已关闭 Notebook 或按参数应排除的回收站结果；
6. 先对过滤后的完整候选集执行候选预算检查，再应用 `offset/page_size`；
7. 仅当 `include_snippets=true` 时，有界读取当前页命中 Page 的正文并生成 snippet。

OneNote 决定查询解析、分词、索引相关性和原始结果顺序。调用方可传入 OneNote UI 接受的搜索字符串，包括其支持的 `AND` / `OR` 语法；本服务不把它重新解释为 `local_scan` 子串语义，也不对结果重新评分。

## Index Budget

移除 backend 参数不等于移除 SearchBudget。预算继续对唯一公开的 index 路径生效：

| 预算 | Index 路径中的约束 |
| --- | --- |
| `max_pages` | `FindPages` 返回并通过 scope/回收站过滤后的候选 Page 总数上限；默认 1000，在分页切片和正文读取前检查。 |
| `max_page_chars` | 生成 snippet 时，单个命中 Page 最多处理的正文字符数。 |
| `max_total_chars` | 单次调用为 snippet hydration 累计处理的正文字符数上限。 |
| `max_seconds` | 从发起 `FindPages` 前开始累计的搜索与 snippet hydration 时间上限；bridge 自身的进程超时仍是独立上限。 |
| `snippet_chars` | 每个返回 snippet 的最大字符数。 |

具体规则：

- `offset >= 0`，`1 <= page_size <= 200`；默认页大小和最大页大小均为 200；
- 每一页都会重新执行一次 `FindPages`，跨页一致性明确为 `live_index`，不承诺冻结快照；
- 即使 `include_snippets=false`，index 候选页数与搜索耗时预算仍然有效；只有正文字符预算不会产生消耗；
- `include_snippets=true` 时，在读取任何命中 Page 正文前，先确认待 hydration 的结果数不超过 `max_pages`；
- 任一预算超限都明确失败，不返回一个被误认为完整的部分结果；
- `scan_budget` 字段为兼容可以暂时保留，但其内容应明确采用 index 名称，例如 `candidate_pages/hydrated_pages/hydrated_chars/max_*`，不能暗示执行了 local scan；后续是否重命名为 `search_budget` 作为独立契约变更处理。

## 返回契约与错误边界

成功继续返回：

```text
pages, count, total_matches,
offset, page_size, has_more, next_offset,
pagination_consistency, scope, search_backend, scan_budget
```

- `search_backend` 暂时保留为固定值 `onenote_index`，用于可观察性和兼容，不代表调用方仍可选择 backend；
- root scope 返回合成描述，例如 `resource_type="root"`、`notebook_count`，不得伪造 COM root ID；
- start-node scope 返回 catalog 中解析出的真实 Notebook、SectionGroup 或 Section；
- root 中没有已打开 Notebook、或合法 scope 没有命中时，成功返回空结果；
- `count=len(pages)`；`total_matches` 是过滤后、分页前候选数；末页 `next_offset=null`，越界 offset 成功返回空页；
- 未知 mode、额外字段、缺失/空 `start_node_id`、Page ID、未知/关闭对象、空 query 或非法分页参数均 fail closed；
- COM/index 不可用、索引尚未就绪或返回无法安全解析的 XML 时明确失败，不调用 `local_scan`。

## Local Scan 的保留边界

现有 `local_scan` 代码暂时保留，但迁移后必须满足：

- 不出现在 `search_pages` 参数 schema、Tool 描述、README 示例或 `health_check` 的公开可选 backend 列表中；
- 不作为 `FindPages` 失败、超时、无结果或索引未就绪时的 fallback；
- 可以保留纯测试、内部诊断和未来显式设计决策所需的实现，但不得形成隐藏的环境变量或其他公开选择路径；
- 若未来确认不再需要，应通过独立清理变更删除，不在本 TODO 中为了简化迁移强制移除。

## 实施范围

1. 将 `search_pages` 迁移为 `query + scope + offset + page_size + include_snippets + include_recycle_bin`，删除公开 `backend` 参数；
2. 用 `root/start_node` 判别联合替换过渡的 `scope_type/scope_id`，并把它严格映射到一个 COM `start_id`；
3. 让所有公开搜索固定执行 `onenote_index`，移除 health/tool schema 中的 backend 选择，同时保留无公开入口的 `local_scan` 实现；
4. 对 index 候选结果、耗时和可选 snippet hydration 完整执行 SearchBudget，并保证候选预算先于分页切片；
5. 补充自动化合同测试：Tool schema 不含旧参数、两个 scope 分支、root/Notebook/SectionGroup/Section、Page/未知/关闭 ID 拒绝、单次 `FindPages`、无 fallback、空结果、回收站、分页和全部 index budget 边界；
6. 开发 human-gated 的 `search-all-open-notebooks` 场景，以两个 run-scoped Notebook 验证 root 搜索；同时在 fixture 内验证一个 Notebook、SectionGroup 和 Section 起点，但不测试多个离散起点；
7. 同步更新 `docs/design/tool_contracts.md`、`docs/design/architecture.md`、根 README、health-check 文档及 manual validation 说明。

## 完成定义

- `search_pages(query, scope={"mode":"root"})` 只调用一次空 `start_id` 的 `FindPages`，并能返回多个已打开 Notebook 的命中；
- `search_pages(query, scope={"mode":"start_node","start_node_id":"..."})` 原生支持一个 Notebook、SectionGroup 或 Section 起点；
- Tool schema 不包含 backend 参数，也不支持多个起点或 ID 数组；
- 所有公开调用固定返回 `search_backend="onenote_index"`，index 失败绝不回退；
- index 候选页数、耗时、snippet 页数/单页字符/总字符和 snippet 长度预算均有稳定合同和自动化覆盖；
- 原有 `local_scan` 实现可以继续存在，但没有公开选择入口；
- 用户显式运行具名真实场景并确认 root 下双 Notebook 命中、单起点范围、结果归属、预算证据和 index readiness 行为；
- 聚焦测试与完整纯测试集通过，canonical 设计文档、README、TODO 索引和实现一致。

## 完成证据

公开参数、index budget、分页和具名多 role 场景均已实现；用户真实运行已经确认空 root 起点、四层范围、索引 readiness、分页稳定性和预算错误。聚焦及全仓纯测试通过，Search 场景进入 `all`。

该场景最低要求：

- 创建两个全新、run-scoped、同时保持打开的 Notebook role，每个 role 中创建带同一 run-unique 口令的 Page；
- `scope={"mode":"root"}` 必须命中两个 role，并返回不同的非空 `notebook_id` 和正确路径；
- 分别以一个 Notebook、SectionGroup 和 Section ID 作为 `start_node_id`，证明结果不会越出该 COM 起点；
- 验证 `page_size=2` 的两页结果、越界 offset 无法规避候选页数预算、snippet hydration 预算、无 backend 参数和 index 错误无 fallback；
- 对 index readiness 使用有界只读轮询并保存每次尝试；超时只能报告 `index_not_ready_or_failed`；
- 保存 fixture manifest、lifecycle lease、调用参数、脱敏命中归属、scope、固定 backend、budget 和错误证据；默认精确关闭 fixture Notebook，不删除本地 Notebook 文件；
- `included_in_all=true`；fresh 与 validated cache hit 的索引时序和双 role finalize 均已有用户真实通过证据。

真实 scenario 只能由用户显式启动；Agent、pytest、CI、hook、timer 和 watcher 只能运行 dry-run 或纯合同测试。

## 决策与实施记录

- 2026-08-10：过渡实现加入 `all_open_notebooks`、空 `scope_id`、`local_scan/onenote_index` 两种 backend、完整 catalog hydration 和 index snippet budget；相关纯合同已加入。
- 2026-08-11：确认正式收口需要两个 run-scoped Notebook 的具名、human-gated 场景；真实证据完成前，不宣称空 `start_id` 与 Desktop `Ctrl+E` 完全等价。
- 2026-08-12：曾评估用判别联合表达多个指定 Notebook；该方案随后取消，不进入最终合同。
- 2026-08-12：最终决定公开 Search 固定使用 OneNote index，删除 backend 选择；scope 只映射 COM 的 root 或一个 `start_node_id`，不支持多个离散子节点。`local_scan` 代码保留但不公开，SearchBudget 继续完整约束 index 路径。
- 2026-08-13：完成 index-only Tool、严格 scope union、无状态分页、1000 默认候选预算、bridge 剩余时间 timeout、自动化合同和 fresh-only 双 Notebook 场景实现；真实场景尚未由用户执行，TODO 保持进行中。
- 2026-08-14：用户确认 fresh 与 validated cache hit Search 均通过，默认 lifecycle 精确关闭；场景纳入 `all`，TODO 标记完成。
