# 008：全部已打开 Notebook 的全局 Page 搜索

> ID：008
> 状态：进行中
> 优先级：P1
> 类型：公开工具契约 / Search 能力
> 更新日期：2026-08-11

## 背景

当前 `search_pages` 强制要求 `scope_type` 为 `notebook`、`section_group` 或 `section`，并把非空 `scope_id` 解析为一个真实 hierarchy 对象。因此单次调用的最大范围是一个 Notebook，不能表达 OneNote Desktop `Ctrl+E` 对全部已打开 Notebook 的检索。调用方虽然可以先 `list_notebooks` 再逐个搜索，但这会把 `max_results`、扫描预算和错误边界拆散到多个请求中，也无法形成稳定的全局结果契约。

## 目标

为 `search_pages` 增加向后兼容的全局 scope：

```json
{
  "query": "needle",
  "scope_type": "all_open_notebooks",
  "scope_id": ""
}
```

- `scope_type="all_open_notebooks"` 表示查询本次 hierarchy 快照中的全部已打开 Notebook；
- 该 scope 不扫描已关闭 Notebook、本地备份目录或磁盘上的 `.one` 文件，继续保持基于 OneNote COM hierarchy、local-only 且不直接解析二进制文件；
- `scope_id` 对该 scope 必须为空，并在公开 tool 签名中变为可省略的默认空字符串；现有三种 typed scope 仍必须提供非空、类型匹配的 ID；
- `max_results` 是整个全局查询的上限，不得按 Notebook 重置；
- 现有调用者、默认 backend 和单 Notebook/Section 搜索语义保持不变。

## Backend 语义

### `local_scan`

- 从同一次 hierarchy 快照取得全部已打开 Notebook 的候选 Page，不通过名称重新选择对象；
- 在读取首个 Page 正文前，对跨 Notebook 的候选 Page 总数执行一次 `SearchBudget.max_pages` 检查；
- `max_page_chars`、`max_total_chars`、`max_seconds` 和 `max_results` 均按整个调用累计，不得为每个 Notebook 重新计数；
- 结果保持稳定、可复现的 hierarchy 顺序，并保留每个 Page 的 `notebook_id`、路径及其他 typed 元数据；
- `include_recycle_bin=false` 时排除所有 Notebook 的回收站 Page，`true` 时仍受同一全局预算约束。

### `onenote_index`

- 对 OneNote COM `FindPages` 传入空 `start_id`，由 OneNote index 在全部已打开 Notebook 中执行一次查询；
- COM/index 失败必须作为 `onenote_index` 错误返回，不得静默回退到 `local_scan`；
- 解析结果时使用同一次完整 hierarchy catalog 补全 `notebook_id`、父级和路径；
- `max_results` 在跨 Notebook 结果上统一截断，snippet hydration 也必须受有界的页数、字符数和耗时控制；
- 在真实 OneNote 验证前，不把空 `start_id` 与 Desktop `Ctrl+E` 的完全等价性视为已证实事实。

## 返回契约与错误边界

- 全局查询继续返回 `pages`、`count`、`search_backend` 和 `scan_budget`；
- `scope` 返回稳定的合成描述，例如 `resource_type="all_open_notebooks"`，并报告本次快照覆盖的 Notebook 数量；不得伪造真实 OneNote 对象 ID；
- `scope_type="all_open_notebooks"` 搭配非空 `scope_id` 时 fail closed，避免调用方误以为该 ID 会进一步过滤范围；
- typed scope 搭配空 `scope_id`、未知 scope、空查询和无效 backend 继续明确拒绝；
- 没有已打开 Notebook 或没有候选 Page 时成功返回空结果，而不是退化为磁盘扫描；
- 全局范围超过预算时返回明确的预算错误，且不得先读取部分 Page 后再以候选数量超限结束。

## 实施范围

1. 扩展 `search_pages` tool schema、`SearchService.search` 的 scope 校验与合成 scope 返回结构；
2. 复用单次完整 hierarchy 快照实现 `local_scan` 全局候选集，避免逐 Notebook 重复枚举和重复预算；
3. 为 `onenote_index` 接入空 `start_id` 的全局 `FindPages` 调用，并保持无隐式 backend fallback；
4. 对 index 结果的 snippet hydration 增加全局有界行为，确保全局 scope 不绕过搜索资源上限；
5. 补充自动化合同测试，覆盖两种 backend、跨 Notebook 命中、全局结果上限、全局预算、回收站过滤、空 hierarchy、scope 参数组合、metadata hydration 和显式失败；
6. 开发具名、human-gated 的 `search-all-open-notebooks` 验证 scenario：程序化构建两个 run-scoped Notebook role 及同口令 Page fixture，再验证两种 backend 的跨 Notebook 命中、结果归属、全局上限和错误行为；真实场景仍只能由用户显式启动；
7. 实现完成后同步更新 `docs/design/tool_contracts.md`、`docs/design/architecture.md`、根 README 及相关 search/health-check 文档。

## 完成定义

- `search_pages(query, "all_open_notebooks")` 可在一次调用中返回多个已打开 Notebook 的 Page，并允许省略 `scope_id`；
- 原有 `notebook/section_group/section` 调用和默认 `local_scan` 行为保持兼容；
- `local_scan` 对全部候选 Page 使用单一、先检查后读取的全局预算，结果数和耗时等计数不会按 Notebook 重置；
- `onenote_index` 使用空 `start_id` 执行全局查询，失败时不回退，结果可正确补全所属 Notebook；
- 全局 `scope`、空结果、回收站、snippet、预算超限和参数冲突拥有稳定响应或错误合同；
- 自动化测试覆盖两种 backend 及关键边界，并通过完整纯测试集；
- 用户显式运行双 Notebook `search-all-open-notebooks` scenario 并确认真实检索证据后，记录环境、调用参数、命中归属和 backend 结果；
- 当前设计文档、README 和 TODO 索引与最终实现一致。

## 下一步：开发双 Notebook 验证 Scenario

当前状态保持“进行中”。公开 Search 实现与纯合同已经交付，但真实验收不再以手工准备任意 Notebook 作为正式收口路径；下一步需要开发具名 `search-all-open-notebooks` scenario。

该 scenario 的最低合同为：

- 由受 lifecycle lease 约束的通用 Notebook bundle 机制创建两个全新、run-scoped、同时保持打开的 role，例如 `search_a` 与 `search_b`；不得让场景 MCP 获得任意 `create_notebook` 能力，也不得复用用户 Notebook；
- 在每个 role 中程序化创建一个 Section 和一个 Page，两个 Page 正文包含同一 run-unique 检索口令，标题分别稳定编号，fixture validator 证明 Notebook ID 不同、Page 归属正确且口令可回读；
- fixture 创建需要 Writes，因此真实 scenario 仍为 human-gated，只能由用户显式运行；被验收的 `search_pages` 调用本身保持只读，pytest/CI/智能体只能运行 dry-run 和纯合同；
- 同一场景 MCP 依次验证 `local_scan` 与 `onenote_index`：两个 backend 都必须命中两个 role，返回不同的非空 `notebook_id` 和正确 `path`，`scope.resource_type="all_open_notebooks"`，且没有 backend fallback；
- 额外验证 `max_results=1` 是调用级全局上限，以及 `all_open_notebooks + 非空 scope_id` 明确 fail closed；
- 对 `onenote_index` 使用有界、只读 readiness 轮询，并保存每次尝试。成功出现连续稳定的双 Notebook 命中可直接判定通过；超时只能报告 `index_not_ready_or_failed`，不得伪装成功或自动回退到 `local_scan`；
- 保存 fixture manifest、两个 lifecycle lease、调用参数、脱敏后的命中归属、scope/backend/budget、readiness attempts 和错误响应；默认精确关闭两个 fixture Notebook，不删除本地 Notebook 文件；
- 初始 `included_in_all=False`。在真实索引时序和双 role finalize 稳定前，不进入批量真实场景。

现有 runner 的 `NotebookLifecycleWrapper`、manifest 和 finalize 仍以单 Notebook/单 lease 为中心；scenario 开发应先抽取最小的 role-aware bundle lifecycle。无需先实现 TODO 014 的模板缓存，但 bundle/role 合同应与其设计一致，避免添加只服务于 TODO 008 的双 Notebook 特例。

## 临时人工参考（正式收口将由 Scenario 替代）

在上述 scenario 完成前，本节仅用于理解预期结果或人工排障，不再作为 TODO 008 的首选正式验收路径。目标仍是证明一次全局调用能同时命中两个当前已打开的 Notebook，而不是分别搜索两次。

### 1. 在 OneNote UI 准备两个命中

1. 选择两个内容可控且当前保持打开的 Notebook，以下称为 Notebook A 和 Notebook B；为减少干扰，可以暂时关闭无关 Notebook。
2. 在 A 中创建或编辑一个测试 Page，在 B 中创建或编辑另一个测试 Page。
3. 在两个 Page 正文中写入完全相同且不会自然出现的口令，例如 `TODO8-ACCEPT-20260811-X7Q9`。两个 Page 标题应不同，例如 `TODO8-A`、`TODO8-B`。
4. 等待 OneNote 保存。对于 index backend，先确认 OneNote UI 的 `Ctrl+E` 已能找到两个 Page；这一步只用于确认索引已更新，不把两者的完全等价性作为预设结论。

### 2. 分别调用两个 backend

通过当前连接 local-onenote MCP 的 Codex、Claude Code 或其他 MCP host 调用工具。`scope_id` 必须省略，不要填写 Notebook ID。

第一次调用：

```json
{
  "query": "TODO8-ACCEPT-20260811-X7Q9",
  "scope_type": "all_open_notebooks",
  "backend": "local_scan",
  "max_results": 10,
  "include_snippets": true
}
```

第二次只把 backend 改为 `onenote_index`：

```json
{
  "query": "TODO8-ACCEPT-20260811-X7Q9",
  "scope_type": "all_open_notebooks",
  "backend": "onenote_index",
  "max_results": 10,
  "include_snippets": true
}
```

### 3. 判断是否通过

两个响应都必须满足：

- `search_backend` 与本次请求一致，没有 backend 静默回退；
- `scope.resource_type == "all_open_notebooks"`，且 `scope.notebook_count >= 2`；
- `pages` 至少包含 `TODO8-A` 和 `TODO8-B` 两项；
- 两项具有不同的非空 `notebook_id`，其 `path` 分别指向 Notebook A 与 Notebook B；
- `count <= max_results`，证明结果上限是整个调用的上限。

再做一个 fail-closed 参数检查：

```json
{
  "query": "TODO8-ACCEPT-20260811-X7Q9",
  "scope_type": "all_open_notebooks",
  "scope_id": "任意非空值"
}
```

该调用必须失败并明确说明 `scope_id must be empty when scope_type is all_open_notebooks`，不能忽略该 ID，也不能退化为单 Notebook 搜索。

### 4. 回传最小证据

无需提交 Page 正文或完整 Notebook 数据。只需记录并回传下表；ID 可保留首尾少量字符，其余打码：

| 项目 | local_scan | onenote_index |
| --- | --- | --- |
| `search_backend` |  |  |
| `scope.notebook_count` |  |  |
| Notebook A 命中：标题 / `notebook_id` / `path` |  |  |
| Notebook B 命中：标题 / `notebook_id` / `path` |  |  |
| `count` |  |  |
| `scan_budget` 是否存在 |  |  |

另附非空 `scope_id` 调用的错误文本。以上证据确认后即可关闭 TODO 008；测试 Page 是否保留由用户自行决定。

## 实施与验证记录

- 2026-08-10：`search_pages.scope_id` 已改为默认空字符串，并加入 fail-closed 的 `all_open_notebooks` 参数组合校验；原三类 typed scope 仍要求精确、类型匹配的非空 ID。
- 2026-08-10：两种 backend 都复用一次完整 hierarchy catalog。`local_scan` 在任何 Page 正文读取前对跨 Notebook 候选集合执行一次预算检查；`onenote_index` 对全局 scope 传空 `start_id`，并对 snippet hydration 统一施加页数、字符和耗时限制。
- 2026-08-10：已增加跨 Notebook 命中、统一 `max_results`、候选预算、回收站、已关闭 Notebook、空 hierarchy、scope 冲突、index metadata hydration、snippet 页数预算和 index 显式失败的纯合同测试。聚焦测试命令：`.venv\Scripts\python.exe -m pytest tests\test_search.py tests\test_server.py -q`。
- 2026-08-11：评估确认双 Notebook fixture 可以程序化构建，并能自动判定成功路径；现有 runner 受单 Notebook lease/orchestrator 限制，尚不能直接承载。TODO 008 保持“进行中”，下一步为开发具名 `search-all-open-notebooks` scenario 及最小 role-aware bundle lifecycle；在该真实场景通过前，不宣称空 `start_id` 与 Desktop `Ctrl+E` 完全等价。
