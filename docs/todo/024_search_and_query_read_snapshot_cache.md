# 024：Search 与 Typed Query 短时只读快照缓存

> ID：024
> 状态：待办
> 优先级：P2
> 类型：只读性能 / Search 与 Query 契约 / Manual Validation
> 更新日期：2026-08-13

## 决策摘要

OneNote Desktop 通常不是高频修改的数据源，而一次公开 Search 或 hierarchy metadata Query 当前需要启动 PowerShell bridge 并调用 OneNote COM。为连续查询、Agent 重试和分页重复读取增加一个轻量的进程内缓存，可以减少 `FindPages` 与 `GetHierarchy` 调用；但缓存不得进入 mutation 的确认、执行后 read-back 或收敛重试路径，也不得演变为磁盘索引、后台同步器或复杂的一致性协议。

本 TODO 采用默认 15 秒、可通过 `LOCAL_ONENOTE_READ_CACHE_TTL_SECONDS` 配置的进程内只读快照缓存。缓存只服务 `search_pages` 和 TODO 022 规划的四个 typed metadata Query，不提供公开 `refresh` 参数。由本 MCP 进程发起的潜在 OneNote mutation 在 COM 调用前立即使缓存失效；用户直接在 OneNote Desktop 中造成的外部修改通过当前配置的 TTL 收敛，因此 Agent 可见合同必须明确当前最大陈旧窗口。

该设计只优化调用成本，不改变 local-only、index-only Search、精确 ID、open-only、回收站、候选预算或 fail-closed 边界。TODO 022 的 typed Query 工具迁移仍由其自身跟踪；本 TODO 不提前实施或重新定义那组公开工具。

## 缓存边界

### 生命周期与容量

- 缓存属于当前 MCP server 进程，重启后自然清空，不写磁盘、不跨进程共享，也不读取或直接操作 `.one` 文件；
- 使用 `time.monotonic()` 计算 TTL，不启动 timer、watcher、轮询线程或后台 COM 调用；
- `LOCAL_ONENOTE_READ_CACHE_TTL_SECONDS` 默认 `15`，只接受 `1..300` 的整数秒，并在进程启动时读取一次；缺失时使用默认值，空值、非整数或越界值必须使配置初始化明确失败，不得静默夹取、回退默认值或接受无限 TTL；
- 使用小型、有界 LRU，并同时限制条目数量、缓存总字节数和单条目字节数；具体默认值在实施时作为进程级设置固定并进入自动化合同，不因超限而截断真实结果；
- 超过单条目或总容量的结果仍按正常只读调用返回，但不得进入缓存；只缓存成功、完成 XML 解析、scope 证明和预算检查的结果，异常、畸形 XML、归属不明或预算拒绝均不缓存；
- 缓存值以不可变序列化形式保存，命中后重新构造返回对象，防止 snippet、错误标记或调用方修改污染后续结果。

### Hierarchy 快照

Hierarchy cache key 固定包含：

```text
(start_id, hierarchy_scope, schema)
```

- 只缓存原始 `GetHierarchy` XML；名称、标题、时间、父级、回收站和 `limit` 等 metadata filter 每次调用时重新执行；
- TODO 022 的 root/start-node、`hsNotebooks`/`hsSections`/`hsPages` 必须自然映射到不同 key，不得退化为统一 root/`hsPages` 大快照；
- `search_pages` 可共享其 scope 证明和结果补全需要的 hierarchy snapshot；
- 应提供显式的 cached read API，而不是透明缓存现有 `HierarchyService.resources()`：精确 `get_*`、`list_*`、identifier resolution、mutation confirmation、`wait_for*`、冲突检查和 mutation 后 read-back 默认继续读取 live hierarchy；
- 同一个公开调用需要 catalog 与 fragment 对齐时必须使用同一次 snapshot，不得在中途混用不同 generation。

### FindPages 候选快照

`FindPages` cache key 固定包含：

```text
(
  sha256(exact_query_utf8),
  scope_mode,
  exact_start_node_id,
  include_recycle_bin,
  include_unindexed=false,
  display=false,
  schema
)
```

- 不把 raw query 写入 cache metadata、日志、health check 或 audit；hash 只用于进程内 key；
- `offset`、`page_size` 和 `include_snippets` 不进入 key。首次 miss 完成精确 scope 验证、一次 `FindPages`、候选过滤、去重、完整候选预算检查并保存 Page metadata 与原始顺序；
- 后续分页从同一候选快照切片，因此 TTL 内候选数量和顺序稳定；TTL 到期或失效后重新执行 `FindPages`，不承诺跨该边界的 offset 分页冻结；
- snippet、Page XML 和正文永不进入本缓存。`include_snippets=true` 时仍只读取当前分页中的 live Page 内容，因此 snippet 可以比候选快照更新；
- 每次命中后重新应用当前 `SearchBudget`，不能通过旧缓存绕过候选数量、字符或总时间限制；cache lookup 和解析时间计入当前调用的总预算。

## 一致性与失效

- 由 MCP 发起的任何可能改变 hierarchy、Page metadata、Page 正文、Notebook open state 或 index 可见性的 bridge operation，必须在调用 COM **之前**清空 hierarchy 和 FindPages 两个 namespace；即使 COM 随后失败，也不得恢复旧缓存；
- 至少覆盖 `open_hierarchy`、`update_hierarchy`、`delete_hierarchy`、`close_notebook`、`create_new_page`、`update_page_content`、`delete_page_content`、`sync_hierarchy`、`merge_sections` 和 `set_filing_location`，并以固定 operation allowlist/分类测试防止新 mutation 忘记接入；
- 使用 generation/token 检查防止并发中的旧只读请求在 mutation 失效后重新写回缓存；失效前开始、失效后完成的结果只能返回给原调用，不得 publish 到当前 generation；
- MCP mutation 的确认读取仍为 live；在 COM 前失效后，后续 verification/read-back 也必须绕过 cache，避免旧快照破坏 `wait_for_created`、删除确认或失败收敛；
- OneNote Desktop 中由用户、插件或其他进程直接造成的修改没有可靠的主动通知，本方案不尝试伪造强一致性，最坏陈旧窗口就是当前配置的 TTL；
- 不增加 `refresh=true`、cache ID、cache control Tool 或手工 clear 参数。TTL 环境变量是首版唯一的 cache control，运行中修改环境不会改变当前进程，需重启 MCP server 后生效。

## Agent 可见公开合同

- `search_pages.pagination_consistency` 从当前 `live_index` 调整为一个明确的 bounded-TTL snapshot 值；最终字符串在实施时固定，并同步 schema、测试、README 与 design 文档；
- `search_pages` description 必须说明：候选结果最多陈旧一个当前配置的 TTL、默认 15 秒且由 `LOCAL_ONENOTE_READ_CACHE_TTL_SECONDS` 控制、同一有效快照内分页顺序稳定、MCP mutation 会立即失效、Desktop 外部修改在 TTL 后可见、snippet 仍是当前页 live read；
- 四个 typed metadata Query description 必须说明 hierarchy metadata 可能最多陈旧一个当前配置的 TTL，指出同一环境变量及默认值，并继续区分 Page metadata Query 与 Page 正文 Search；
- `health_check` 增加 content-free 的 `read_cache` capability，至少报告 `storage="process_memory"`、当前实际 `ttl_seconds`、`ttl_environment_variable="LOCAL_ONENOTE_READ_CACHE_TTL_SECONDS"`、允许范围、适用的公开只读工具类别、mutation invalidation 和“不提供强制刷新”；不得返回 raw query、query hash、OneNote 内容、对象 ID、缓存条目或命中明细；
- mutation Tool description 不需要暴露实现细节，但当前 design 文档必须明确 confirmation/read-back 不使用该缓存；
- 同步根 README、`docs/design/architecture.md`、`docs/design/tool_contracts.md`、Search TODO 008、typed Query TODO 022 和受影响的 manual-validation 文档，删除“每页必定重新执行一次 `FindPages`”等过期说明。

## 自动化合同

至少覆盖：

- 相同 hierarchy key 在配置的 TTL 内共享，`start_id`、scope 或 schema 不同则隔离；使用可控 monotonic clock 验证默认值和自定义 TTL 的边界前命中、边界后 miss；
- 相同 Search key 跨 offset、page size 和 snippet 选项只执行一次 `FindPages`，query、scope、start node 或回收站选项变化时 miss；
- Search 候选顺序、`total_matches`、分页和 candidate budget 在 cache hit/miss 上一致；snippet 不缓存且只 hydration 当前页；
- 空成功结果可以缓存；bridge 失败、XML 失败、scope/归属失败、预算失败和超大结果不缓存；
- 返回对象或 snippet 修改不污染后续命中；缓存和 audit 不保存 raw query 或 Page 内容；
- 任一列入分类的 mutation 在 COM 前失效，失败 mutation 仍失效；generation 防止并发旧请求重新 publish；
- mutation confirmation、`wait_for*`、partial failure verification 和 read-back 始终使用 live hierarchy；
- cache hit 仍重新检查当前 SearchBudget，过期/解析时间仍计入总时间预算；
- TTL 环境变量缺失时为 15，合法边界 `1`/`300` 和区间内自定义值精确生效；空值、非整数、`0`、负数及大于 `300` 均在启动配置阶段明确失败；
- Tool schema 不出现 `refresh`，Tool description、health capability 和新的 pagination consistency 值完整可见；
- TODO 022 的四种最浅 hierarchy scope 在 key 中保持隔离，不因缓存回退为 root/`hsPages`。

先运行新增 cache、search、hierarchy 和 server 的聚焦纯测试；共享行为稳定后运行完整：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## 真实后端验证场景

新增具名 human-gated 场景 `read-cache-coherence`，放在 `tests/manual_validation/`，使用场景本次运行创建的 fresh-only disposable Notebook 和 run-unique Page 内容。场景默认 `included_in_all=false`，禁止 fixture cache，使用静态最小权限和 content-free bridge audit；Agent 只能运行纯测试及：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py read-cache-coherence --dry-run --json
```

真实的 `run.py read-cache-coherence` 只能由用户在交互式前台显式启动。场景必须保存请求、精确 ID、时间点、content-free bridge operation 计数、返回摘要及独立 live evidence，并至少验证：

1. 对同一 typed metadata Query 连续调用，首次产生预期 `GetHierarchy`，TTL 内第二次为 cache hit；不同原生 scope/start node 不错误共享；
2. 对同一 `search_pages` query/scope 请求两页，TTL 内只有首次产生 `FindPages` audit，第二页复用同一候选顺序且只 hydration 自己的 snippet Page；
3. 不同 query 或精确 start node 产生独立 `FindPages`，证明 key 不会跨内容或 scope 泄漏；
4. 场景启动子 MCP 时显式设置一个较短但合法的 TTL（建议 `LOCAL_ONENOTE_READ_CACHE_TTL_SECONDS=2`），确认 `health_check.read_cache.ttl_seconds` 与进程环境一致；在无 MCP mutation 的情况下等待超过该 TTL，下一次 Query 与 Search 分别产生新的 `GetHierarchy`/`FindPages` audit；时间证据必须使用有界容差，不以脆弱的精确毫秒断言判定；
5. 通过现有最小写权限 Tool 更新 disposable Page 后，不等待 TTL 即再次 Search/Query，证明 MCP mutation 已立即失效并产生新的 COM 调用；正文更新还应验证新 Search query 可见，但不得依赖旧索引立即消失；
6. 执行一个可安全构造的、在 COM 层失败或被 read-back 判定失败的 disposable mutation 分支，证明失败后下一次只读调用不会命中 mutation 前旧缓存；若无法跨受支持 OneNote 版本稳定构造，该项必须保留为自动化合同并在真实报告中明确记为未验证，而不能伪造通过；
7. Agent 可见 description 与 `health_check.read_cache` 精确声明环境变量、默认值、当前实际 TTL、进程内、mutation 失效和无强制刷新，并且 audit/evidence 不包含 raw query、Page 正文或 snippet；
8. 场景结束时遵循现有 lifecycle 保留与关闭规则，不永久删除 working Notebook、失败现场或用户数据。

真实验证证据必须由用户确认。Mock、完整 pytest、`--dry-run` 或 Agent 推断都不能单独满足本 TODO 的真实场景完成门。

## 非目标

- 不建立磁盘缓存、SQLite/全文索引、文件 watcher、OneNote 事件订阅、后台刷新或跨进程 cache daemon；
- 不缓存 Page XML、正文、snippet、binary content、mutation confirmation 或 read-back；
- 不直接编辑 `.one` 文件，不扫描已关闭 Notebook、备份或本地目录，不引入 Graph、Azure、OAuth、遥测或远程内容处理；
- 不为了提高命中率放宽精确 ID、open-only、回收站、scope、候选预算或 raw XML policy；
- 不在本 TODO 中实施 TODO 022 的工具替换，也不保留 `query_hierarchy` 兼容 alias；
- 不承诺用户在 OneNote Desktop 外部修改后的即时一致性。

## 完成定义

- 进程内可配置 TTL（默认 15 秒、合法范围 `1..300`）、有界容量、不可变值、generation 防旧值回填和 mutation-before-COM 失效全部实现；
- Search 和 typed metadata Query 使用显式 cached snapshot API，一致性敏感读路径经测试证明保持 live；
- Search 分页合同、Tool description、`health_check`、README、design、TODO 008/022 与 manual-validation 文档同步且不存在旧 `live_index` 叙述；
- 聚焦纯测试与完整 pytest 通过，`read-cache-coherence --dry-run --json` 为零副作用成功；
- 用户显式运行 `read-cache-coherence`，并确认自定义 TTL 被 health 与真实过期行为采用，以及 cold/hit、key 隔离、分页复用、MCP mutation 立即失效、snippet live read、脱敏和 lifecycle 证据满足场景断言；
- 真实证据未闭合前，本 TODO 不得标记为“已完成”。

## 关联

- [TODO 008](008_all_open_notebooks_search_scope.md)：当前 index-only Search、候选预算和 live pagination 合同；实施本 TODO 时需要协调更新。
- [TODO 022](022_typed_metadata_query_tools_and_native_scopes.md)：typed metadata Query 的公开工具、原生 scope 和最浅 `GetHierarchy` 合同。
- [当前架构](../design/architecture.md)：实现后缓存职责与 mutation live-read 边界的 canonical 归属。
- [公开 Tool 契约](../design/tool_contracts.md)：实现后 Agent 可见 description、响应和 pagination consistency 的 canonical 归属。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：真实场景的人机权限、证据和生命周期规则。
