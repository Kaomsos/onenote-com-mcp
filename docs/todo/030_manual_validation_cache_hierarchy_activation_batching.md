# 030：Manual Validation Cache 层级激活批处理与证据复用

> ID：030
> 状态：已完成
> 优先级：P2
> 类型：Manual Validation 性能 / Fixture Cache 激活
> 更新日期：2026-08-14

## 背景

Fixture cache 的 opaque byte copy 通常只需约 `0.1s`，cache hit 的主要耗时并不在文件复制，而在 OneNote 将新的 run-local working path 重新导入为独立 Notebook、重建 live ID 并逐步加载 hierarchy。优化前的 lifecycle 为证明 SectionGroup/Section 已在正确 parent 下激活，会逐对象调用 `OpenHierarchy`、global/exact-self `GetHierarchy` 和 parent 回读；每个 bridge 调用又会启动新的 PowerShell 进程并创建新的 `OneNote.Application` COM 对象。复杂 fixture 因而可能产生数十次乃至约 90 次 hierarchy 调用，放大 OneNote 的异步加载窗口和进程初始化成本。

TODO 016 已在完整 Page 内容取证前增加 manifest-aware 双稳定门，并消除同一 Page XML 的重复读取，但它不优化更早的 Notebook lifecycle 激活。本 TODO 专门降低 cache materialization 的 hierarchy 打开和证明成本，同时保持每次 run 的新 working copy、live ID 重绑定和 fail-closed 安全边界。

## 当前进展与最新证据（2026-08-14）

> 完成确认：用户完成的稳定对照确认，先前大面积 fixture failure 与 scenario 开始前 OneNote Desktop GUI 未启动强相关，而不是由 `CloseNotebook(false)` 动作或缺少 persistence checkpoint 导致。加入 check-only GUI preflight 后，当前实现保留 exact-parent batch、typed relative-address rebind、两次 hierarchy 稳定、每 Page 单次完整读取、scenario-before snapshot handoff、failure isolation 与 cache origin metrics；移除 materialized import/close/reopen、fresh persistence checkpoint、activation close/reopen recovery 及其 evidence。当前版本的完整 `all --use-cache` 已由用户确认 `15 passed, 0 failed`。

最终单项回归 `run-2026-08-14-14-33-13` 以 `decision/cache_origin=validated_hit` 完成 `reparent-page-with-level`：cache opaque copy/verify 为 `0.005947s`、materialization 总计 `0.010411s`，声明层级在 `1.345978s` 内连续稳定两次，唯一一次 `scenario before` 完整取证为 `6.140156s` 并被 mutation 单次消费；root-only 与 full-subtree 两个 case 均通过，template 未打开且 inventory unchanged，working Notebook 最终为 `closed_preserved`。多 role 证据 `run-2026-08-14-11-26-12` 的 destination/source 也分别连续稳定两次，唯一 before snapshots 被消费，`copy-section-group`、template immutability 与双 Notebook 关闭全部通过。

范围在完成时收敛为已经证明有效的机制。未继续实现“把 lifecycle 最后一次轻量 hierarchy observation 充当 Fixture 双稳定第一次观察”：它最多节省一次轻量 `get_tree`，却会增加跨阶段身份、签名与时序耦合；当前保留两次独立 hierarchy observation。也未增加持续缺失 Page 时自动 reactivation owning Section：GUI preflight 已消除此前的共性触发条件，自动重激活会扩大状态机和误归因面；当前有界观察耗尽后继续 fail closed。两项均作为不采用的探索方案保留在下文历史设计中，不属于最终完成范围。

以下 2/15、12/15 与 checkpoint 记录保留为排障历史，不再代表当前推荐流程；它们说明中间方案为何一度显得有效，也说明未控制 GUI 初始状态时不能作因果判断。

用户启动的首轮 `all --use-cache` 证明问题不在 opaque copy 本身，而集中在 working Notebook 激活边界：

- `run-2026-08-14-00-29-31` 在打开 Group-B 后，尚未校验的同级 `99-Section-Anchor-B.one` 已被 OneNote 接管，旧 Python loop 再次 `resolve(strict=True)` 时把路径变化误判为 fixture 损坏；
- `run-2026-08-14-00-30-14`、`00-34-11`、`00-36-26` 在逐对象、多 PowerShell/COM session 激活中耗尽一次 close/reopen；`00-38-40` 在 destination 双稳定门耗尽，但 failure finalizer 均精确关闭 lease，异常没有跨 child 扩散；
- `run-2026-08-14-00-32-39` 的五个声明对象已出现，只有嵌套 `group_page` 在 16 次 hierarchy observation 中持续缺失，证明“Page 永远只等观察、不重新激活 owning Section”不能作为无条件规则；
- `run-2026-08-14-00-18-56` 还暴露 Delete recipe 的空目标 SectionGroup 没有形成可持久化物理子树；这不是 activation timeout，应通过 recipe 形状修复。
- 首版 batch 后的 `run-2026-08-14-01-33-41`、`01-34-10`、`01-34-35` 与 `01-35-21` 出现新回归：逐项 `OpenHierarchy` 已返回稳定 ID，但同一 COM session 末尾的立即 snapshot 尚未显示 child，lifecycle 连续执行两个无等待 batch 后在 Fixture 双稳定门之前误判失败；`01-33-54` 还证明末尾 `GetHierarchy` 的 `0x800706BA` 会让旧 bridge 丢弃已完成的逐项结果。相邻 `01-34-47` 的 rename 完整通过，支持“snapshot 可见性时序回归、不是 cache template 损坏”的判断。`01-34-22` 与 `01-35-34` 则在更早的全局开放 Notebook snapshot 失败，属于独立的只读 COM 可用性问题。
- 修正立即误判后的完整 `all --use-cache`（`02-06-49` 至 `02-20-02`）进一步证明：15 个场景仅 `rename` 与 `reparent-section-group` 通过，其余 13 个均在 mutation 前完成一次 batch 后、后续 16 次独立 hierarchy observation 中保持相同缺失集合；包括刚完成 cold build、关闭发布并立即 materialize 的 `delete`、`copy-notebook` 与 `move-section-group`。全部失败 lease 都精确关闭且批处理继续到底。这排除了旧 cache 污染和猜测式 sleep，表明“第一次 identity 导入”与“跨 COM session 的 mutation-ready identity”必须通过持久化 checkpoint 分离。

已从本 TODO 提取并实现当前直接相关部分：lifecycle 在任何 child COM 调用前完成全部路径预算、containment、reparse 与 typed-parent 校验并冻结请求；新增非公开 `open_hierarchy_batch`，在一个 PowerShell/`OneNote.Application` session 中按 parent-before-child 打开精确 SectionGroup/Section，并在同一 session 末尾尝试取得一次 pages hierarchy snapshot。回归修正后，只有逐项 `OpenHierarchy` error 才最多重试该失败项一次；成功返回 ID 的容器即使暂未出现在同会话 snapshot 中也不会立即重开，而是交给既有 Fixture 双稳定 observer。末尾 `GetHierarchy` 失败现在作为 typed batch evidence 返回，不再吞掉逐项成功结果。Batch 中已可见的确定性类型、parent、path、回收站或唯一性冲突仍立即 fail closed；生产 MCP 的公开 tool/schema/response 未变化。

中间方案曾将所有 materialized working role 统一为两阶段 lifecycle：第一次 import identity 完成 exact-parent batch 后 `CloseNotebook(force=false)`，随后从同一个 working path 重开 Notebook shell。该方案一度覆盖 validated hit、programmatic cold-build publish 后 materialize、interactive cache consumer 以及单/多 role bundle，但后续 GUI 初始状态对照证明 checkpoint 不是共性修复，因此最终实现已移除该流程。

用户随后完成的新一轮 `all --use-cache` 已从上一轮的 2/15 提升到 12/15；统一 import/close/reopen checkpoint、异常隔离和继续执行合同均按预期工作。剩余三项形成了更窄且彼此独立的失败：Create cold-build 的旧 full preset 在 checkpoint 后只丢失 `Disposable-Page`；Copy SectionGroup cold-build 在发布 inventory 中缺少原本为空的 `99-Group-Anchor-B`；Move SectionGroup 已完成 verified/lossless Copy 和一次非永久源根删除，却因目标 `modified` 后台推进被完整 digest 误判为目标变化。它们不再支持“cache 全面不可用”的判断。

针对当时证据，Create recipe 曾升到 v3 并声明 publish 前 persistence checkpoint；Copy SectionGroup recipe 升到 v5，为四个空 anchor Group 增加无 Page 的 typed sentinel Section。生产 Move 的源计划重验证继续严格包含 `modified`，只把源删除后的目标复核改成排除 `modified`、仍包含完整拓扑和稳定 Page 内容 hash 的 protected semantic digest，时间戳单独漂移只产生 warning，语义变化仍为 partial failure。后续真实运行已完成这些分支的验证；Create 的 persistence checkpoint 与 sentinel Page 又在最终纠偏中移除。

阶段诊断也已细化：`cache-materialization.json` 同时记录 legacy materialize decision、真正的 `cache_origin` 以及 preflight/copy/publish 耗时；`cache-working-import-checkpoint.json` 记录 import-open/import-close/reopen 耗时；`cache-hierarchy-convergence.json` 记录逐 role hierarchy/content 耗时。这样 cold-build 后 materialize 不再伪装成普通 validated hit，后续真实 run 可直接定位慢点。

Delete recipe 同时提升到 v2，在 `Disposable-Group` 内创建 `Disposable-Section` sentinel，使目标 Group 具有持久化 `.one` 形状；旧 fingerprint 自动 miss，不要求清理合法 cache。

TODO 016 的“层级连续稳定两次后才读完整 Page 内容、每个 snapshot 同一 Page XML 只读一次”已经完成并取得真实 Copy Page 证据。持续 `missing_page` 的历史备选方案曾计划对精确 owning Section 做一次有界 reactivation；最终实现没有采用该方案，而是在 GUI preflight 后保留有界只读观察与耗尽即 fail-closed 的更小状态机。

当前进一步完成了内容证据的跨阶段复用：cache build 保持权威模板内容验证；materialized working copy 打开后先承担 exact-parent batch 与轻量 hierarchy/identity 收敛；随后唯一一次完整 `scenario before` snapshot 同时用于 materialized live Recipe 真实性复核和 scenario mutation 基线。Snapshot 按 exact role set、唯一 Notebook ID 与 SHA-256 digest 单次 handoff，scenario 的首个 `capture_snapshot()` 直接消费而不再调用 hierarchy/Page read；任一 role 尚未消费时首次 mutation 在 MCP 调用前 fail closed。终端阶段名相应为 `scenario before`，证据写入 `scenario-before-snapshot-handoff.json`。

后续历史批次 `run-2026-08-14-07-47-48` 曾显示 fresh persistence 不能把 build 后的第一次 reopen 同时当作 mutation identity：Create 的 sentinel `.one` 已持久化且 import `OpenHierarchy` 返回新 Section ID，但 Notebook snapshot 仍暂不可见。当时的实现因此临时采用 build close → import open/batch activation → exact import close → shell-only mutation reopen → typed 双稳定 → 完整内容验证；不使用 sleep、不重放 mutation。随后同一批次的双 role `copy-section-group` 在 `run-2026-08-14-07-57-45` 对 destination/source 均完成三套互异 Notebook identity、exact import close、两次稳定、内容验证和全部 ID remap，并完整通过 Copy/cleanup/close。`run-2026-08-14-08-12-05` 的 Create 也通过，但当时只证明 materialized cache consumer 路径；该 checkpoint 后来因 GUI 初始状态纠偏而移除。

同一批次继续运行到结束，`move-page`、`move-section`、`move-section-group` 均通过；唯一新增失败是 `run-2026-08-14-08-00-40` 的 `copy-notebook`。其 cache 内容与 live hierarchy 已通过，失败发生在 mutation 前：模板 manifest 的 `notebook_copy_root` 仍指向发布模板的旧 run，而 materialized manifest 只更新了 working Notebook path。当前实现改为先证明 cached `notebook_copy_root`、各 role Notebook path 与 lifecycle lease 全部属于同一旧 run，再逐字段重绑到当前 run，并写 `cache-run-local-path-remap.json`；关系不一致立即 fail closed，模板保持不变。用户复验 `run-2026-08-14-08-17-56` 已以 `validated_hit` 完整通过：remap evidence 明确绑定旧 run `03-12-04` 到当前 run，唯一 scenario-before snapshot 被消费，三个 Page 分别通过 strict/semantic/strict 保真比较，源与目标 Notebook 均精确关闭，template immutability 通过。

## 目标

在不修改生产 MCP 公开 tool/response、不打开 immutable template、不复用跨 run 可变 Notebook 的前提下，最终交付以下四项优化：

1. 在 manual-validation 内部用单次 PowerShell/COM 会话批量激活一个 role 的精确 SectionGroup/Section 路径，并在同一会话末尾读取一次 Notebook hierarchy。
2. 将激活收敛从逐对象轮询改为 Notebook 级观察：一次 snapshot 同时判断全部 manifest-bound 对象；batch 只重试上一轮失败的精确容器，成功返回 ID 的容器交给 Fixture 有界双稳定观察。
3. 将 materialized live Recipe 真实性复核产生的唯一完整 `scenario before` snapshot 按 exact role、Notebook ID 与 digest 单次交给 scenario，避免再次读取 hierarchy 和 Page 内容。
4. 根据当前 hierarchy 与 manifest 计算精确缺失集合，只对尚未出现的 SectionGroup/Section 调用 `OpenHierarchy`；Page 不逐个打开，只通过 Notebook 级 hierarchy convergence 证明就绪。

## 不可削弱的安全门

- Cache hit 仍必须 materialize 到本次 run 独有的 working path；template 只允许关闭状态下的 opaque copy，OneNote 永不打开 template。
- 批量接口只能接收 recipe/manifest 声明并已完成路径预算、root containment 和 typed parent 约束的精确路径；不得接受任意 raw XML、任意路径、名称搜索或无界扫描。
- Notebook 直属 child 与 SectionGroup 嵌套 child 继续遵守现有 absolute-path/relative-ID 组合规则，禁止 absolute path 与非空 parent ID 混用。
- `OpenHierarchy` 返回 ID、请求成功或单次 snapshot 都不能单独放行。进入 Page 内容验证前，全部声明对象仍必须按 Notebook-relative typed address 唯一存在，并连续两次保持相同的 ID、类型、parent、section、page level、parent Page 和 sibling order。
- `scenario before` snapshot handoff 必须绑定精确 role set、working Notebook ID 与 digest，并且只能消费一次；未完整消费全部 role 时 mutation 必须在 MCP 调用前 fail closed。
- 确定性类型/parent/path 冲突立即 fail closed；瞬态 COM/readiness 问题只能在有界观察窗口内重试读取和缺失容器激活，不能重放业务 mutation。
- 失败 evidence、run-local lifecycle lease、默认精确关闭和显式 keep 行为保持不变。优化不得把 working activation 失败错误归因于 immutable template 内容损坏。
- 不解析或修改 `.one`/`.onetoc2`，不引入云 API、遥测、后台 watcher、warm mutable Notebook pool 或跨 run COM session。

## 建议实现

### 1. Internal batched activation bridge

在 manual-validation lifecycle 边界增加非公开的批量调用，单次执行以下步骤：

1. 创建一个 PowerShell 进程和一个 `OneNote.Application` COM 对象；
2. 按已验证的 parent-before-child 顺序处理精确 SectionGroup/Section 请求；
3. 对每项记录 content-free 状态：requested type、parent proof、返回 ID、HRESULT/error class；
4. 在同一 COM session 中对精确 Notebook 读取一次完整 hierarchy；
5. 返回结构化、无正文的 batch evidence。

批量操作只负责打开和读取，不执行任何业务 mutation，也不扩大 scenario MCP allowlist。

### 2. Notebook-scoped convergence loop

每轮从一次 hierarchy snapshot 建立 manifest typed-address 映射，并分类：

- `present_and_valid`：类型、相对地址和 parent 均正确；
- `missing_container`：声明的 SectionGroup/Section 尚未出现，可进入下一轮精确 batch；
- `missing_page`：只等待后续有界 Notebook observation；持续缺失时 fail closed，不按 Page 名称打开、不自动重激活 Section，也不无界重试；
- `deterministic_conflict`：类型、parent、路径或唯一性冲突，立即失败。

每轮只把 `missing_container` 传给下一次 batch。全部对象出现后开始计算稳定签名，不再重复打开已经成功出现的容器。

### 3. `scenario before` snapshot handoff

Fixture runtime 在双稳定后取得唯一一次完整 `scenario before` snapshot，同时完成 cache 内容真实性复核与 mutation 基线取证，并把 run-local handoff 至少绑定到：

- working Notebook ID 与 canonical working path；
- exact role set 与各 role 的唯一 Notebook ID；
- capture 时间、对象计数与 snapshot SHA-256 digest；
- handoff 的 pending/consumed 状态。

Scenario 的第一次 before capture 只能单次消费匹配的 snapshot，不再读取 hierarchy/Page 内容。role、Notebook ID、digest 或消费状态不匹配时立即拒绝；全部 role 未消费完成时不得执行 mutation。

### 4. Evidence 与 progress

将 Notebook 阶段耗时拆分并持久化：

- `cache_copy_seconds`；
- `notebook_shell_open_seconds`；
- `hierarchy_batch_activation_seconds`；
- `hierarchy_observation_seconds`；
- batch 数、`OpenHierarchy` 请求数、snapshot 数和 PowerShell/COM session 数；
- scenario 是否消费 snapshot handoff、避免的重复 hierarchy/Page 读取以及最终双稳定结果。

终端 progress 保持 content-free，至少区分 `cache open`、`cache hierarchy` 和 `scenario before`，长 batch 必须有有界 heartbeat，不能让用户误以为 runner 卡死。

## 自动化验证

- Batch 输入只能来自已验证的精确 managed working path 和 typed parent；任意路径、template path、absolute+parent 混用、重复 role 或越界路径均在启动 PowerShell 前拒绝。
- 多个 SectionGroup/Section 在一次 batch 中只创建一个 PowerShell/COM session；调用次数测试冻结该上界。
- 同会话 snapshot 暂不可见或末尾 hierarchy read 出现 typed transient error 时，成功返回 ID 的容器不被重复激活，而是交给 Fixture 的独立双稳定门；逐项 activation error 最多只重试失败集合一次。
- Notebook 级 observer 用一次 snapshot 同时识别全部 present/missing/conflict 项，不再为每个对象重新读取 global hierarchy。
- 已出现或已成功返回 ID 的容器不会在后续 batch 再次 `OpenHierarchy`；Page 缺失只触发 hierarchy observation，不触发 Page/Section 打开。
- 缺失 Page 后出现并连续两次稳定可以通过；ID/parent/order 震荡、回退、歧义或确定性冲突必须失败。
- 有效 `scenario before` handoff 必须按 role、Notebook ID 与 digest 单次消费；任一绑定不匹配时拒绝，且不得回退到 mutation 后补证。
- 完整 Page 内容读取只在双稳定完成后发生；失败路径证明业务 mutation 调用次数为零。
- 单 role、多 role、cold-build 重新 materialize 和 validated-hit 路径均覆盖；现有默认关闭、keep 模式与 failure finalization 合同不变。
- 运行 manual-validation 纯测试、完整 `pytest -q`、相关 `--use-cache --dry-run --json` 和 `git diff --check`。

## 真实验证与度量（已完成）

真实 scenario 均由用户在交互式前台启动。单 role 最终证据为 `run-2026-08-14-14-33-13`，多 role 证据为 `run-2026-08-14-11-26-12`；当前版本完整 `all --use-cache` 从 `run-2026-08-14-11-17-56` 至 `run-2026-08-14-11-29-54`，结果为 `15 passed, 0 failed`。这些 run 覆盖 `validated_hit`、old→live ID/path remap、双稳定、单次完整 snapshot handoff、mutation/restore 或显式非恢复结果、template inventory unchanged 与默认精确关闭。

优化前复杂 fixture 曾产生数十次乃至约 90 次 hierarchy 调用；最终 batch 将一个 role 的 exact-parent `OpenHierarchy` 合并到单 PowerShell/COM session，并只对失败集合做至多一次重试。耗时仍受 OneNote 状态影响，因此不设置牺牲正确性的硬阈值；最新单 role 证据明确区分了约 `0.01s` 的 cache materialization、`1.35s` 的轻量 hierarchy 双稳定与 `6.14s` 的唯一完整内容取证，证明剩余主要成本已不在 opaque copy 或重复 Page 读取。

## 非目标

- 不改变生产 MCP bridge 的公开调用模型或 tool schema；若实现最终需要生产内部复用，必须另行审查并同步设计合同。
- 不把 cache 改成长期打开的 warm Notebook，也不跨 run 共享 mutable working state。
- 不通过放宽 timeout、增加无界 retry、跳过 live validation或接受裸 `OpenHierarchy` ID 制造表面成功。
- 不在本 TODO 中决定哪些小型 recipe 应标记为 `fresh_preferred`；cache 适用性策略另行评估。
- 不优化完整 Page evidence 的单次 XML 复用；该范围由 TODO 016 跟踪。

## 依赖与关联

- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：不可变模板、working bundle、ID 重绑定与 cache lifecycle 的已完成基础合同。
- [TODO 016](016_copy_page_manual_validation_read_evidence_efficiency.md)：完整内容取证的单次 Page XML 复用与真实性能度量。
- [TODO 026](026_manual_validation_progress_verbosity.md)：content-free 实时 progress 与 `all` 子进程输出合同。
- [`cached_fixture_operation_validation.md`](../dev/cached_fixture_operation_validation.md)：当前 cache materialization 和 live validation 操作说明。
- [`fixture_cache_consumer_materialization_and_live_validation.md`](../lesson/fixture_cache_consumer_materialization_and_live_validation.md)：OneNote working copy 激活、live identity 与失败归因的真实观察边界。

## 完成定义

- 四项最终交付目标全部实现，单 role 与多 role cache materialization 共用 Notebook 级批量激活/观察机制。
- 自动化证明每个 batch 至多一个 PowerShell/COM session、只重试失败容器、Page 不被逐个打开、完整 snapshot handoff 不削弱双稳定门。
- 完整纯测试、dry-run、文档和差异检查通过，生产公开 tool/response 未意外变化。
- 用户真实运行至少一个单 role、一个多 role及后续 `all --use-cache`，确认业务验证和失败隔离仍正确。
- 真实 evidence 记录优化前后阶段耗时与调用分类，并证明 cache template 未打开、未修改，working bundle lifecycle 正常收尾。
