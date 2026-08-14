# 030：Manual Validation Cache 层级激活批处理与证据复用

> ID：030
> 状态：进行中
> 优先级：P2
> 类型：Manual Validation 性能 / Fixture Cache 激活
> 更新日期：2026-08-14

## 背景

Fixture cache 的 opaque byte copy 通常只需约 `0.1s`，cache hit 的主要耗时并不在文件复制，而在 OneNote 将新的 run-local working path 重新导入为独立 Notebook、重建 live ID 并逐步加载 hierarchy。当前 lifecycle 为证明 SectionGroup/Section 已在正确 parent 下激活，会逐对象调用 `OpenHierarchy`、global/exact-self `GetHierarchy` 和 parent 回读；每个 bridge 调用又会启动新的 PowerShell 进程并创建新的 `OneNote.Application` COM 对象。复杂 fixture 因而可能产生数十次乃至约 90 次 hierarchy 调用，放大 OneNote 的异步加载窗口和进程初始化成本。

TODO 016 已在完整 Page 内容取证前增加 manifest-aware 双稳定门，并消除同一 Page XML 的重复读取，但它不优化更早的 Notebook lifecycle 激活。本 TODO 专门降低 cache materialization 的 hierarchy 打开和证明成本，同时保持每次 run 的新 working copy、live ID 重绑定和 fail-closed 安全边界。

## 当前进展与最新证据（2026-08-14）

用户启动的首轮 `all --use-cache` 证明问题不在 opaque copy 本身，而集中在 working Notebook 激活边界：

- `run-2026-08-14-00-29-31` 在打开 Group-B 后，尚未校验的同级 `99-Section-Anchor-B.one` 已被 OneNote 接管，旧 Python loop 再次 `resolve(strict=True)` 时把路径变化误判为 fixture 损坏；
- `run-2026-08-14-00-30-14`、`00-34-11`、`00-36-26` 在逐对象、多 PowerShell/COM session 激活中耗尽一次 close/reopen；`00-38-40` 在 destination 双稳定门耗尽，但 failure finalizer 均精确关闭 lease，异常没有跨 child 扩散；
- `run-2026-08-14-00-32-39` 的五个声明对象已出现，只有嵌套 `group_page` 在 16 次 hierarchy observation 中持续缺失，证明“Page 永远只等观察、不重新激活 owning Section”不能作为无条件规则；
- `run-2026-08-14-00-18-56` 还暴露 Delete recipe 的空目标 SectionGroup 没有形成可持久化物理子树；这不是 activation timeout，应通过 recipe 形状修复。
- 首版 batch 后的 `run-2026-08-14-01-33-41`、`01-34-10`、`01-34-35` 与 `01-35-21` 出现新回归：逐项 `OpenHierarchy` 已返回稳定 ID，但同一 COM session 末尾的立即 snapshot 尚未显示 child，lifecycle 连续执行两个无等待 batch 后在 Fixture 双稳定门之前误判失败；`01-33-54` 还证明末尾 `GetHierarchy` 的 `0x800706BA` 会让旧 bridge 丢弃已完成的逐项结果。相邻 `01-34-47` 的 rename 完整通过，支持“snapshot 可见性时序回归、不是 cache template 损坏”的判断。`01-34-22` 与 `01-35-34` 则在更早的全局开放 Notebook snapshot 失败，属于独立的只读 COM 可用性问题。
- 修正立即误判后的完整 `all --use-cache`（`02-06-49` 至 `02-20-02`）进一步证明：15 个场景仅 `rename` 与 `reparent-section-group` 通过，其余 13 个均在 mutation 前完成一次 batch 后、后续 16 次独立 hierarchy observation 中保持相同缺失集合；包括刚完成 cold build、关闭发布并立即 materialize 的 `delete`、`copy-notebook` 与 `move-section-group`。全部失败 lease 都精确关闭且批处理继续到底。这排除了旧 cache 污染和猜测式 sleep，表明“第一次 identity 导入”与“跨 COM session 的 mutation-ready identity”必须通过持久化 checkpoint 分离。

已从本 TODO 提取并实现当前直接相关部分：lifecycle 在任何 child COM 调用前完成全部路径预算、containment、reparse 与 typed-parent 校验并冻结请求；新增非公开 `open_hierarchy_batch`，在一个 PowerShell/`OneNote.Application` session 中按 parent-before-child 打开精确 SectionGroup/Section，并在同一 session 末尾尝试取得一次 pages hierarchy snapshot。回归修正后，只有逐项 `OpenHierarchy` error 才最多重试该失败项一次；成功返回 ID 的容器即使暂未出现在同会话 snapshot 中也不会立即重开，而是交给既有 Fixture 双稳定 observer。末尾 `GetHierarchy` 失败现在作为 typed batch evidence 返回，不再吞掉逐项成功结果。Batch 中已可见的确定性类型、parent、path、回收站或唯一性冲突仍立即 fail closed；生产 MCP 的公开 tool/schema/response 未变化。

当前又将所有 materialized working role 统一为两阶段 lifecycle：第一次 import identity 完成 exact-parent batch 后必须 `CloseNotebook(force=false)` 并证明 exact ID/path 已关闭；随后从同一个 working path 只重开 Notebook shell，第二次 identity 才进入完整 hierarchy 枚举、typed relative-address ID 重绑、连续两次稳定与每 Page 单次内容验证。该实现覆盖 validated hit、programmatic cold-build publish 后 materialize、interactive cache consumer 以及单/多 role bundle；不提升 cache schema/fingerprint，因为 template bytes/identity 未改变，只改变每个 run 的 working lifecycle。真实复验尚未完成，因此 TODO 保持“进行中”。

用户随后完成的新一轮 `all --use-cache` 已从上一轮的 2/15 提升到 12/15；统一 import/close/reopen checkpoint、异常隔离和继续执行合同均按预期工作。剩余三项形成了更窄且彼此独立的失败：Create cold-build 的旧 full preset 在 checkpoint 后只丢失 `Disposable-Page`；Copy SectionGroup cold-build 在发布 inventory 中缺少原本为空的 `99-Group-Anchor-B`；Move SectionGroup 已完成 verified/lossless Copy 和一次非永久源根删除，却因目标 `modified` 后台推进被完整 digest 误判为目标变化。它们不再支持“cache 全面不可用”的判断。

针对该证据，Create recipe 已升到 v3，只保留业务真正需要的 `Duplicate-Title-Target` Section并声明 publish 前 persistence checkpoint；Copy SectionGroup recipe 升到 v5，四个空 anchor Group 各增加一个无 Page 的 typed sentinel Section并声明同一 checkpoint；旧 fingerprint 自动 miss。生产 Move 的源计划重验证继续严格包含 `modified`，只把源删除后的目标复核改成排除 `modified`、仍包含完整拓扑和稳定 Page 内容 hash 的 protected semantic digest，时间戳单独漂移只产生 warning，语义变化仍为 partial failure。三项均已有纯合同覆盖，尚待下一次用户真实运行。

阶段诊断也已细化：`cache-materialization.json` 同时记录 legacy materialize decision、真正的 `cache_origin` 以及 preflight/copy/publish 耗时；`cache-working-import-checkpoint.json` 记录 import-open/import-close/reopen 耗时；`cache-hierarchy-convergence.json` 记录逐 role hierarchy/content 耗时。这样 cold-build 后 materialize 不再伪装成普通 validated hit，后续真实 run 可直接定位慢点。

Delete recipe 同时提升到 v2，在 `Disposable-Group` 内创建 `Disposable-Section` sentinel，使目标 Group 具有持久化 `.one` 形状；旧 fingerprint 自动 miss，不要求清理合法 cache。

TODO 016 的“层级连续稳定两次后才读完整 Page 内容、每个 snapshot 同一 Page XML 只读一次”已经完成并取得真实 Copy Page 证据。本 TODO 已完成当前三层关键耗时指标，但尚未完成跨阶段 observation handoff 和上述三项修复后的真实复验，因此保持“进行中”。如果后续仍出现持续 `missing_page`，下一步只对其精确 owning Section 做一次有界 reactivation并重新取得 Notebook snapshot；仍缺失则 fail closed，不能放宽结构双稳定门。

当前进一步完成了内容证据的跨阶段复用：cache build 保持权威模板内容验证；working copy import/close/reopen 只承担轻量 hierarchy/identity checkpoint；reopen 后的唯一一次完整 `scenario before` snapshot 同时用于 materialized live Recipe 真实性复核和 scenario mutation 基线。Snapshot 按 exact role set、唯一 Notebook ID 与 SHA-256 digest 单次 handoff，scenario 的首个 `capture_snapshot()` 直接消费而不再调用 hierarchy/Page read；任一 role 尚未消费时首次 mutation 在 MCP 调用前 fail closed。终端阶段名相应改为 `scenario before`，证据写入 `scenario-before-snapshot-handoff.json`。这消除了当前最明显的一整轮重复 Page 与 hierarchy 读取，但不等同于本 TODO 目标 3 所述的轻量 hierarchy observation handoff，后者仍待实现与真实量化。

最新 `all --use-cache` 的 `run-2026-08-14-07-47-48` 又证明 fresh persistence 不能把 build 后的第一次 reopen 同时当作 mutation identity：Create 的 sentinel `.one` 已持久化且 import `OpenHierarchy` 返回新 Section ID，但 Notebook snapshot 仍暂不可见。Fresh persistence 因此也统一为 build close → import open/batch activation → exact import close → shell-only mutation reopen → typed 双稳定 → 完整内容验证；不使用 sleep、不重放 mutation。随后同一批次的双 role `copy-section-group` 在 `run-2026-08-14-07-57-45` 对 destination/source 均完成三套互异 Notebook identity、exact import close、两次稳定、内容验证和全部 ID remap，并完整通过 Copy/cleanup/close。`run-2026-08-14-08-12-05` 的 Create 也通过，但其 `cache_origin=validated_hit` 且没有 fresh persistence 三份 evidence，因此只证明 materialized cache consumer 路径；Create fresh/cold-build checkpoint 仍需用户用不带 `--use-cache` 的单项运行复验。

同一批次继续运行到结束，`move-page`、`move-section`、`move-section-group` 均通过；唯一新增失败是 `run-2026-08-14-08-00-40` 的 `copy-notebook`。其 cache 内容与 live hierarchy 已通过，失败发生在 mutation 前：模板 manifest 的 `notebook_copy_root` 仍指向发布模板的旧 run，而 materialized manifest 只更新了 working Notebook path。当前实现改为先证明 cached `notebook_copy_root`、各 role Notebook path 与 lifecycle lease 全部属于同一旧 run，再逐字段重绑到当前 run，并写 `cache-run-local-path-remap.json`；关系不一致立即 fail closed，模板保持不变。用户复验 `run-2026-08-14-08-17-56` 已以 `validated_hit` 完整通过：remap evidence 明确绑定旧 run `03-12-04` 到当前 run，唯一 scenario-before snapshot 被消费，三个 Page 分别通过 strict/semantic/strict 保真比较，源与目标 Notebook 均精确关闭，template immutability 通过。

## 目标

在不修改生产 MCP 公开 tool/response、不打开 immutable template、不复用跨 run 可变 Notebook 的前提下，实现以下四项优化：

1. 在 manual-validation 内部用单次 PowerShell/COM 会话批量激活一个 role 的精确 SectionGroup/Section 路径，并在同一会话末尾读取一次 Notebook hierarchy。
2. 将激活收敛从逐对象轮询改为 Notebook 级观察：一次 snapshot 同时判断全部 manifest-bound 对象，只对仍缺失的物理容器继续执行有界激活。
3. 将 Notebook 阶段最后一次完整 hierarchy observation 传给 Fixture 阶段，作为双稳定门的第一次候选观察，避免跨阶段无条件重读；第二次观察仍必须独立取得且签名一致。
4. 根据当前 hierarchy 与 manifest 计算精确缺失集合，只对尚未出现的 SectionGroup/Section 调用 `OpenHierarchy`；Page 不逐个打开，只通过 Notebook 级 hierarchy convergence 证明就绪。

## 不可削弱的安全门

- Cache hit 仍必须 materialize 到本次 run 独有的 working path；template 只允许关闭状态下的 opaque copy，OneNote 永不打开 template。
- 批量接口只能接收 recipe/manifest 声明并已完成路径预算、root containment 和 typed parent 约束的精确路径；不得接受任意 raw XML、任意路径、名称搜索或无界扫描。
- Notebook 直属 child 与 SectionGroup 嵌套 child 继续遵守现有 absolute-path/relative-ID 组合规则，禁止 absolute path 与非空 parent ID 混用。
- `OpenHierarchy` 返回 ID、请求成功或单次 snapshot 都不能单独放行。进入 Page 内容验证前，全部声明对象仍必须按 Notebook-relative typed address 唯一存在，并连续两次保持相同的 ID、类型、parent、section、page level、parent Page 和 sibling order。
- 从 Notebook 阶段传入的 observation 只有在绑定精确 working Notebook ID/path、结构完整、无异常且签名 schema 一致时，才能成为第一次稳定观察；否则 Fixture 阶段必须自行重新观察。
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
- `missing_page`：首次缺失只等待下一轮 observation；若在有界窗口内持续缺失，只允许对 manifest 已绑定的精确 owning Section 做一次 reactivation，再重新取得 Notebook snapshot，不按 Page 名称打开或无界重试；
- `deterministic_conflict`：类型、parent、路径或唯一性冲突，立即失败。

每轮只把 `missing_container` 传给下一次 batch。全部对象出现后开始计算稳定签名，不再重复打开已经成功出现的容器。

### 3. 跨阶段 observation handoff

Notebook 阶段将最后一次结构完整的 observation 作为 run-local、content-free evidence 传给 Fixture runtime，至少绑定：

- working Notebook ID 与 canonical working path；
- role、manifest fingerprint/instance 和结构签名 schema；
- capture 时间、对象计数、typed-address→live-ID 映射和稳定签名；
- batch/observation 次数以及尚未完成的稳定次数。

Fixture runtime 验证 handoff 后，将其作为第一次候选观察，并独立读取第二次 hierarchy。两次不一致时重置稳定计数，不得继续完整 Page 内容验证。

### 4. Evidence 与 progress

将 Notebook 阶段耗时拆分并持久化：

- `cache_copy_seconds`；
- `notebook_shell_open_seconds`；
- `hierarchy_batch_activation_seconds`；
- `hierarchy_observation_seconds`；
- batch 数、`OpenHierarchy` 请求数、snapshot 数和 PowerShell/COM session 数；
- Fixture 阶段是否接受 handoff、节省的 observation 数以及最终双稳定结果。

终端 progress 保持 content-free，至少区分 `cache open`、`cache hierarchy` 和 `scenario before`，长 batch 必须有有界 heartbeat，不能让用户误以为 runner 卡死。

## 自动化验证

- Batch 输入只能来自已验证的精确 managed working path 和 typed parent；任意路径、template path、absolute+parent 混用、重复 role 或越界路径均在启动 PowerShell 前拒绝。
- 多个 SectionGroup/Section 在一次 batch 中只创建一个 PowerShell/COM session；调用次数测试冻结该上界。
- 同会话 snapshot 暂不可见或末尾 hierarchy read 出现 typed transient error 时，成功返回 ID 的容器不被重复激活；全部打开请求成功后由统一 close/reopen checkpoint 建立第二次 identity，再交给 Fixture 双稳定门。逐项 activation error 最多只重试失败集合一次。
- Notebook 级 observer 用一次 snapshot 同时识别全部 present/missing/conflict 项，不再为每个对象重新读取 global hierarchy。
- 已出现的容器不会在后续轮次再次 `OpenHierarchy`；Page 缺失只触发 hierarchy observation，不触发 Page/Section 打开。
- 缺失 Page 后出现并连续两次稳定可以通过；ID/parent/order 震荡、回退、歧义或确定性冲突必须失败。
- 有效 handoff 可作为第一次稳定观察；Notebook ID/path、fingerprint、schema 或签名不匹配时拒绝 handoff并重新观察。
- 完整 Page 内容读取只在双稳定完成后发生；失败路径证明业务 mutation 调用次数为零。
- 单 role、多 role、cold-build 重新 materialize 和 validated-hit 路径均覆盖；现有默认关闭、keep 模式与 failure finalization 合同不变。
- 运行 manual-validation 纯测试、完整 `pytest -q`、相关 `--use-cache --dry-run --json` 和 `git diff --check`。

## 真实验证与度量

只有用户可以运行真实 scenario。完成前至少选择一个单 role 和一个多 role cache consumer，例如：

1. `reparent-page --use-cache`；
2. `copy-page --use-cache`；
3. 在单项稳定后运行 `all --use-cache`，确认 cache 异常不会跨 scenario 扩散。

真实 evidence 至少比较优化前后：

- opaque copy、Notebook shell open、hierarchy activation、Fixture convergence 和完整内容验证耗时；
- PowerShell/COM session 数、`OpenHierarchy` 数和 hierarchy snapshot 数；
- cache decision、old→live ID remap、template inventory unchanged；
- mutation/restore/cleanup/default close 或显式 keep 的最终状态。

耗时受 OneNote 与机器状态影响，不设置牺牲正确性的硬阈值；但若调用/session 数未显著下降，必须解释剩余调用来源，不能仅以偶然耗时下降关闭本 TODO。

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

- 四项目标全部实现，单 role 与多 role cache materialization 共用 Notebook 级批量激活/观察机制。
- 自动化证明每轮至多一个 PowerShell/COM session、只处理缺失容器、Page 不被逐个打开、跨阶段 handoff 不削弱双稳定门。
- 完整纯测试、dry-run、文档和差异检查通过，生产公开 tool/response 未意外变化。
- 用户真实运行至少一个单 role、一个多 role及后续 `all --use-cache`，确认业务验证和失败隔离仍正确。
- 真实 evidence 记录优化前后阶段耗时与调用分类，并证明 cache template 未打开、未修改，working bundle lifecycle 正常收尾。
