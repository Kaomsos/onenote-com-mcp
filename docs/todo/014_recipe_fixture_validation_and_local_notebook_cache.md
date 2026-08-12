# 014：Recipe 驱动的不可变 Notebook 模板缓存与隔离工作副本

> ID：014
> 状态：进行中
> 优先级：P2
> 类型：验证架构 / Fixture 性能与本地缓存安全
> 更新日期：2026-08-12

## 实施进展（2026-08-11）

已完成的纯实现与合同：唯一 `RecipeBase`、有序 Notebook role/cache identity、canonical fingerprint、`--use-cache`/`all --use-cache` parser 与 dry-run、统一的单/多 role programmatic fresh/cold/hit runtime、closed bundle opaque publish、per-role/bundle inventory、原子 entry/index、role working lease、COM path assertion、全部 role 的 live materialized revalidation、精确失效清理、保留范围内的 Interactive recipe/bootstrap Scenario、bounded UserAuthored recipe/freeze、稳定 `RecipeContractCase` catalog，以及 managed cache 的项目级安全决策。所有变更均只由纯测试和 dry-run 验证，Agent 未执行真实 Scenario。

FileAttachment 探索曾暴露公开 Page 对象必须使用 `kind` 而不是 parser-private `type`；当前 OneNote GUI 的多次“插入 → 文件附件”均只回读为 `InsertedFile`，无法形成独立 FileAttachment fixture。因此 FileAttachment 专属入口已删除，且不设类型别名；字段差异、真实观察和环境记录只保留在 [`lesson/onenote_page_object_kind_and_file_attachment_representation.md`](../lesson/onenote_page_object_kind_and_file_attachment_representation.md)。MeetingInfo 也因小众、难生成且当前价值低而删除专属入口。Embedded Spreadsheet（内嵌电子表格）尚未收集公开 `kind`/XML 证据，也按产品范围明确不支持且不建立 Table/InsertedFile/FileAttachment 别名。三类排除项保持 unverified/unsupported，不影响生产 fail-closed 门限；完整边界见 [`lesson/copy_content_type_exclusions.md`](../lesson/copy_content_type_exclusions.md)。

用户随后成功完成 InsertedFile detector 与人工 verdict，但首次 post-publish materialization 真实回读到一个已打开却没有任何 Section 的 working Notebook shell；working Notebook ID 也不同于 source ID，旧 manifest `canvas_page` 因而无法解析。实现据此改为在 exact working tree 内逐级显式打开 SectionGroup/`.one`，按唯一 Notebook-relative typed address 记录全部 old→live ID，并用 live structure 重跑 detector。首次 consumer 复验进一步证明：相对文件名调用 `OpenHierarchy` 虽返回 object ID，Section 仍可能未进入 exact working parent；旧实现还把这次 run-local activation failure 错误 quarantine 为 template `invalid`，导致后续立即表现为 cache miss。第二次复验进一步给出 `0x80042006/hrFileDoesNotExist`：当前 OneNote 不接受绝对 `.one` path 与非空 parent ID 的混合参数。现在层级加载只按顺序使用 `absolute working path + empty relative ID` 和兼容回退 `child filename + exact parent ID`，并始终回读 actual parent；Notebook folder exact path 验证后立即把实际 working ID 写入 lifecycle/cache lease，即使后续 child activation 失败也不再只留下模板内部旧 ID。active lease 冲突报告精确旧 run/path，历史失败 evidence 中的实际 ID 也参与 stale reconciliation。Working-copy activation failure 保留 working Notebook、live-ID lease 和 content-free 诊断但不污染已验证 immutable template；历史上仅因此被误隔离的 entry 在 recipe identity、原始 validation 和 byte inventory 全部复核后可恢复为 `ready`。ID rebind/live validation 失败仍严格 quarantine。用户手动关闭失败 working Notebook 后，后续运行可通过只读 ID/path probe 对遗留 lease 做 `stale_closed_observed` reconciliation。最初的只读 detector consumer 后续由 `interactive-copy-inserted-file --use-cache` 取代；新场景继续共用 bootstrap fingerprint/instance，命中后执行 live detector、一次 Copy 机器比较和人工 verdict，真正 miss/invalid 时仍在打开 Notebook 前提示运行 `bootstrap-inserted-file-fixture`，不会重新 bootstrap。

2026-08-11 用户真实复验已闭合 InsertedFile 的 bootstrap/cache 基础证据链：`run-20260811T022911Z` 的 cache-only consumer 以 `decision=validated_hit` 成功 materialize、加载层级、重绑定 live ID、观察到精确 `InsertedFile=1`、证明 `opened_template=false` 并正常关闭 working Notebook；随后 `run-20260811T023122Z` 的 bootstrap 完成人工 ACCEPT、detector 通过、发布 `state=ready`，并在第二份 materialized working bundle 上再次通过 live validation。该 bootstrap 的 Notebook/Section/Page source ID 与 working ID 均发生变化，`cache-structure-remap.json` 精确记录并通过全部映射，最终 source 与 working lifecycle 均正常关闭。2026-08-12 的 `run-2026-08-12-12-34-58` 又补齐 Copy comparator 与人工目标附件验收；当前剩余工作只涉及其他 Interactive/UserAuthored 和 E–F 矩阵，InsertedFile Copy 和共享 Move 门已经闭合。

同日命名回归复验又补充了两条真实证据：旧的长物理 Notebook 名称在两个独立 run 中均于 Notebook folder 的首次 `OpenHierarchy` 返回 `0x80042006`；缩短为 `__<scenario>-<?CACHED>-<YYYY-MM-DD-HH-MM-SS>__` 后，`run-2026-08-11-12-30-34` 与 `run-2026-08-11-12-31-13` 连续完成 `validated_hit`、exact working-path proof、hierarchy activation、live ID rebind、`InsertedFile=1` 和 `opened_template=false`。前一 run 的 working lifecycle 正常关闭，后一 run 显式覆盖 `--keep-worksite` 并按契约保留 active working Notebook/lease。该对照证明当前环境的短命名实现可用，但不推导 OneNote 的通用路径长度阈值。

同日 `copy-page` 的 fixture/live-validator 合同已改为共同读取 Page XML capability projection，并把 layered Copy recipe 提升到版本 2，旧的仅含构建期 `List/Tag` 声明、但 live Page XML 不含对应节点的模板不再命中。用户随后以同一新 fingerprint `3d4c7e6057f2eff9fb0b09b0cdafd43aecfa35132c4dcc9bac9120d673d60e3d` 完成四次真实运行：`run-2026-08-11-13-31-57` 为 `decision=cold_build`，`run-2026-08-11-13-33-47` 为带 `--keep-worksite` 的 `decision=validated_hit`，`run-2026-08-11-13-37-37` 为执行默认 cleanup/restore 的 `decision=validated_hit`，`run-2026-08-11-13-39-13` 则以 `--keep-notebook` 覆盖当前 recipe version 2 的 `decision=fresh`。四次的 root-only case 都以 `strict_canonical` 验证一页，full-subtree case 精确映射两页，其中父页通过 `strict_canonical`，List/Tag 子页通过 `semantic_list_tag`，全部 `verified=true`、`lossless=true`、无 issue/skipped content。cold/keep-worksite hit/default-cleanup hit/fresh 分别记录 90.271 秒/279、66.802 秒/210、86.440 秒/274、84.381 秒/237 次 bridge call，且每次都只启动一个 MCP process。两次 cached keep-worksite run 均证明 `opened_template=false`、materialization 未打开 template、template inventory 不变；默认 cached hit 精确清理三个 Copy 目标、`restored=true`、正常关闭 working Notebook，fresh run 同样清理三个目标并 `restored=true`，其 run 目录没有 cache materialization/immutability artifact，证明 fresh 路径未进入 cache runtime。至此 A 的 fresh/cold/hit、Copy 保真、keep-worksite 与默认 restore/cleanup 对照已由用户真实证据闭合；单次 metrics 只作观测，不承诺固定加速比例。

阶段 B 的双 Notebook Runtime 与六 case Copy 已由用户真实证据闭合。recipe version 3 直接使用 `source`/`destination` 双 Notebook bundle；`run-2026-08-11-14-27-08` 以新 fingerprint `ad0bf5be9c5eee60d0dfdebfca6cfa27a3dc5ae223f4dcb7327b5cee24736212` 完成 cold build、双 role live validation、关闭发布和重新 materialize，随后在 Copy 前的 destination snapshot runner 合同处失败，因此保留为构建链证据而不冒充业务成功。修复 plan evidence 与 created-target 精确 ID 定位后，`run-2026-08-11-14-54-05` 和 `run-2026-08-11-14-57-01` 连续以 `decision=validated_hit` 完成同 Section、跨 Section、跨 Notebook各自 root-only/subtree 的六次 Copy；每个 case 均为 `verified=true`、`lossless=true`，两个 working Notebook ID 互异，`opened_template=false`，且每次只启动一个 MCP process。前一 run 还完成反向 cleanup、双 Notebook 恢复和 lifecycle close；后一 run 按用户显式 `--keep-worksite` 保留全部六个根目标及两个 working Notebook。用户确认不再要求额外补跑，因此默认 cleanup/close 与保留现场两种成功分支均作为阶段 B 的最终验收证据。

同日三个升级后的容器 Recipe 又取得独立 `cold_build` 真实证据。`run-2026-08-11-21-33-01` 的 `copy-section` 与 `run-2026-08-11-21-36-13` 的 `copy-section-group` 均使用 source/destination 双 role bundle，在同一 MCP 内分别完成 Notebook 内部和跨 Notebook 两个 Copy case；四个 case 全部 `verified=true`、`lossless=true`，随后反向精确清理、双 role restore/close，`opened_template=false`、inventories unchanged。`run-2026-08-11-21-31-17` 的 `copy-notebook` 单 role bundle 则完整映射根 Section 富内容子树与新增 SectionGroup/Section/Page 子树共 7 个对象，刷新 target `modified` 后一次关闭目标，source lifecycle 也正常关闭，cache template 未打开且 inventory 不变。三次均为 cold build，因此补强了一般 Recipe 的 programmatic build/publish/materialize、单/多 role 与容器 Copy 证据，但不冒充对应 fingerprint 的 `validated_hit`。

`cache-invalidation` 的真实证据也已补齐：`run-2026-08-11-12-33-37` 与 `run-2026-08-11-12-36-16` 均以 `decision=invalidated_rebuild` 通过，root-level tombstone 证明只删除 managed cache root 下固定 fingerprint/instance entry，且 containment、ownership、无 reparse point、无 active lease 全部成立；重建 materialization 未打开 template，最终 inventory 不变。后续 `run-2026-08-11-19-07-17` 在 `--keep-worksite` 下保留双 Notebook 与 active lease，`run-2026-08-11-19-10-38` 同时从相同 fingerprint/instance 再次 `validated_hit`，为两个 role 得到与前一 run 全部互异的 live Notebook ID，并独立完成六 case、cleanup/restore 和 close；前一 worksite 的 lease 仍保持 active。结合 `run-2026-08-11-18-46-59` 未完成 live identity 建立时保留的冲突现场、`run-2026-08-11-18-50-54` 的精确拒绝以及用户关闭后 `run-2026-08-11-18-51-26` 的恢复成功，阶段 C 的并发隔离、真实 ID 冲突保护、stale reconciliation 与受控失效证据已闭合。

后续用户运行 `run-2026-08-11-16-25-41` 的 `move-page --use-cache` 暴露了一个缓存状态机回归：历史 working-copy activation failure 已把固定 entry 标记为 `invalid` 并按隔离语义保留目录，lookup 却把该 entry 折叠为普通 miss；runtime 完成 fresh fixture build 后尝试向同一路径 publish，因而被“不得覆盖现有实例”门限正确拒绝。实现现已增加 exact entry state 检查，并在首次 lookup、programmatic publish 前及 interactive re-bootstrap publish 前统一处理；非 open failure 的 `invalid` entry 必须经精确安全清理后以 `invalidated_rebuild` 重建，`cleanup_failed`、缺失 ownership metadata、未知状态、active lease 或 source 仍打开全部阻断重建。用户随后运行 `run-2026-08-11-16-38-40`，root-level tombstone 已证明旧 entry 在 containment、ownership、无 reparse point、无 active lease和 source 关闭全部成立后被精确删除，fresh bundle 完成 live validation、close、publish 与 opaque materialization，原 overwrite 冲突已由真实证据闭合。

同一 `run-2026-08-11-16-38-40` 随后在 working copy 打开 `Destination.one` 时暴露第二个 lifecycle 证据边界：绝对与 parent-relative 两种 `OpenHierarchy` 都返回同一 fresh Section ID，`GetHierarchyParent` 也证明其 parent 是本次 exact working Notebook，但全局 hierarchy snapshot 暂时未列出该 Section，旧 wrapper 因而误报 activation failure、把已验证模板 quarantine，并让 cache lease 保留 fresh source ID 而非实际打开的 working ID。`run-2026-08-11-16-42-11` 与 `run-2026-08-11-16-43-45` 随后被该失败现场的 active lease 正确拒绝；`run-2026-08-11-16-47-56` 的 fresh `move-page` 则完整通过并关闭 Notebook，证明 mutation 本身未回归。实现现要求全局 snapshot 不可见时，对同一 COM 返回 ID 做 exact-self 回读并同时证明类型、名称、非回收站状态和精确 parent，之后仍必须通过完整 live Recipe validation；仅有返回 ID 或 parent 仍不足以继续。Working-copy open failure 现在保留实际 live working ID、active lease 和现场，但不再污染已验证模板；历史 `materialized-open`/`cold-materialized-open`/`bootstrap-materialized-open` 误隔离只有在原 validation 和 byte inventory 重新通过后才可恢复。纯合同已覆盖 invalid 清理重建、发布前并发隔离、root tombstone、三类历史 open recovery、exact object/type/name/parent 正负分支、run-local failure 不隔离模板及失败 lease live-ID 绑定；manual-validation 纯测试 `348 passed`、完整 pytest `549 passed`、`move-page --use-cache --dry-run --json` 与 `git diff --check` 通过。

用户关闭保存的失败 working Notebook 后，`run-2026-08-11-16-52-10` 与 `run-2026-08-11-16-52-56` 连续以 `decision=validated_hit` 完整通过。两次都对 `Destination.one` 记录 `activation_proof=exact_object_and_parent`、对 `Source.one` 记录正常 `global_snapshot` proof，随后 typed structure remap 全部通过；working Notebook ID 分别为 `{0054489A-BEAE-4C3D-A62D-D0276A16076F}{1}{B0}` 与 `{7F13ED97-722B-4DC5-B0F0-A6AB7A171BBB}{1}{B0}`，均不同于模板记录的 `{732D6027-98CD-4E8A-83B0-6E3ADB2DBEE8}{1}{B0}`。每次 Move 都分配并解析两个 fresh target ID，`verified=true`、`lossless=true`、collision anchor unchanged，完成严格非永久 source subtree deletion；`opened_template=false`、template inventory `all_templates_unchanged=true`，working lease 为 `closed`，lifecycle 为 `closed_preserved`。至此本次 invalid-as-miss overwrite 回归、全局 snapshot 滞后分支和连续 cache hit 真实验收均已闭合，Agent 未执行真实 Scenario；该问题属于本 TODO 的 cache lifecycle，不改变 TODO 015 已闭合的 mutation identity 结论。

尚未满足完成定义，因此不得标记已完成：A、B 与 C 已由用户真实证据闭合；D 的 InkDrawing、UIShape、MediaFile、InsertedFile 已分别完成 bootstrap/Copy、机器 comparator、人工 verdict 和静态 allowlist 评审，其中录像 MediaFile 还覆盖同 Section/跨 Section，并由用户确认人工删除源 Section 后两个副本仍有效。InsertedFile 的 `run-2026-08-12-12-34-58` 复用既有 ready recipe/cache，完成 strict canonical Copy、机器 comparator、用户打开附件确认和正常 lifecycle 关闭，现已进入生产 validated Copy 集合并可复用共享 Move 门。UserAuthored 已有显式实例 consumer 和多实例 cache identity，但尚无用户 ready/evidence-only/越界真实证据。`FileAttachment`、`MeetingInfo` 与 `Embedded Spreadsheet` 已按产品取舍排除出当前 Copy 取证完成条件；Embedded Spreadsheet 的排除没有真实 backend 观察，不代表跨版本平台限制。仍必须由用户本人补齐本文件 E–F 的剩余真实 OneNote 验收并确认 evidence。当前 mock、临时文件系统与 dry-run 证据不能替代该门槛。

## 背景

[TODO 011](011_scenario_owned_fixture_recipes.md) 已让每个公开 Scenario 显式拥有唯一 fixture recipe，并由 recipe 负责现有一般 fixture 的构建与场景专属验证。本 TODO 在该唯一所有权上直接扩展缓存和多 Notebook 能力，不建立一套与现有 recipe 并行的 template recipe registry。当前真实 manual-validation 仍为每次运行创建全新的 disposable Notebook；该默认值隔离性最强，但在重复调试同一复杂 RichText/Table/Image/List/Tag fixture 时，会重复支付相同的 COM 创建、回读与内容验证成本。

本 TODO 评估并实现一个以 recipe 为唯一所有者的本地 fixture cache。所有 recipe 从合同层都生成并验证一个由一个或多个具名角色组成的 Notebook bundle；单 Notebook 只是仅声明一个 role 的普通情况，不另建 `MultiNotebookRecipe` 子类。每个 role 对应一个固定格式、不可变的 OneNote Notebook 模板；每次隔离验证都先把整个 bundle 透明复制到全新的 run-scoped 工作目录，再让 OneNote 只打开和操作各 role 的工作副本。缓存母本本身永远不由 OneNote 打开，也永远不承接 scenario mutation。

保留现有 `RecipeBase` 作为全部 Recipe 的唯一基类，并把原先规划在 `FixtureRecipe` 上的 bundle、cache identity、build 和 validation 合同统一并入这个基类；不再保留并行的 `FixtureRecipe` 基础类型。`RecipeBase` 的非交互式具体实现可以程序化构建 fresh bundle。对于墨迹、媒体、会议详情或其他需要用户通过 OneNote UI 创建的内容，使用明确面向 [TODO 004](004_interactive_copy_move_content_fidelity_validation.md) 的 `InteractiveFixtureRecipe`：它在统一 Recipe/cache/bundle 合同上增加受控交互 bootstrap。对于需要让用户在 disposable Notebook 内尽可能自由创作内容和局部结构、再冻结为具体模板实例的探索或取证流程，使用 `UserAuthoredRecipe`。类型关系固定为 `RecipeBase → InteractiveFixtureRecipe → UserAuthoredRecipe`；三者都天然具有相同的 cache 能力和多 Notebook role 集合。

缓存必须保持 local-only，不使用 Microsoft Graph、云存储、OAuth、遥测或远程内容处理。实现只允许对已经关闭的 disposable Notebook 目录执行不解析内容的 opaque byte-for-byte copy；不得解析、编辑、拼接或重写 `.one` 二进制内容。这个有限的文件复制/清理能力与仓库当前“人工验证不得删除本地 Notebook 文件或目录”的规则存在冲突，因此实施前必须先形成显式项目级安全决策并同步根 `AGENTS.md`：例外只能覆盖受管理 cache root 下未打开、未 leased 的模板/staging 路径，绝不能覆盖工作副本、普通 validation artifact 或用户 Notebook。

这里的“复用”只表示复用不可变模板 bundle 的字节，不表示重复打开或 mutation 同一个 Notebook 实例。任何一次 scenario 都必须为每个 role 获得独立工作副本和独立 lifecycle lease，并为整个 bundle 获得独立 manifest 和 evidence。

## 目标

- 以现有 `RecipeBase` 为 bundle 身份、角色集合、构建和验证的唯一基类；原 `FixtureRecipe` 合同并入并改名为 `RecipeBase`，不让两个基础类型并存，也不新增按 scenario 名称分派的 cache switch、第二 registry 或 `MultiNotebookRecipe`；
- 所有 recipe 都使用同一个 `notebook_roles` 集合合同并天然支持多个 Notebook；单 Notebook recipe 只声明一个 role，缓存、manifest、lease 和验证代码不使用单项特例；
- recipe 提供稳定、结构化的 cache identity；公共 canonical builder 从 recipe/schema 版本、全部 role profile、manifest keys、内容能力、创建工具、bundle invariant 和影响 fixture 的参数计算 `cache_fingerprint`，recipe 不自行拼接任意摘要字符串；
- `InteractiveFixtureRecipe` 在 `RecipeBase` 上增加用户交互、checkpoint、timeout、human verdict 和 bootstrap scenario，明确承载 TODO 004 的逐类型 Copy 内容保真 fixture；
- `UserAuthoredRecipe` 继承 `InteractiveFixtureRecipe`，允许用户在受控 authoring zones 内尽可能自由地创建内容和局部层级，并在确认后冻结为带 `template_instance_id` 的不可变实例；自由创作不等于接受业务 Notebook、越出 disposable role 或绕过 live validation；
- 所有 recipe 都具有同一 cache identity、publish、materialize 和 invalidation 合同；默认 fresh build 只是运行时默认值，是否传入全局 `--use-cache` 不改变 Recipe 的 cache 能力；cache index、锁、复制、发布、失效和清理由公共 cache runtime 负责，不成为 recipe 的文件系统能力；
- `run.py` 为全部具名 Scenario 和特殊串行入口 `all` 提供全局 `--use-cache`；未传入时不查询、创建或修改 cache，传入时积极执行 lookup→validated hit materialization，未命中的非交互式 `RecipeBase` 实现自动 cold build/publish 后再 materialize；
- cache hit 原子 materialize 整个母本 bundle 到新的 run-scoped role paths；lifecycle wrapper 必须逐 role 打开工作副本并证明 OneNote 回报的规范化路径等于对应 working path、绝不等于任何 cache template path；
- 全部 role 的工作副本在 mutation 前必须分别执行与 fresh build 相同或更严格的 active-ID、拓扑、标题、编号、内容和 capability 验证，再执行 recipe 声明的跨 role bundle invariant；
- 非交互式 `RecipeBase` 实现的 cache miss 或可安全重建的失效 entry 由 recipe 重新构建、验证和发布整个 bundle；`InteractiveFixtureRecipe`/`UserAuthoredRecipe` 的 miss/失效只能返回 `interactive_bootstrap_required`，由用户显式运行其具名 bootstrap scenario，绝不能在普通 scenario 中隐式等待输入或自动扩权；
- 缓存只保存在本地 validation 工作区，拥有明确索引、规范化模板路径、byte inventory/hash、创建时间、最后成功 materialize/validation 时间、recipe fingerprint 和清理证据；
- 保持单 Scenario、单 MCP 进程、静态最小 policy/allowlist、before/after evidence、失败保留和 HUMAN-GATED 真实执行边界；
- 模板 bundle 是只读发布物；scenario mutation、restore、`--keep-worksite` 和失败状态都只发生在各 role 的工作副本，永远不回写母本；
- 模板失效后的精确清理必须自动完成；非交互式 `RecipeBase` 实现可随后自动重建，InteractiveFixtureRecipe/UserAuthoredRecipe 则转为 `bootstrap_required` 并等待用户重新运行其具名 bootstrap scenario。删除范围必须是经过 root containment、fingerprint、entry ownership、非打开状态和无 lease 校验的单个精确路径。

## 推荐契约

### 1. Recipe 统一声明 Notebook Bundle 与缓存身份

现有一般 fixture 的 Recipe 直接扩展为缓存和多 Notebook 的唯一合同，不新增只服务 cache 的平行类型。全部 Recipe 都具有 cache identity、build/publish/materialize 能力；Notebook 数量是数据，不是继承层级：

```python
@dataclass(frozen=True)
class NotebookRoleSpec:
    role: str
    profile: FixtureProfile
    fixture_parameters: Mapping[str, JSONValue]

@dataclass(frozen=True)
class FixtureCacheIdentity:
    schema_version: int
    recipe_name: str
    recipe_version: int
    notebook_roles: tuple[NotebookRoleSpec, ...]
    evidence_schema_version: int
    contract_compatibility_version: int

class RecipeBase:
    cache_identity: FixtureCacheIdentity

    async def build(
        self,
        context: FixtureBundleContext,
    ) -> FixtureBundleBuildReceipt: ...

    def validate_live(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport: ...
```

- `notebook_roles` 至少包含一个唯一、稳定的 role；单 Notebook recipe 也使用 tuple，例如 `("source",)`，runtime 不提供单 Notebook 快捷分支。
- 每个 role 独立声明 profile、manifest namespace、fixture 参数和 creation tools；recipe 另外声明跨 role invariant，例如源/目标 Notebook ID 必须不同、目标锚点属于 destination role。
- 每个现有 Scenario 的一般 fixture build 都继续由其唯一 `RecipeBase` 具体实现接管；cache 是这个基类合同的固有能力，不通过 `reusable` 标志把 Recipe 分成“可缓存/不可缓存”两套。当前 `common.fixture_models.FixtureRecipe` Protocol 的合同迁入现有 `fixture_recipes.recipe_base.RecipeBase` 后，Scenario/runtime/type annotation 都统一使用 `RecipeBase`，不得让二者作为两个公共基础类型并存。Runner 默认仍走 fresh build，显式全局 `--use-cache` 则积极命中或构建 cache。
- `build()` 返回构建调用与产物的 receipt，但 receipt 不能成为 cache hit 的真相；fresh build 与 cache hit 都必须从当前打开的 working bundle 形成 `FixtureBundleObservation`，并调用同一个 `validate_live()`。
- `cache_fingerprint` 由公共 canonical builder 对 `FixtureCacheIdentity`、各 role 的 manifest keys/expected structure/content capabilities/validation conditions/creation tools、跨 role invariant 和影响 fixture 的参数统一计算；recipe 只提供结构化 identity。
- Fingerprint 不得包含随机 token、绝对 run directory、Notebook 运行时 ID 或时间戳，也不得仅依赖 Python 类名、源码路径或文件时间。

### 2. `InteractiveFixtureRecipe`、`UserAuthoredRecipe` 与交互式 Bootstrap

Recipe 类型只区分 fixture 的创作方式，不区分 Notebook 数量或 cache 能力：

```python
class InteractiveFixtureRecipe(RecipeBase):
    """Abstract base for one explicitly defined interactive fixture."""

    build_mode = BuildMode.HUMAN_BOOTSTRAP_REQUIRED
    bootstrap_scenario_name: str

    async def build(self, context: FixtureBundleContext):
        raise InteractiveBootstrapRequired(...)

    async def build_scaffold(
        self,
        context: FixtureBundleContext,
    ) -> FixtureBundleBuildReceipt: ...

    def validate_authored_content(
        self,
        observation: FixtureBundleObservation,
    ) -> FixtureValidationReport: ...


class UserAuthoredRecipe(InteractiveFixtureRecipe):
    authoring_zones: tuple[AuthoringZoneSpec, ...]

    def freeze_authored_instance(
        self,
        observation: FixtureBundleObservation,
    ) -> AuthoredTemplateInstance: ...


class InsertedFileInteractiveFixtureRecipe(InteractiveFixtureRecipe): ...
class InkDrawingInteractiveFixtureRecipe(InteractiveFixtureRecipe): ...
class MediaFileInteractiveFixtureRecipe(InteractiveFixtureRecipe): ...
class UIShapeRepresentationDiscoveryRecipe(InteractiveFixtureRecipe): ...
```

#### `InteractiveFixtureRecipe`

`InteractiveFixtureRecipe` 明确面向 [TODO 004](004_interactive_copy_move_content_fidelity_validation.md)，用于墨迹、形状、短时合成录像和插入文件等必须由用户通过 OneNote UI 创建、但预期位置、类型和验证目标仍由 Recipe 严格声明的内容：

- `InteractiveFixtureRecipe` 本身是抽象基类；TODO 004 的当前具体实现分别是 `InkDrawingInteractiveFixtureRecipe`、`UIShapeInteractiveFixtureRecipe`、`MediaFileInteractiveFixtureRecipe` 和 `InsertedFileInteractiveFixtureRecipe`。Shape 由真实矩形/箭头证据冻结为公开 `kind=InkDrawing` 加 `ShapeInfo` 的复合 `UIShape`，不得伪造字面量 `kind=Shape`；
- 每个具体子类只负责一个清晰 fixture 合同，包括自己的 role/Canvas、程序化 scaffold、用户操作说明、requested/observed 检测、对象计数、binary/semantic comparator、human verdict 字段和失败证据；不得在基类或 common runtime 中通过 `content_type` 字符串 switch 实现不同 fixture；
- Scenario 必须显式拥有具体 Recipe 实例。若确需一次验证多种内容，应声明一个具名、静态组合 Recipe，明确列出其具体子 Recipe/组件及合并后的 profile/allowlist/budget；不得由任意 CLI 字符串动态构造未审查组合，也不得形成第二个 Recipe registry；
- 完整的 `notebook_roles`、role profile、manifest schema、内容能力和 `validate_live()`，因此模板不是“任意人工 Notebook”；
- 显式、固定的 `bootstrap_scenario_name`，对应一个经过代码审查并注册的具名 Scenario；不得由用户传入任意 recipe 名、Notebook ID 或模板路径进行动态 bootstrap；
- 只供其 bootstrap scenario 调用的 `build_scaffold()`，负责创建全部 role 的程序化骨架和 exact-ID 编辑目标；普通 scenario 和 cache miss path 不得调用该方法；
- 每种请求内容使用 Recipe 声明的精确 role/Section/Page、对象计数、allowed capability 和 comparator；对缺失、额外、放错位置、未知 namespace、敏感内容或无法形成自动 invariant 的对象 fail closed；
- 用户 UI verdict 与机器 validator 共同形成内容保真证据，但 UI verdict 不能替代自动 comparator，也不能在运行时修改静态 Copy allowlist；Move 复用同一类别门禁。

#### `UserAuthoredRecipe`

`UserAuthoredRecipe` 用于让用户在本次创建的 disposable Notebook bundle 中获得尽可能大的创作自由，再把确认时刻的结果冻结为一个不可变 cache template instance。它仍然不能导入或复用用户业务 Notebook：

- Recipe 预先声明受控 `authoring_zones`、系统保留的 instructions/anchors、允许用户修改的 role 和边界；用户可在 zone 内创建、删除、重命名或重排局部 Section/Page，并添加大部分 OneNote Page 内容；
- 用户不得修改 bundle role 身份、系统保留 marker、cache/lifecycle 路径或 zone 之外的对象，也不得把外部 Notebook ID/path 作为模板来源；
- 确认后由 `freeze_authored_instance()` 捕获完整 role catalog、层级、内容能力、机器可比较投影、未知能力、精确 manifest 和 `template_instance_id`；后续 materialization 只能复用这个冻结实例，不能继续漂移；
- contract fingerprint 标识 UserAuthoredRecipe 的 authoring/validation 规则，`template_instance_id` 与 bundle inventory digest 标识某次具体用户创作；同一 contract 可以产生多个实例，但 cache lookup 必须显式绑定当前选定实例，不能按名称猜测；
- 如果所有内容都具有稳定自动 validator，可发布为 `ready`；含未知、未验证或仅能人工观察的内容只能发布为 `evidence_only`，且 `mutation_eligible=false`、`move_source_deletion_allowed=false`；
- “尽可能大自由”只扩大 disposable authoring zone 内的创作范围，不放宽 local-only、synthetic content、资源预算、精确 lease、未知内容 fail-closed 和失败保留规则。

#### 共同 Bootstrap Scenario

每个具体 `InteractiveFixtureRecipe` 和 `UserAuthoredRecipe` 都必须绑定一个静态、具名、`registered_for_all=False` 的 bootstrap Scenario，或者被一个同样静态注册、能够保持逐 fixture 证据边界的具名组合 Scenario 显式拥有。前者面向 TODO 004 的逐类型严格 Canvas；后者面向受控 authoring zones。二者都执行完整、不可恢复为普通 helper action 的 human-gated 闭环：

```text
create fresh disposable Notebook bundle for all declared roles
→ start the scenario's single statically allowlisted MCP process
→ create the programmatic scaffold and exact-ID manifest
→ write checkpoint and show per-role/Page or authoring-zone synthetic-content instructions
→ wait for a run-bound user confirmation with bounded timeout
→ capture a fresh live bundle observation
→ run profile checks and Recipe.validate_live()/validate_authored_content()
→ for UserAuthoredRecipe, freeze the authored instance and assign template_instance_id
→ persist machine evidence and per-capability human verdict
→ precisely close every role Notebook
→ opaque-copy the closed bundle to random staging
→ generate per-role byte inventories and bundle evidence
→ atomically publish one ready cache entry
→ materialize a second fresh role working bundle from the published entry
→ open and live-validate the materialized bundle with the same MCP process
→ prove opened_template=false and close the verification working bundle
```

- 普通 scenario 遇到 InteractiveFixtureRecipe/UserAuthoredRecipe cache miss、invalid entry、不兼容版本或缺失实例选择时，必须在零 scenario mutation 状态返回 `interactive_bootstrap_required`；不能自动进入交互等待、自动调用 bootstrap scenario 或临时扩张 policy。
- Bootstrap 失败、超时、EOF、用户取消、验证失败或任一 role 无法精确关闭时，不得发布 `ready` entry；保留 disposable bundle、lease、checkpoint 和人工接管证据。
- `--keep-worksite` 可以保留 bootstrap 现场用于诊断，但保留打开的 Notebook 不能同时发布为模板；结果必须明确为 `template_not_published`。
- Bootstrap scenario 的 dry-run 只展示 role、checkpoint、交互、验证、close、stage 和 publish 计划，不创建或读取 cache、不等待 stdin、不启动 MCP 或访问 OneNote。
- Bootstrap scenario 本身就是交互 Recipe 的真实发布验收：发布后必须立即从 cache master 创建第二组全新工作副本并重新执行完整 live validation，不能把最初供用户编辑的 Notebook 当作 cache-hit 证据。TODO 004 使用固定的 `bootstrap-ink-drawing-fixture`、`bootstrap-shape-fixture`、`bootstrap-media-file-fixture`；三者均已有精确 detector 和 cache-only Copy consumer。UserAuthoredRecipe 使用另一个显式注册的具名场景。实际名称不能实现为接受任意 recipe 名的通用命令。
- 真实 cold bootstrap、发布后的首次 validated materialization 和强制失效后的重新 bootstrap 都只能由用户本人显式运行并确认。

### 3. 本地 Bundle Cache Index 与 Lease

建议默认使用未纳入版本控制的 `.local-validation/fixture-cache/`：

```text
.local-validation/fixture-cache/
  index.json
  <fingerprint>/
    recipe-entry.json
    instances/
      <template-instance-id>/
        bundle-entry.json
        notebooks/
          <role>/
            template-notebook/
              <opaque OneNote notebook files>
            template-manifest.json
            template-fixture-result.json
            template-snapshot.json
            byte-inventory.json
        bundle-validation.json
        lock.json
```

`bundle-entry.json` 至少记录：

- fingerprint、`template_instance_id`、recipe 名称与版本、具体 Recipe class、完整 role 集合，以及 `programmatic`、`interactive` 或 `user_authored` 模板来源；
- 每个 role 的规范化 template directory、发布 staging directory 和 cache root containment 结果；
- 每个模板关闭前最后确认的 Notebook ID/name/path，仅用于来源证据和冲突检测，不能作为工作副本运行时身份；
- 每个 role template directory 中所有相对文件的长度和 cryptographic hash，以及整个 bundle inventory digest；不得记录或解释 `.one` 内部结构；
- manifest/snapshot/evidence schema version；
- 创建时间、最后成功 materialize/验证时间、role validation 和 bundle validation checks；
- `state=building|ready|invalid|rebuilding|bootstrap_required|cleanup_failed`；
- 当前 clone/rebuild lock owner、运行 ID、进程 ID 和有界过期策略；
- 失效原因、最后失败阶段、清理目标、清理结果和人工处理说明。

每个 cache artifact 都使用 `(fingerprint, template_instance_id)` 作为 typed identity。非交互式 `RecipeBase` 实现可以使用由完整构建输入确定性产生的 instance ID；InteractiveFixtureRecipe 的实例来自某次严格 bootstrap；UserAuthoredRecipe 允许同一 fingerprint 下存在多个冻结实例，并要求调用者或 Scenario 静态默认值显式选择 instance ID。不得按显示名称、最近修改时间或目录枚举顺序猜测实例。

索引和 entry 更新必须采用原子替换。整个 bundle 先写入目标 instance 同级的随机 staging directory，全部 role 完成 byte inventory、recipe evidence 和跨 role 校验后再一次性原子发布；不得独立发布某个 role，也不得把半成品目录标为 `ready`。不得接受任意外部路径、符号链接/junction/reparse-point 逃逸、名称定位 mutation 目标或无界目录扫描。

每次命中后，各 role working copy 固定放在当前 fresh run directory 下，例如：

```text
.local-validation/run-<timestamp>/
  notebooks/
    <role>-working-copy/
  cache-materialization.json
  lifecycle-leases/
    <role>.json
  manifest.json
  ...
```

`cache-materialization.json` 必须逐 role 记录 template path、working path、复制前后 byte inventory、OneNote 实际打开路径和 `opened_template=false` 证明，并记录 role 集合、bundle fingerprint、全部工作 Notebook ID 互异和跨 role validator 结果。

### 4. 全局 `--use-cache` 积极缓存模式

`run.py` 的公共 parser 为所有具名 Scenario 提供同一个默认关闭的全局选项，并由特殊串行入口 `all` 接受和逐子命令透传：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page `
  --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py all --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py all --use-cache
```

约束：

- 不带 `--use-cache` 时严格保持当前 fresh 行为：不 lookup、不读取 index、不创建 cache entry、不失效/清理旧 entry；这也是发布和最保守验收的默认路径；
- `--use-cache` 只影响 fixture 来源，不改变 scenario policy、tool allowlist、mutation 参数或真实执行授权；其含义是“积极使用或构建不可变模板，再创建工作副本”，绝不是“打开缓存 Notebook”；
- 对非交互式 `RecipeBase` 实现，`--use-cache` 的确定性流程为 `lookup exact fingerprint/instance → validated hit materialize`，miss 时为 `fresh build → validate → close → publish → materialize`；cache 损坏或不兼容时先按受控失效合同处理，再 cold build；不得静默绕过 publish 直接把 build Notebook 交给 mutation；
- 对 InteractiveFixtureRecipe/UserAuthoredRecipe，validated hit 正常 materialize；miss、invalid 或缺失实例时返回 `interactive_bootstrap_required` 和精确具名 scenario，不能因为传入 `--use-cache` 就隐式等待用户或自动执行真实 bootstrap；
- dry-run 必须显示 `cache_mode=use_cache`、fingerprint、instance selection、cache root、全部 role 的 template/working path、hit/miss 分支、bundle copy/open/validate、锁、清理、cold build 或 bootstrap-required 步骤，但不得实际读取 cache 目录、判断真实 hit/miss 或探测 OneNote；
- `all --use-cache` 继续按现有合同串行启动完整独立子场景，并向每个显式纳入 `all` 的子命令透传 `--use-cache`；Scenario 之间不共享 run-dir、working copy、MCP、lease 或 evidence，只允许复用各自经过 fingerprint/instance 匹配的不可变 cache masters；任一子场景的交互 bootstrap requirement 按普通失败报告，不得自动启动未纳入 `all` 的 bootstrap Scenario；
- 不允许调用方传入任意 Notebook ID/path 充当 cache entry；选择只能通过当前 recipe fingerprint 和受控 index；
- 无论 hit、非交互式 `RecipeBase` 实现刚完成 rebuild，还是交互 Recipe 刚完成用户 bootstrap，实际 scenario 都必须再从发布的 bundle materialize 一组新的 role 工作副本，不能直接继续操作用来生成模板的 Notebook；`evidence_only` 实例只能进入明确允许取证的只读/Copy 验证路径。

开发文档和 manual-validation README 应把 `--use-cache` 作为日常反复调试复杂 fixture 的推荐命令；涉及缓存实现本身、fresh/cold 对照、发布前最终验收或排查 cache 污染时，明确省略该选项。推荐不改变默认值，避免 CI、首次用户运行或不了解 cache 状态的调用方意外进入文件复制/失效路径。

### 5. Materialize Notebook Bundle 并重新验证

命中 entry 后必须依次：

1. 原子取得 fingerprint 级 bundle clone/rebuild lock，并确认全部模板内部 Notebook ID 都没有未决 working-copy lease；锁与冲突检查必须覆盖整个 cache store，不能只检查同 fingerprint；
2. 校验 bundle entry、全部 role 的 template root containment、无 reparse-point、role 集合和完整 byte inventory；
3. 在当前 run 下为每个 role 创建全新的 working directory，并将对应 template directory opaque byte-for-byte 复制进去；任一 role 失败则整个 materialization 失败；
4. 在打开前逐 role 比较模板与工作副本的相对文件集合、长度和 hash，并复核 bundle inventory；任何差异都视为 cache failure；
5. lifecycle wrapper 按稳定 role 顺序只打开 working paths；每次打开后回读精确 Notebook ID/name/path，并强制 `actual_path == role.working_path`、`actual_path != every template path`；
6. 如果任一模板内部 Notebook ID 已在其他路径打开、两个 role 被 OneNote 解析成同一 Notebook ID，或实际路径无法唯一证明，立即拒绝，不得关闭/接管未知实例；
7. 使用当前 scenario 已启动的唯一 MCP client 捕获全部 role 工作副本的 fresh fixture snapshots；
8. 由当前 recipe 对每个 role 重新执行通用 profile checks 和 role validator，再执行跨 role bundle validator；
9. 核对每个 role manifest 中所有精确 ID 的工作副本映射。若 clone 后 ID 保持不变，必须证明全部 cache masters 未打开且不存在另一份同 ID 工作副本；若 OneNote 重分配 ID，必须按 role 生成完整、唯一、类型一致的 `template_id → working_id` 映射；
10. 写出本次 `cache-materialization.json` 和 `cache-validation.json`。只有全部 role 和 bundle invariants 通过，才把角色化工作副本集合交给 Scenario mutation。

不得只凭路径存在、Notebook 名称、旧 `fixture-result.json` 或旧 hash 判定命中有效。Byte inventory 只证明复制完整；当前全部工作副本的 OneNote 回读、role validation 和 bundle validation 才是 mutation 发布门。跨 Notebook Copy/Move 至少必须证明 source/destination role 来自同一 run 和 fingerprint、Notebook ID/路径互异、目标父级属于 destination，并将两侧 snapshot 同时绑定进 plan digest。

### 6. 模板 Bundle 不可变、工作副本独立

缓存模板与运行状态必须单向流动：`validated fresh/interactive bundle → all roles closed → staging bundle → immutable ready bundle → run-scoped role working copies`。任何运行结果都不得反向写回模板：

- scenario mutation、restore/cleanup、`--keep-worksite`、Delete、Move、Copy cleanup、`copy_only` 和失败 handoff 只影响对应 role 的 working copies；缓存模板字节和 template evidence 保持不变；
- 每个 role working copy 继续遵守现有失败保留规则。任一副本处于 open/lease uncertain 时不得删除，也不得为了释放 cache lock 强制关闭；跨 Notebook Copy/Move 的失败必须保留整个角色集合和两侧证据；
- 任何 cache entry 中的模板内部 Notebook ID 已存在未关闭或状态不确定的工作副本时，默认拒绝新的 materialization，即使调用来自不同 fingerprint；
- 默认成功关闭全部 role 后，也只允许清理各自 run-scoped working paths；不得用任一 working copy 刷新模板；
- 只有模板 inventory、materialization 或 mutation 前 recipe validation 失败才使模板 `invalid`。Scenario mutation 自身失败不自动证明模板损坏，但必须记录两类失败的边界；
- invalid entry 进入受控 cleanup；非交互式 `RecipeBase` 实现可以随后 rebuild，InteractiveFixtureRecipe/UserAuthoredRecipe 只能等待新的用户 bootstrap。旧模板永远不能从某个运行后的工作副本“修复”。

### 7. 失效清理、自动重建与重新 Bootstrap

非交互式 `RecipeBase` 实现的失效必须执行确定性的 `invalidate → clean exact entry → rebuild bundle → publish → materialize` 流程；InteractiveFixtureRecipe/UserAuthoredRecipe 则执行 `invalidate → clean exact entry → interactive_bootstrap_required`，只有用户显式运行具名 bootstrap scenario 后才可重新发布和 materialize：

1. 把 entry 原子标记为 `invalid`，记录 fingerprint、原因、模板路径和失败证据；
2. 解析模板路径并同时证明：位于配置的 cache root 下、恰为该 fingerprint entry、不是 cache root 本身、不是 workspace/run root、不是 working path、没有 reparse-point、没有 open/lease owner；
3. 先删除或移动 entry 内已知的模板/staging 文件，再删除空 entry directory；禁止使用未解析环境变量、宽泛 glob 或由 Notebook 名称拼接的路径；
4. 清理完成写 tombstone/cleanup evidence 到 cache root 级日志；如果任何文件无法删除，状态改为 `cleanup_failed`，停止且不得在同一路径覆盖重建；
5. 对非交互式 `RecipeBase` 实现，清理成功后创建新的随机 staging directory，由 recipe 构建 fresh Notebook bundle、逐 role 与跨 role 完整验证、精确关闭全部 Notebook 并 opaque copy；
6. 对 InteractiveFixtureRecipe/UserAuthoredRecipe，清理成功后停止并返回 `interactive_bootstrap_required`；不得由 cache runtime 模拟 UI、复用旧 working copy 或自动运行 bootstrap scenario；
7. 对 staging bundle 生成 per-role 和 bundle byte inventory，一次性原子发布为同 fingerprint 的新 `ready` entry；
8. 再从新模板 materialize 全新的 role 工作副本，OneNote 只打开这些副本。

受控清理属于本 TODO 的必要能力，不是通用 Notebook 删除工具。实施前必须修改仓库级安全合同，明确只允许删除 managed cache root 内、由本系统创建、当前未打开且未 leased 的模板/staging；若该项目级决策未获接受，实现只能把 entry 隔离为 tombstone/quarantine，不能声称本 TODO 已完成。

## 安全边界与风险

### P0：OneNote 直接打开缓存母本

风险：路径绑定错误使 lifecycle wrapper 打开 `template-notebook/`，scenario mutation 永久污染所有后续运行的模板。

缓解：template path 与 working path 是不同的冻结 typed 字段；wrapper 只接受 working path，打开后强制回读规范化实际路径并写 `opened_template=false` 证据。任何相等、别名、junction 或无法证明的路径都在 mutation 前拒绝。

### P0：清理越界删除用户或工作数据

风险：invalid cleanup 使用错误 root、名称拼接、symlink/junction 或宽泛递归删除，命中 workspace、run evidence、工作副本或用户 Notebook。

缓解：只允许 managed cache root 下的精确 fingerprint entry；删除前验证 resolved containment、entry ownership、非 root、非 working path、无 reparse-point、无 open lease。清理目标和逐项结果写 root-level tombstone；任何不确定性 fail closed。

### P0：多个克隆以同一内部 Notebook ID 同时打开

风险：opaque copy 保留内部 ID；前一次失败工作副本仍打开时再次 materialize，OneNote 可能把两个路径视为同一 Notebook 或绑定到错误实例。

缓解：fingerprint 级 bundle materialization lock，加上覆盖整个 cache store 的模板内部 Notebook ID claim 与未决 role working-copy lease；打开前枚举/核对已打开 Notebook 的 ID/path，发现同 ID 异路径、两个 role ID 相同或任一 role 无法唯一绑定时立即拒绝。相同 fingerprint/instance 本身不是冲突：每次 validated hit 都 materialize 唯一 working path，并在打开后把 lease 从 template identity 重绑定到实际 live ID；只有实际 ID 集相交或身份仍未确定时才拒绝。2026-08-11 的双 Notebook 真实证据同时观察到成功 materialization 后 live ID 全部重建、两组 working bundle 可并存，以及 activation failure 保留 template ID 时下一次 claim 被拒绝，因而复用必须保持这两个分支而不能退化为 fingerprint/instance 排他锁。

### P0：交互 Recipe 接受任意用户 Notebook 或绕过 Bootstrap

风险：为取得无法程序创建的内容而接受外部 path/ID、复用业务 Notebook，或由普通 scenario 在 cache miss 时隐式进入交互流程，会破坏 disposable ownership、静态权限和 human-gated 边界。

缓解：每个 InteractiveFixtureRecipe/UserAuthoredRecipe 绑定一个显式注册且 `registered_for_all=False` 的具名 bootstrap scenario；场景自己创建全部 fresh role Notebook 并绑定精确 lease。InteractiveFixtureRecipe 只允许用户编辑 checkpoint 指定的严格目标；UserAuthoredRecipe 只允许编辑声明的 authoring zones。普通 scenario 只能返回 `interactive_bootstrap_required`，不得接受外部 Notebook、动态 recipe 名或任意模板路径。

### P1：缓存扩大权限或绕过 fresh 边界

风险：为了 publish/open/repair template 或构建多 role bundle 临时加入额外 lifecycle、Copy、Delete 或 raw XML 权限。

缓解：每个 role 的 Notebook build/open/close 仍只能使用当前 Scenario 声明的静态最小权限闭包和角色化窄 lifecycle wrapper；一个 Scenario 仍最多启动一个 MCP process。Opaque filesystem copy/cleanup 是独立的受控 cache store，不暴露为 MCP tool，也不能在运行中改变 policy。

### P1：Fingerprint 不完整导致错误命中

风险：Description、内容 fixture、manifest schema 或 validator 已变化，但 fingerprint 未变化。

缓解：显式 recipe/evidence schema version；合同测试对所有影响 fixture 的静态字段进行 canonical serialization；变更 recipe 时必须更新版本或使结构化 fingerprint 自动变化。

### P1：本地路径或 Notebook 身份漂移

风险：cache index 指向被移动、替换或同名的 template，或者实际打开路径落到 template 而不是 working copy。

缓解：同时绑定 fingerprint、byte inventory、规范化 template/working path 和 cache root；任一不一致都拒绝，不按名称寻找替代对象。`.one` 只作为关闭状态下的 opaque bytes 复制/清理，绝不解析或修改。

### P2：缓存收益不足以抵消复杂度

风险：opaque clone、打开工作副本和完整 cache-hit 回读与验证的耗时接近重新构建，却引入状态机与清理负担。

缓解：实施前后分别记录 cold miss、validated hit、invalid rebuild 的 MCP calls、bridge calls 和总耗时；若某个 recipe 的复用没有稳定收益，Runner 对它继续默认走 fresh path，但 Recipe 仍保留统一 cache 合同，避免重新分裂类型体系。

## 必须由用户本人运行的真实验收

本节命令是目标 CLI，只有相应实现、静态 policy/allowlist、dry-run 和纯合同测试完成后才可使用；当前未实现前不构成可用命令。下面所有不带 `--dry-run` 的命令都可能创建、打开、修改、关闭 disposable OneNote Notebook 或清理受控 cache entry，只能由用户本人在前台显式运行。Agent、pytest、CI、hook、timer、watcher 或后台任务绝不能执行、串联或间接触发。

每个真实验收先运行同参数的 `--dry-run --json`，确认 role、fingerprint/instance、policy、allowlist、预算、cache 分支和 lifecycle 后，再由用户去掉 `--dry-run`。单次失败立即停止，保持全部工作 Notebook 和 evidence；不得为了完成矩阵而自动继续下一项。

### A. 非交互式 RecipeBase 实现：Fresh、Cold Miss 与 Validated Hit

当前真实进度（2026-08-11，已闭合）：recipe version 2 修复 live capability 合同后，`run-2026-08-11-13-31-57`、`run-2026-08-11-13-33-47`、`run-2026-08-11-13-37-37` 与 `run-2026-08-11-13-39-13` 以同一 fingerprint 分别覆盖 cold build、keep-worksite validated hit、默认 cleanup validated hit 和 fresh。所有 root-only/full-subtree Copy 均 verified/lossless；cached template 未打开且 inventory 不变；默认 cached hit 与 fresh 都精确清理三个目标并 `restored=true`，fresh run 未生成任何 cache runtime artifact；四次 metrics 均已记录。因此本节第 1–5 项已有用户真实证据，性能数据仅作为本机观测，不形成固定加速承诺。

选择一个构建成本较高、可恢复且已经具有真实后端证据的单 Notebook Copy fixture 作为代表，初始建议使用 `copy-page`：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page

.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
```

用户必须确认三条互相独立的真实路径：

1. 无 `--use-cache` 的 fresh run 不 lookup、不读取、不创建或清理 cache；
2. 第一次 `--use-cache` 明确记录 `decision=cold_build`，只在 fresh fixture 完整验证、精确关闭和 inventory 通过后发布，随后另行 materialize 工作副本；
3. 第二次相同命令明确记录 `decision=validated_hit`，不重复 fixture build，并在 mutation 前重新完成 live recipe validation；
4. 三次 Scenario 的业务 after/restore/cleanup 合同均与原 fresh 行为一致，cache master 前后 inventory 不变；
5. 记录 fresh、cold build、validated hit 的 elapsed time、MCP/bridge calls；只有 hit 在复杂 fixture 上表现出稳定收益，README 才能把 `--use-cache` 写成性能建议，而不能承诺固定加速比例。

### B. 多 Notebook Bundle 与跨 Notebook 操作

当前真实进度（2026-08-11，已闭合）：`run-2026-08-11-14-27-08` 证明 version 3 双 role cold build、live validation、关闭发布与重新 materialize 成立，但该 run 随后因 destination snapshot evidence 的 runner bug 失败，不计为 Copy 成功。修复该问题及重名 Page 的 created-target 定位后，`run-2026-08-11-14-54-05` 与 `run-2026-08-11-14-57-01` 使用同一 fingerprint 连续 `validated_hit` 成功；六个 case 均 `verified=true`、`lossless=true`，source/destination Notebook ID 互异，cache template 未打开，每次只有一个 MCP process。前者覆盖默认反向 cleanup、两侧 `restored=true` 与 lifecycle close，后者覆盖 `--keep-worksite` 的六目标及双 Notebook 保留。用户明确接受该证据组合并决定不再补跑，因此本阶段不再保留额外 cleanup/close 或重复 cold-business-run 要求。

当前实现决策：直接把已有 `copy-page` 升级为固定双 Notebook recipe，而不是增加一个仅为 cache 重复业务语义的 `cache-two-notebook-copy`。它使用同一个非交互式 `RecipeBase` 具体实现的 `source`/`destination` roles，不引入多 Notebook 子类；同一个 source Page 完成 `3 个目标范围 × 2 个子树模式` 六次复制。先检查纯 dry-run，再由用户依次运行 cold build 和 validated hit：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
```

用户必须确认 cold build 和 validated hit 均满足：

- source/destination template 与 working paths 分别唯一，两个工作 Notebook ID 互异；
- 两个 cache masters 从未被 OneNote 打开，`opened_template=false` 逐 role 成立；
- 两个 role 都来自同一 fingerprint/instance/run，Copy plan 同时绑定两侧 snapshot；
- 同 Section 与跨 Section 的四个目标只出现在 source working copy 的声明 Section；跨 Notebook 的两个目标只出现在 destination working copy；source Page 子树和两侧无关锚点保持合同状态；
- 成功时按角色化 lease 精确关闭两个 working Notebook；任一失败时保留整个 bundle，不只保留其中一侧。

后续 [TODO 012](012_reconstructive_section_and_section_group_move.md) 的 `move-section` / `move-section-group` 接入 cache 后，用户还必须分别完成一次 `--use-cache` 真实 Move，证明 source 删除仅作用于 working copy、cache master 永不承接 Delete，且 `permanently=false`。TODO 012 未完成不阻塞本 TODO 的双 Notebook Copy cache 基础验收，但容器 Move 不能借用 Copy 结果声称自身已通过。

### C. 并发隔离、真实身份冲突、Keep-worksite 与受控失效重建

当前真实进度（2026-08-11，已闭合）：`run-2026-08-11-19-07-17` 以 `decision=validated_hit` 和 `--keep-worksite` 保留第一组 source/destination working Notebook 与 active lease；在其仍打开时，`run-2026-08-11-19-10-38` 从相同 fingerprint `05a513f7de2fddf635795dcf107e0109b4010b159e30d4d3bec9617170787581`、相同 instance `programmatic-05a513f7de2fddf6` 再次命中。第二个 run 使用不同 run directory 和 working paths，OneNote 为两个 role 重建了与第一组全部互异的 live Notebook ID；六个 Copy case 全部通过，第二组独立 cleanup/restore/close，第一组 lease 仍为 active。这证明 working lease 不是 fingerprint/instance 排他锁，相同 immutable template 可以安全服务多个 live identity 互异的隔离 consumer。

真实冲突分支由另一条连续证据覆盖：`run-2026-08-11-18-46-59` 在 working hierarchy activation 中途失败并保留尚未完成独立 live identity 建立的 working bundle；`run-2026-08-11-18-50-54` 的下一次 claim 因实际 Notebook ID 集相交而在 lifecycle/MCP mutation 前精确拒绝，未关闭、接管或修改旧现场。用户关闭旧 working Notebook 后，`run-2026-08-11-18-51-26` 将遗留 lease reconcile 为 `stale_closed_observed`，随后以 `validated_hit` 完成六 case、cleanup/restore 和 close。`run-2026-08-11-12-33-37` 与 `run-2026-08-11-12-36-16` 还分别完成固定 entry 的精确失效清理与 `invalidated_rebuild`；root-level tombstone 证明目标只位于 managed cache root 的精确 `(fingerprint, instance)` entry，且 containment、ownership、无 reparse point、无 active lease 均成立。

阶段 C 固化的合同是：

1. 相同 fingerprint/instance 可以并发 materialize 多个 run-scoped working bundle；只有 working paths 唯一、每组 role ID 互异且所有 active lease 的实际 live ID 集不相交时才可继续；
2. 任一实际 live ID 冲突、同 ID 异路径、role 内重复 ID 或身份尚未可靠重绑定都必须在业务 mutation 前 fail closed，并保留精确旧 run/path 供用户处理；
3. `--keep-worksite` 保留的是该 run 独立 working bundle 和 active lease，不阻止其他身份互异的 consumer，也不得被后续 consumer 关闭或修改；
4. active lease 必须阻止对应 cache entry 的 invalidation/cleanup；它不禁止从 immutable entry 继续 materialize 身份互异的新 working bundle；
5. 用户关闭失败或保留的 exact working Notebook 后，下一次 claim 才能通过只读 ID/path probe 将其 reconcile 为 stale；不得删除 lease 文件或猜测接管；
6. 固定 `cache-invalidation` Scenario 只能操作自己拥有的 entry，执行 `invalidate → exact cleanup → cold rebuild → publish → materialize`；cleanup failure 必须转为 `cleanup_failed` 并停止。

目标命令：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py cache-invalidation --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py cache-invalidation --use-cache
```

这类真实失效/清理只能在项目级安全决策和 `AGENTS.md` 已明确授权 managed cache 精确 entry 清理后执行；此前只允许纯测试和 quarantine/tombstone 演练。

### D. TODO 004 的具体 InteractiveFixtureRecipe

当前代码与真实证据进度（更新至 2026-08-12）：`interactive-copy-ink-drawing`、`interactive-copy-ui-shape`、`interactive-copy-media-file` 与 `interactive-copy-inserted-file` 均作为 cache-only、Copy-only consumer 注册；只启用 Writes + Experimental Copy，不含 Delete/Move/Permanent Delete/Raw XML。InkDrawing 的最终 run `run-2026-08-11-21-53-24` 通过 `semantic_ink_drawing`，仅对 Position/Size 使用 `1e-4` 容差；UIShape 的 `run-2026-08-11-22-23-29` 通过 `semantic_ui_shape`、`ShapeInfo`/可选 `AnchorPoint` 完整比较和 `0.02` 容差；录像 MediaFile 的 v8 bootstrap `run-2026-08-11-23-21-38` 与双 case consumer `run-2026-08-11-23-23-16` 通过 strict canonical、materialized live validation、同/跨 Section Copy 与人工播放 verdict。用户随后人工删除源 Section并确认两个录像副本仍有效。InsertedFile 的 `run-2026-08-12-12-34-58` 通过 strict canonical、detector/comparator 和用户打开目标附件后的 run-bound verdict。四类均已通过静态代码变更进入生产 `VALIDATED_COPY_CONTENT_TYPES`；未知节点、越界几何和未验证类型仍 fail closed。

[TODO 004](004_interactive_copy_move_content_fidelity_validation.md) 的三个目标 Recipe 子类都必须分别获得用户 bootstrap 和 Copy 证据，不能只运行一个混合场景后把全部类型记为通过。目标具名 bootstrap Scenario 至少包括：

```text
bootstrap-ink-drawing-fixture
bootstrap-shape-fixture
bootstrap-media-file-fixture
bootstrap-inserted-file-fixture
```

FileAttachment/MeetingInfo/Embedded Spreadsheet 不得重新加入注册表或 catalog；排除原因只记录在 [`lesson/copy_content_type_exclusions.md`](../lesson/copy_content_type_exclusions.md)。Embedded Spreadsheet 在观察到真实公开表示前只作为产品能力类别记录，不能凭名称创建 detector。

四类目标的用户验收流程均已执行；InsertedFile 复用此前已经发布的 ready fixture，因此本轮只需执行对应 Copy consumer：

```text
bootstrap scenario --dry-run --json
→ 用户真实 bootstrap，在精确 Canvas 中加入 synthetic 内容
→ close/publish 后立即 materialize 第二组工作副本并 live validate
→ 对应具体 Copy scenario --use-cache --dry-run --json
→ 用户真实 Copy，并确认机器 comparator 与 OneNote UI
```

每个类型必须独立记录 requested/observed/missing/unexpected、对象计数、role/Page、binary/semantic comparator、用户 verdict、Office 环境和 cleanup。lossless 类型必须由静态代码合同明确登记；尚无稳定 comparator 的其他类型仍只能形成 `evidence_only`。Move 不新增逐类别命令或专有类别门禁，直接复用生产 Copy 的 `copy_contract_satisfied` 结论与既有非永久删除安全链。

UI Shape 的两次 discovery 已证明矩形和箭头都公开为 `kind=InkDrawing + ShapeInfo`，箭头另含 `AnchorPoint`；后续 v5 bootstrap/consumer 已冻结这一复合表示并取得真实 Copy 证据。该历史仍说明 detector 和生产 allowlist 只能通过静态代码评审更新，不得从 evidence 动态扩张。

如果保留静态组合 Scenario，它只能在所有组成子类已有独立证据后补充验证组合行为，不能替代任一具体 Recipe 的 bootstrap/Copy 结论。

### E. UserAuthoredRecipe

必须提供一个固定、`registered_for_all=False` 的 `bootstrap-user-authored-fixture` 及其明确 consumer Scenario。用户至少完成以下真实分支：

1. 在声明的 authoring zones 内创建、删除、重命名和重排局部 Section/Page，并加入多种 synthetic Page 内容；冻结为第一个 `template_instance_id`，发布后立即 materialize 并 live validate；
2. 对同一 contract fingerprint 创作第二个不同实例，证明两个 instance 可并存，consumer 必须用精确 instance ID 选择，省略或歧义选择时零 mutation 拒绝；
3. 创建一个全部能力均有稳定 validator 的实例并确认 `state=ready`；
4. 创建一个含未知/未验证能力的实例并确认只能成为 `state=evidence_only`、`mutation_eligible=false`、`move_source_deletion_allowed=false`；
5. 在专门的负向 run 中修改系统保留 marker 或 authoring zone 外对象，预期 bootstrap 非零失败、模板不发布、Notebook 保持打开并保存精确失败证据；
6. 使用 ready instance 运行 consumer 的 `--use-cache` validated hit，确认后续 working mutation 不改变冻结实例或 cache master；
7. 对 UserAuthored entry 执行受控失效后，普通 scenario 只能返回 `interactive_bootstrap_required`，必须由用户重新运行具名 bootstrap，不能自动用旧 working copy 修复。

### F. 全局 CLI 与 `all --use-cache`

所有单场景验收通过后，用户最后运行一次：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py all --use-cache
```

用户确认 `all` 仍只串行执行显式 included scenarios；每个子场景拥有独立 run-dir、working bundle、MCP、lease 和 evidence，cache decision 可各自为 hit/cold build，但不会共享可写状态。未纳入 `all` 的 Interactive/UserAuthored bootstrap Scenario 不能被自动加入或启动。一次 `all` 通过只证明串行编排，不替代上面逐 Recipe、逐内容类型和负向分支的真实证据。

### G. 每次用户验收必须保存的证据

每个真实 run 至少记录并由用户确认：

- run ID、Scenario、具体 Recipe class、recipe version、fingerprint、template instance ID 和全部 Notebook roles；
- `cache_mode=fresh|use_cache`、`decision=fresh|cold_build|validated_hit|interactive_bootstrap_required|invalidated_rebuild`；
- 每个 role 的 template/staging/working resolved path、root containment、byte inventory，以及 `opened_template=false`；
- template Notebook ID、working Notebook ID、全 cache ID claim、同 ID 异路径检查和角色化 lifecycle lease；
- build receipt、fresh live snapshot、role checks、bundle checks、Scenario before/after/restored 或 cleanup 结果；
- cache master 在 Scenario mutation 前后的 inventory 不变证明；
- 失败时的 current step、entry state、保留的工作 Notebook、created IDs、cleanup target/result 和人工接管说明；
- Interactive/UserAuthored run 的 checkpoint、run-bound confirmation、requested/observed capabilities、机器 comparator、用户 verdict、authoring-zone 或越界证据；
- fresh/cold/hit 的 elapsed time、MCP starts、tool calls 和 bridge calls，不从单次数据外推普遍性能结论。

用户确认后，应在本 TODO 的实施进展或具名验收记录中引用对应本地 report/run ID；TODO 004 的逐内容结论还必须同步回 TODO 004。Mock、recording fake、dry-run、旧模板 hash 或 Agent 推断均不能替代上述真实证据。

## 不要求用户运行的纯自动化验收

以下项目必须由 pytest 内的纯单元、合同、recording fake 和受控临时文件系统测试覆盖，不应把可自动判定的重复劳动转嫁给用户。全部 case 都不得访问 OneNote、真实 bridge、真实 MCP server、用户 Notebook 或受管 `.local-validation/fixture-cache/`；临时文件系统 case 只能使用测试进程创建并完整拥有的临时目录。

### 纯合同 Case Catalog

测试实现必须维护结构化、稳定 ID 的 `RecipeContractCase` catalog，而不是把覆盖范围分散在测试函数名、自由文本参数或大块 golden snapshot 中。建议的最小测试模型为：

```python
@dataclass(frozen=True)
class RecipeContractCase:
    case_id: str
    scenario_name: str
    recipe_name: str
    dimension: RecipeContractDimension
    variant: str
    expected_outcome: ContractOutcome
    expected_roles: tuple[str, ...]
```

- catalog 只描述测试输入维度和预期结果，不得携带任意 callback、shell command、外部路径、环境变量覆盖或真实 Notebook ID；
- pytest collector 从唯一的 Scenario registry 枚举每个公开 Scenario 及其唯一 Recipe，再根据 Recipe 的静态特征计算必需维度；catalog 不得成为第二个 runtime Recipe registry，也不得负责运行时 Recipe 选择；
- 每个 case ID 必须全局唯一、可读且稳定，建议形如 `recipe.<recipe-name>.<dimension>.<variant>`；报告必须输出 case ID、Scenario、Recipe、roles、预期和实际结果；
- collector 在测试执行前 fail closed：重复 case ID、未注册 Scenario/Recipe、Scenario 与 Recipe 所有权不一致、缺少必需维度、未知 dimension/variant、非法 role 期望、动态 content-type selector 或任意路径/命令字段均使 collection 失败；
- 必需维度由统一函数根据 `build_mode`、role 数量、是否静态组合、是否为具体 Interactive 子类、是否为 `UserAuthoredRecipe` 等结构化特征计算；不能靠开发者手工勾选 `covered=True` 绕过；
- 新增公开 Scenario、具体 Recipe 子类、Notebook role、bootstrap binding、cache schema 状态或 CLI 分支时，若未同时补齐相应 contract cases，完整性测试必须立即失败；删除或重命名对象时，孤儿 case 也必须失败。

### 每个公开 `RecipeBase` 实现的基础必需维度

以下基础 case 适用于 registry 中每个公开 Scenario 的唯一 Recipe，包括交互 Recipe；不允许只给 `copy-page` 等示例 Recipe 建 happy-path 测试后推定其他 Recipe 等价：

| 维度 | 必须覆盖的纯合同 case |
| --- | --- |
| 所有权与声明 | Scenario 恰好拥有一个 Recipe；Recipe name/fingerprint 唯一且稳定；至少一个唯一 role；profile、fixture parameters、creation tools、manifest keys、validator 与 bundle invariant 都可枚举；静态组合组件无重复/孤儿且不形成第二 registry。 |
| fresh 默认路径 | 未传 `--use-cache` 时执行 fresh build 与 live validation，但 cache lookup/index/read/write/invalidate/cleanup 调用计数全部为零；build/validate 失败保留 evidence 并阻止 mutation。 |
| cache cold 路径 | `--use-cache` miss 后，一般 Recipe 严格执行 build→role/bundle validate→close-all→stage→inventory→publish→materialize；任一步失败均不得产生 `ready` entry。 |
| validated hit | 合法 entry materialize 到新的 working paths，再用当前 live observation 重跑同一 Recipe validator；旧 receipt、旧 report、hash 或 manifest 不能跳过 live validation。 |
| invalid 与兼容性 | recipe/schema/evidence/contract version 变化、role/profile/parameter/invariant 变化、缺失文件、额外文件、inventory/hash 不符、未知状态和历史 schema 均 fail closed；一般 Recipe 仅在精确安全清理后重建。 |
| 不可变与失败保留 | working mutation 不改变 template per-role/bundle inventory；partial build/materialization、validator exception、close failure、publish crash、cleanup failure 与 keep-worksite 都保持精确状态、lease 和接管证据。 |
| manifest/report | fingerprint、instance、role ordering、template/working paths、cache decision、lease、`opened_template=false`、逐 role/bundle checks、invalidation/cleanup 结果完整，且不记录敏感正文。 |
| 职责边界 | Recipe 只能声明、构建和验证 fixture；Recipe 调用 restore/cleanup、Notebook open/close、cache lookup/copy/publish/delete、bridge/subprocess 或 Scenario mutation 时 sentinel 失败。Scenario 负责 mutation/restore/cleanup，Lifecycle 负责 open/close/keep，Cache runtime 只在全部 roles 已关闭后 publish。 |

### 按 Recipe 特征追加的必需维度

除上述基础矩阵外，collector 必须为下列特征叠加相应 case；一个 Recipe 同时具有多个特征时取并集，不能只选择其中一组：

| Recipe 特征 | 必须追加的纯合同 case |
| --- | --- |
| 多 role / 跨 Notebook | role 顺序 canonicalization、重复/缺失/额外 role、两个 role 映射到同一 ID、同 ID 异路径、部分 materialization、任一 role open/close/validate 失败、bundle invariant 失败、source/destination plan digest 任一侧 stale、整个 bundle lease/失败保留；同时证明 common runtime 不按 Notebook 数量分派。 |
| 静态组合 Recipe | 组件清单固定、组件 Recipe 唯一归属、profile/allowlist/budget/invariant 的确定性合并、冲突拒绝、逐组件证据可追踪；拒绝动态 CLI content string、共同基类 content-type switch 和第二 registry。 |
| 每个具体 `InteractiveFixtureRecipe` | bootstrap binding 存在且 `registered_for_all=False`；普通 miss/invalid 只返回 `interactive_bootstrap_required`，不调用 scaffold、不读 stdin；scaffold 只能由绑定的 bootstrap Scenario 调用；checkpoint timeout/EOF/cancel/错误确认短语、requested content 缺失/额外/错位/未知、机器 comparator mismatch、负向 human verdict、任一 role 未关闭和 `--keep-worksite` 均禁止发布；成功发布后必须从母本 materialize 第二份工作 bundle 并再次验证，且母本从未打开。 |
| TODO 004 的当前具体范围 | `InkDrawing`、`UIShape`、`MediaFile`、`InsertedFile` 分别具有 detector/comparator、自己的 Canvas/manifest/capability 断言和独立 case IDs，且四类真实 Copy 证据与静态 allowlist 评审均已闭合。InsertedFile 复用既有 ready cache，并由 `run-2026-08-12-12-34-58` 完成 comparator/人工 verdict；FileAttachment/MeetingInfo/Embedded Spreadsheet 已排除，不得被旧矩阵重新引入。Embedded Spreadsheet 尚无公开表示证据，不得臆造 `kind`。 |
| `UserAuthoredRecipe` | zone 内允许的创建/删除/重命名/重排、zone 外修改拒绝、系统 marker 修改拒绝、冻结后不可漂移、同一 fingerprint 多 instance 共存、精确 instance 选择、缺失/歧义/未知 instance 拒绝、`ready` 与 `evidence_only` 分级、未知能力阻止 mutation/Move source deletion、invalid instance 要求重新 bootstrap。 |

### Cache、CLI 与非执行式边界

以下是 Recipe catalog 的共享依赖合同，也必须作为稳定 case 进入同一自动化验收报告：

- canonical fingerprint 覆盖完整 identity、instance identity、role ordering、manifest schema、plan digest 和序列化确定性；随机 token、绝对 run path、运行时 Notebook ID、时间戳、类文件路径或 mtime 不得影响 identity；
- cache index 状态机覆盖 staging/ready/evidence-only/invalid/cleanup-failed/tombstone、原子 publish/replace、fingerprint lock、instance/working lease、崩溃恢复、重复发布、并发 claim 和历史 schema compatibility；
- 路径安全覆盖 resolved containment、空路径、root 本身、父级逃逸、大小写/规范化差异、symlink/reparse-point/junction、TOCTOU 再校验、精确 instance cleanup，以及任意 path/ID/name/fingerprint selector 拒绝；
- recording fake 覆盖 hit、一般 miss、Interactive/UserAuthored miss、invalidation、cleanup failure、partial bundle、ID remap、role collision、open/close failure、lock conflict、crash lease、template-open sentinel 和 working-mutation inventory sentinel；
- parser case 覆盖每个公开 Scenario 的默认 fresh 与 `--use-cache`、`all --use-cache` 串行透传、重复/错误位置参数拒绝，以及废弃别名 `--reuse-fixture-cache` 不存在；
- dry-run catalog 覆盖无 cache、cold/hit/invalid/bootstrap-required 的计划输出、单/多 role、静态组合、Interactive、UserAuthored、instance selection 和 cache invalidation；sentinel 必须证明 dry-run 零目录创建、零真实 cache lookup/read/write/cleanup、零 stdin、零 MCP、零 bridge、零 OneNote 和零 lifecycle 副作用；
- 文档中的目标命令必须能非执行式映射到已注册的 dry-run/contract case，且不存在文档声明但 registry/case catalog 不认识的 Scenario。

纯测试分为四层：静态声明/registry 测试、无文件 I/O 的 recording fake 合同、仅使用 pytest 临时目录的 cache 文件系统合同、被强制注入 `--dry-run --json` 的 CLI 合同。完整自动化测试集必须包含一个 catalog completeness test，输出各 Recipe 已要求/已实现/缺失的 case IDs，并在缺少任何基础或特征追加维度时失败。

这些纯测试通过只能证明合同、状态机和 fail-closed 逻辑，不能把任何真实 OneNote/cache Scenario 标记为已通过，也不能替代前述必须由用户本人执行的 A–F 真实验收。

## 实施步骤

1. 保留并扩展现有 `fixture_recipes.recipe_base.RecipeBase`，把原 `common.fixture_models.FixtureRecipe` 的合同迁入并统一改名为 `RecipeBase`，再增加至少一个 role 的 `notebook_roles` 集合、bundle context/build receipt/live observation/validation report；Scenario/runtime/type annotation 不再公开第二个 `FixtureRecipe` 基础类型；迁移全部单 Notebook recipe 使用同一集合模型，并以纯测试禁止单 Notebook runtime 特例或 `MultiNotebookRecipe`；
2. 为结构化 cache identity、公共 canonical fingerprint、bundle entry、per-role/bundle materialization evidence、状态机和 template/working 路径约束增加纯单元测试；
3. 在 `run.py` 公共 parser 和全部具名 Scenario 上增加全局 `--use-cache`，并让 `all` 接受后串行透传；无 flag 时证明零 cache access，有 flag 时一般 Recipe 执行 hit-or-build，交互 Recipe miss 返回 bootstrap requirement；不得保留 `--reuse-fixture-cache` 等同义参数；
4. 增加 `InteractiveFixtureRecipe`、`UserAuthoredRecipe`、`interactive_bootstrap_required` 和显式 bootstrap scenario 绑定校验；registry 必须拒绝未绑定 scenario、动态 recipe selector、允许进入 `all`、缺少 live validator、缺少 authoring-zone 边界或允许外部模板路径的交互 Recipe；
5. 在实施任何 Notebook 目录复制或 invalid entry 清理前，先形成项目级安全决策，并同步根级与 manual-validation `AGENTS.md`：只允许关闭状态下 disposable template bundle 的 opaque copy，以及 managed cache root 内精确、未打开、无 lease entry 的受控清理；
6. 实现 local bundle cache index、随机 staging、per-role/bundle byte inventory、原子 publish、fingerprint 级独占锁、全 cache internal-ID claim、角色化 working-copy lease、root-level tombstone 和 crash recovery evidence；
7. 扩展 branch-free fixture runtime：一般 cold path 为 `fresh bundle build → validate roles/bundle → close all → stage copy → inventory → publish → materialize`，hit path 为 `lookup → validate bundle entry → materialize all roles`，交互 Recipe miss path 只返回 `interactive_bootstrap_required`；common 层不得出现 scenario 名称、Recipe 子类名称或 Notebook 数量分派；
8. 扩展 lifecycle wrapper 接受冻结的 role→working-path 集合，逐 role 打开后回读实际路径和 ID，断言路径只指向 working copy、全部 role ID 互异，并拒绝全 cache 范围内的同 ID 异路径冲突；
9. 已将现有 `copy-page` 直接升级为 source/destination 双 role Recipe 并完成真实 A/B 证据；不再增加重复业务语义的 `cache-two-notebook-copy`；
10. TODO 004 当前保留 InkDrawing、UI Shape、MediaFile 和 InsertedFile：四类均已有精确 bootstrap、cache-only Copy consumer、机器 comparator、人工 verdict 与静态生产 allowlist 评审；InsertedFile 的 `interactive-copy-inserted-file` 共用既有 bootstrap recipe/cache，并由 `run-2026-08-12-12-34-58` 完成真实 Copy 闭环。FileAttachment/MeetingInfo/Embedded Spreadsheet 专属入口已排除。所有相关 scenario 均不进入 `all`，common runtime 中不存在按 CLI 内容类型扩权的 switch；
11. 实现一个 `UserAuthoredRecipe` 和独立具名 bootstrap scenario，覆盖多 authoring zones、用户新增/删除/重排局部对象、实例冻结、多个 `template_instance_id`、`ready`/`evidence_only` 分级和显式实例选择；该 scenario 不进入 `all`；
12. 实现固定、`registered_for_all=False` 且只操作自己受管测试 entry 的 `cache-invalidation` Scenario，覆盖真实版本/inventory 失效、精确 cleanup、cold rebuild 和可选 cleanup-failure 停止；它不接受任意 fingerprint、instance 或路径参数；
13. 实现稳定 ID 的 `RecipeContractCase` catalog 和 pytest collection completeness gate：从唯一 Scenario registry 枚举 Recipe，按基础、多 role、静态组合、具体 Interactive 子类和 UserAuthored 特征自动计算必需维度；重复/孤儿 case、所有权不一致、非法字段或任一缺失维度都在 collection 阶段 fail closed；
14. 按 catalog 为 hit、一般 miss、两类交互 Recipe miss、版本/实例/role 集合变化、inventory/hash 不匹配、路径越界、reparse point、清理失败、同 ID 异路径、role ID 冲突、锁冲突、部分 bundle 构建、人工编辑、keep-worksite 和崩溃 lease 增加 recording fake/临时文件系统合同；加入职责边界 sentinel，并证明所有 cache masters 从未被打开且 working mutation 不改变任一 role 的 byte inventory；
15. 把 role、template/working path、缓存决策、fingerprint、template instance、authoring zones、inventory、lease、`opened_template=false`、role/bundle 验证、invalidation 与逐项清理结果加入运行时 manifest/report，但保持内容无敏感正文；contract case ID 只进入纯测试报告，不污染真实运行时合同；
16. 为 dry-run catalog 增加无-cache 基线、所有公开 Scenario 的 `--use-cache`、`all --use-cache`、单/多 role cache、InteractiveFixtureRecipe、UserAuthoredRecipe 和 `cache-invalidation` 变体；harness 仍强制 `--dry-run --json` 并证明不创建、读取或清理 cache，不读取 stdin、不启动 MCP、不访问 OneNote；
17. 同步 manual-validation AGENTS、README、TODO 004、开发验证文档和当前架构文档，明确所有 recipe 都使用 Notebook bundle/cache 合同，日常复杂 fixture 开发推荐 `--use-cache`，两类交互 Recipe 由各自具名 bootstrap scenario 发布实例，每次真实验证只操作 role 工作副本；
18. 运行 catalog completeness test、manual-validation 纯测试与完整 pytest，并保存按 Recipe/维度汇总的 case report。真实 cold rebuild、InteractiveFixtureRecipe/UserAuthoredRecipe bootstrap、hit materialization 或 forced invalidation 只能由用户本人显式执行并确认。

## 非目标

- 不缓存或复用用户业务 Notebook；
- 不引入 `MultiNotebookRecipe`、按 Notebook 数量分派的 runtime 分支或第二套多 Notebook cache；单/多 Notebook 必须共用同一 role collection 合同；
- 不让普通 scenario 在 InteractiveFixtureRecipe/UserAuthoredRecipe miss 时自动进入交互 bootstrap，也不接受任意外部 Notebook ID/path、动态 recipe 名称或运行后的 working copy 作为模板；
- 不让 OneNote 打开、注册或修改 cache master；cache master 只作为关闭状态下的不可变 Notebook 模板；
- 不解析、编辑或重写 `.one` 文件；模板发布和 materialization 只允许对关闭状态下的 disposable Notebook 目录做 opaque byte-for-byte copy；
- 不删除 managed cache root 之外、不能证明归属的、已打开的、存在 lease 的 template/working Notebook，也不提供通用 Notebook 删除能力；
- 不引入 Graph、OneDrive、SharePoint、Azure、OAuth、远程对象存储或遥测；
- 不让 pytest、CI、hook、import、timer、watcher 或 Agent 启动真实 cache build/hit mutation；
- 不让任何 recipe、fingerprint、role、policy 或 Scenario 共享同一个可写 working copy，也不允许具有同一实际 live Notebook ID 的不同路径副本同时打开；相同 fingerprint/instance 只有在每次 materialize 到唯一 run-scoped working paths、实际 live ID 全部互异且 lease 独立时才允许并发消费；
- 不因 template cache hit 跳过 working-copy snapshot、recipe validator、health check 或 before/after evidence；
- 不把 mutation 后的 working copy 回写、合并或晋升为 template；
- 不把性能优化描述为新的 OneNote capability 证据。

## 完成定义

- 每个公开 recipe 都使用稳定、无 I/O、可审查且至少包含一个 role 的 Notebook bundle cache identity；单 Notebook recipe 与多 Notebook recipe 共用相同接口、runtime、cache entry 和测试模型，仓库中不存在 `MultiNotebookRecipe` 或按数量分派的第二路径；
- 现有 `fixture_recipes.recipe_base.RecipeBase` 被保留并成为唯一 Recipe 基类；原 `common.fixture_models.FixtureRecipe` 合同已迁入并统一改名，Scenario/runtime/type annotation 不再暴露并行的 `FixtureRecipe` 基础类型；现有一般 fixture 创建全部继续由对应 Scenario 的唯一 `RecipeBase` 具体实现接管，所有 RecipeBase 实现固有支持 cache 和多 Notebook bundle，公共 canonical builder 从完整结构化 identity 计算 fingerprint，recipe 不自行管理 index、文件复制、锁、lease 或清理；
- `run.py` 的全部具名 Scenario 和 `all` 都接受唯一全局选项 `--use-cache`；默认无 flag 时零 cache access，传入时一般 Recipe 确定性执行 validated hit 或 cold build→publish→materialize，`all` 串行精确透传，且不存在 `--reuse-fixture-cache` 等别名；
- manual-validation README 和开发指南把 `--use-cache` 作为反复调试复杂 fixture 的推荐方式，同时明确 fresh/cold 对照、缓存实现验收和发布前最终验证应省略该选项；CLI 默认值继续保持 fresh；
- `InteractiveFixtureRecipe` 是抽象交互基类；TODO 004 当前的 InkDrawing、UIShape、MediaFile、InsertedFile 分别拥有精确 Canvas、role/profile/manifest/live-validator、机器 comparator、人类 verdict 和失败证据。UIShape 保持真实 `InkDrawing + ShapeInfo` 复合表示；FileAttachment/MeetingInfo/Embedded Spreadsheet 已排除；不得在基类/common runtime 中按动态内容类型分派，尤其不得为尚无公开表示证据的 Embedded Spreadsheet 猜测 `kind`；
- 每个具体交互 Recipe 都由具名 bootstrap Scenario 或显式静态组合 Scenario 拥有；普通 scenario 的 miss/invalid 返回 `interactive_bootstrap_required`，只有用户显式运行且不进入 `all` 的具名 scenario 才能发布或重新发布；动态 CLI 内容字符串不得创建未注册的 Recipe 组合；
- `UserAuthoredRecipe` 继承 InteractiveFixtureRecipe，并支持受控 authoring zones、用户创建/删除/重排局部对象、实例冻结、多个显式 `template_instance_id` 以及 `ready`/`evidence_only` 分级；它不得接受用户业务 Notebook、任意外部 path 或 zone 外修改；
- 两类交互 Recipe 的 bootstrap 都使用本次创建的 fresh disposable role Notebook、精确 checkpoint 和 synthetic 内容边界；timeout、EOF、取消、越界编辑、验证失败、任一 role 未关闭或 `--keep-worksite` 保留打开时均不得发布 `ready`；
- 每个 `ready` entry 都是全部 role 已关闭、不可变、具有 per-role 与 bundle byte inventory 的固定格式模板 bundle；本地 cache index/entry 使用受控根目录、原子整体 publish、精确路径、独占锁和角色化 lease，不接受任意外部路径或名称目标；
- 非交互式 `RecipeBase` 实现的 cold miss 只有在完整 bundle build + role/bundle snapshot validation + close all + staging inventory 通过后才发布 `ready`；交互 Recipe 只有 bootstrap/instance-freeze 闭环满足同等发布门才可发布；含未知或未验证内容的 UserAuthoredRecipe 实例最多为 `evidence_only`，部分失败或部分 role entry 永不命中；
- 每次 cache hit 都把全部 role 原子复制到唯一的 run-scoped working paths；lifecycle 证据逐 role 证明 OneNote 实际打开的是对应 working copy，且 `actual_path == working_path`、`actual_path != every template path`、`opened_template=false`，全部 role Notebook ID 互异；
- 每个 role working copy 在 mutation 前重新捕获 snapshot 并通过 role checks，整个集合再通过 recipe 的 bundle validation；template artifact、byte hash、构建 receipt 或旧验证结果不能单独替代 live validation；
- 跨 Notebook Copy/Move 的 plan digest 同时绑定 source/destination role snapshot、路径、Notebook ID 和目标父级；任一侧变化均 stale，失败保留整个工作 bundle，mutation 永不触及 cache master；
- working mutation、失败、keep-worksite 和成功后的清理都不改变任何 role cache master；前后 per-role/bundle byte inventory 证明模板保持不变；
- template 失效时只清理受控根目录内经过 containment、ownership、reparse-point、open-state 与 lease 检查的精确 fingerprint/instance entry；非交互式 `RecipeBase` 实现随后重新构建并原子发布，InteractiveFixtureRecipe/UserAuthoredRecipe 转为 `bootstrap_required` 等待用户具名 scenario；清理失败转为 `cleanup_failed` 并停止，绝不原地覆盖；
- 项目级安全决策和相应 `AGENTS.md` 规则已明确授权上述狭窄 opaque copy/定点清理边界；若未获授权，则实现只能 quarantine/tombstone，不能宣称本 TODO 完成；
- 任一即将使用的 Notebook ID 已由另一条 working path 的 active lease 占用、身份仍未可靠重绑定，或本次 bundle 内两个 role 被解析为同一 ID 时，新的 materialization fail closed；仅有同 fingerprint/instance 的 active lease 不构成冲突，但必须阻止该 cache entry 的 invalidation/cleanup；真实 disposable 证据覆盖 OneNote 对单/多 Notebook 克隆身份的行为；
- cache 复用不增加 MCP 进程、不动态扩张 policy/tool allowlist、不启用 raw XML，也不改变 HUMAN-GATED 真实执行授权；
- dry-run case 覆盖所有公开 Scenario 的 `--use-cache`、`all --use-cache`、单/多 role cache、InteractiveFixtureRecipe、UserAuthoredRecipe、实例选择、template/working paths 和 miss/hit/bootstrap-required 计划，但 dry-run 不读取真实 cache 或声称实际命中，并由 sentinel 证明零目录、零 cache lookup/cleanup、零 stdin、零 MCP、零 bridge 和零 lifecycle 副作用；
- 每个已注册公开 Scenario/Recipe 都有全局唯一、稳定 ID 的 `RecipeContractCase`；pytest collector 从唯一 Scenario registry 自动计算基础以及多 role、具体 Interactive 子类、UserAuthored 特征追加矩阵，缺失/重复/孤儿 case、所有权不一致或非法字段均在 collection 阶段 fail closed；InkDrawing/UIShape/MediaFile/InsertedFile 分别具有 detector/comparator 正负 case；
- 纯合同 sentinel 明确证明 Recipe 不执行 Scenario mutation/restore/cleanup、Notebook open/close、cache lookup/copy/publish/delete、bridge 或 subprocess；recording fake/临时文件系统 case 覆盖单/多 role hit、一般 miss、两类交互 Recipe miss/bootstrap、UserAuthored instance freeze/selection、invalidation/exact cleanup/rebuild/materialization/path assertion/ID conflict/lock/recovery，catalog completeness、manual-validation 纯测试与完整 pytest 全部通过；
- 用户本人按本文件 A–F 矩阵完成并确认：一般 Recipe fresh/cold/hit、相同 entry 的多 working-bundle 并发隔离、实际 Notebook ID 冲突拒绝与关闭后恢复、`cache-invalidation` 精确清理重建、多 Notebook cold/hit、TODO 004 当前 InkDrawing/UIShape/MediaFile/InsertedFile 的独立 Copy（前三类另含独立 bootstrap，InsertedFile 复用既有 ready fixture）、UserAuthored ready/evidence-only/多实例/越界拒绝、`all --use-cache` 串行回归；证据确认所有 cache masters 从未被 OneNote 打开，Agent 未执行任何真实场景；
- manual-validation AGENTS、README、TODO 004、开发验证文档、当前架构文档、两类交互 bootstrap scenario 文档和 TODO 索引与最终实现一致。
