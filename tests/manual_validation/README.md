# OneNote Manual Validation — HUMAN-GATED / ISOLATED / LEAST-PRIVILEGE

> [!CAUTION]
> 本目录只承载由用户本人显式启动的真实 OneNote mutation 验证。Agent、CI、pytest、hook、安装脚本、timer、watcher、前台或后台任务不得执行真实 scenario。每次运行必须创建全新隔离 Notebook，并使用 scenario 级静态最小权限。智能体的强制行动边界见本目录的 [AGENTS.md](AGENTS.md)。

## 公开接口：扁平 Scenario 与特殊 `all`

`run.py` 后通常直接接一个具名 scenario。没有 `validate` 分组，也没有公开的 `inspect`、`read`、`report`、`suite` 或其他辅助 action。`create` 是正式的 fixture-only scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename
.venv\Scripts\python.exe tests\manual_validation\run.py create
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-page
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section-group
.venv\Scripts\python.exe tests\manual_validation\run.py delete
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section-group
.venv\Scripts\python.exe tests\manual_validation\run.py copy-notebook
.venv\Scripts\python.exe tests\manual_validation\run.py move-page
```

每个具名 action 都可显式保留已验证的操作现场，供 OneNote UI 人工验收：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-page --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --keep-worksite
```

`--keep-worksite` 会隐含保持源 Notebook 打开，并在成功 read-back 验证后保留该 action 的现场：`rename/reorder-page/reorder-section/reparent-page/reparent-section/reparent-section-group` 跳过反向恢复，Copy 跳过目标 cleanup，`create/delete/move-page` 保留其原本最终状态以供查看。精确目标 ID、原/现 predecessor、现场状态和人工清理说明写入 `worksite.json`。Page reparent 若由 OneNote 重映射 ID，会同时记录 `target_id`、`current_target_id` 与完整 `id_history`。该选项不会扩权；Copy 场景反而从 policy/tool allowlist 移除不再需要的 Delete/Close cleanup 权限。默认不传时仍执行各 scenario 原有的 restore/cleanup 与生命周期策略。`reorder-section`、`reorder-section-group`、`reparent-page` 和 `reparent-section-group` 不进入 `all`，但仍全部进入注册 dry-run 自动测试。

所有会通过 COM 复制 Page XML 的具名场景（四个 Copy 层级以及
`move-page`）都自动创建两页组成的完整 Page fixture。Section/Group/Notebook Copy 与 Move 使用通用名称；Page Copy 为便于 UI 对照，使用等价的编号名称：

- `Rich-Page` / `01-Source-Parent`（父页）：只含已确认的 `Outline/RichText/Table/Image`，使用严格 canonical 验收；
- `List-Tag-Page` / `02-Source-Child`（子页）：程序通过受限 HTML 自动生成三个编号/项目符号与 To Do 标签混合项（完成、未完成、完成），使用 `semantic_list_tag` 验收。

`copy-page` 的 recipe version 4 是一个 `source`/`destination` 双 Notebook bundle。同一个 `source/Source/01-Source-Parent` 依次执行六个独立 case：同 Section、同 Notebook 跨 Section、跨 Notebook 三种目标范围，各自再覆盖省略 `include_descendants` 的 root-only 与显式 `include_descendants=true` 的完整子树。每个 case 都有唯一目标名、独立稳定 plan 和 read-back；root-only 只允许 Parent 进入 `id_map`，subtree 必须让 Parent/Child 都进入 `id_map` 并保持相对层级。跨 Section 与跨 Notebook destination 各预置一个与源 Child 同标题、不同正文的 manifest-bound anchor；六个 case 都要求新 target IDs 不复用源或 anchor ID，并证明 anchors 的正文 hash、order、level 与 parent 不变。跨 Notebook 目标只能出现在 `destination/Cross-Notebook-Destination`；源 Parent/Child 和两侧既有对象在六次操作中保持不变。默认按反向 case 顺序清理六个根目标并同时验证两个 Notebook 恢复；`--keep-worksite` 则保留全部六个目标和两个 working Notebook 供 UI 对照。整个过程不暂停、不要求用户编辑，也不启用 raw XML。第二层忽略 COM 重新编号 `TagDef`、列表序号状态和 Outline 布局重排，但仍严格比较可见文本、列表种类、标签类型、完成状态和二进制内容。`List/Tag` 已进入 validated/lossless allowlist；这表示其保真结论由 `semantic_list_tag` 而不是 canonical XML 相等来证明。下一阶段 Copy 内容取证只聚焦 `InkDrawing`、OneNote UI `Shape` 和 `MediaFile`（在线视频）；`MeetingInfo` 不属于验证范围。

唯一特殊入口 `all` 会按显式 `included_in_all` 资格的顺序串行启动其中的 scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

唯一注册表对象位于 `scenarios/common/registry.py` 的 `SCENARIO_REGISTRY`。每个场景类使用 `@SCENARIO_REGISTRY.register` wrapper；`scenarios/__init__.py` 按审查后的固定顺序导入所有公开场景，导入时自动完成实例注册。Registry 本身不导入具体场景，也不维护第二份构造列表。新增公开 scenario 默认 `included_in_all = False`；只有经过稳定性和权限审查才可改为 `True`。`get_all_scenario_names()` 只表示用户真实 `all` 批处理资格，不能用于 pytest collection。

每个 Scenario 同时显式持有一个 `fixture_recipe`。场景专属 Description、构建与 validator 位于 `scenarios/fixture_recipes/<scenario>.py`；`common/fixture_runtime.py` 只负责 recorder、snapshot、通用 profile 检查和证据持久化，不按 scenario 名称分派。Recorder 在每个精确 ID 创建后增量写入 pending manifest；中途失败会保留已登记 ID、lifecycle lease 路径、Notebook 路径和 failed validation handoff。Copy/Move 只共享不含 scenario 名称分支的 layered Page component。

所有 recipe 统一继承 `fixture_recipes.recipe_base.RecipeBase`；原并行的 `FixtureRecipe` Protocol 已移除。Recipe 以有序 `notebook_roles`、profile、fixture parameters、manifest keys、creation tools、validation conditions 和 bundle invariant 形成无 I/O 的 canonical SHA-256 fingerprint。单 Notebook 是仅含 `source` role 的普通 bundle，不存在 `MultiNotebookRecipe` 或按 Notebook 数量分派的 cache registry。

普通具名 Scenario 默认仍使用 fresh fixture，并且不查询、创建、失效或清理 cache。重复调试复杂 fixture 时可显式使用：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py all --use-cache --dry-run --json
```

`--use-cache` 只改变 fixture 来源：validated hit 把关闭的 immutable template opaque-copy 到本次 run 的 role-specific working 路径。lifecycle 为每个 role 使用独立 lease（`source` 保持 `lifecycle-lease.json`，其他 role 使用 `lifecycle-lease-<role>.json`），先证明实际打开路径是 working path、不是任一 template path，并立即把全部实际 working Notebook ID/name/path 写入 bundle lease。随后逐 role、逐级调用 `OpenHierarchy` 打开 SectionGroup 和 `.one` Section：先用绝对 working path 与空 relative ID，必要时回退到文件名与精确 parent ID；不能把绝对 path 与非空 parent ID 混用，也不能把“Notebook shell 已打开”或 COM 仅返回 object ID 误当成内容层级已加载。每次都必须回读 actual parent；若全局 hierarchy snapshot 暂时不可见，还必须对同一返回 ID 做 exact-self 回读，严格证明类型、名称、非回收站状态和 parent，随后仍执行完整 live Recipe validation。Working Notebook/Section/Page ID 允许由 OneNote 重建，但必须按 role 内唯一的 Notebook-relative 类型化结构地址形成 old→live ID evidence，之后所有 validation/mutation 只使用 live ID。Programmatic miss 先构建并 live-validate 完整 fresh bundle，精确 close-all，生成逐 role per-file SHA-256 inventory 并原子发布，再从发布物 materialize 完整 working bundle；旧 receipt/hash 不能代替全部 role 的 live validation。多 Notebook 名称在 scenario 后增加 `source`/`destination` role。任一 working-copy open/activation 失败都保留整个 working bundle、逐 role lease 与 `materialized-hierarchy-open[-<role>].json`，lease 必须绑定已实际打开的 live working Notebook ID；这类 run-local 失败不污染已验证的 immutable template。active lease 冲突必须报告精确旧 run ID 和 working paths。ID rebind 或 live validator 失败仍会 quarantine exact entry。`--keep-worksite` 只保留 working bundle 及 active lease，不写回 template。

每个命令在 dispatch 时只读取一次主机本地时区，并冻结 run identity。Notebook、默认 run 目录以及 Copy/Move 目标名称共享 Windows-safe 的本地显示时间，例如 `2026-08-11-11-05-49`。完整本地 ISO 时间、UTC offset 和时区名称仍保存在 `run_identity`；JSON 中的 `created_at`、`failed_at`、`closed_at` 等事件字段仍使用 UTC ISO-8601。immutable template 继续使用内部 `template-notebook` 目录名，不作为 OneNote Notebook 打开。

Cache 固定为未纳入版本控制的 `.local-validation/fixture-cache/`。只有带 managed marker 的该根目录可被 cache runtime 操作；失效清理只允许精确 `(fingerprint, template_instance_id)` entry，并要求 root containment、ownership、无 reparse point、source 已关闭且没有 active working lease。`.one` 和 `.onetoc2` 只作为 opaque bytes 复制/散列，绝不解析、编辑或回写。模板从不由 OneNote 打开。

Cache lookup 会区分真正不存在的实例与目录仍被保留的 `invalid` entry。历史上仅因 working-copy `materialized-open` 阶段被误隔离的 entry，可在原始 validation 与 byte inventory 重新通过时恢复；其余 `invalid` entry 必须先在 fingerprint lock 内通过上述安全门限执行精确清理，再以 `decision=invalidated_rebuild` 重建。该检查在首次 lookup 与 programmatic publish 前都会执行，避免并发隔离再次退化为发布冲突。`cleanup_failed`、缺失 ownership metadata、未知状态、active lease 或 source 仍打开都会阻止重建；publish 始终拒绝覆盖任何现有实例。

2026-08-11 用户真实验证：layered Copy recipe version 2 将 fixture/live validator 与 Copy plan 统一到 live Page XML capability projection 后，`run-2026-08-11-13-31-57`、`run-2026-08-11-13-33-47`、`run-2026-08-11-13-37-37` 和 `run-2026-08-11-13-39-13` 使用同一旧版单 role `copy-page` fingerprint，依次覆盖 `decision=cold_build`、带 `--keep-worksite` 的 `decision=validated_hit`、执行默认 cleanup/restore 的 `decision=validated_hit`，以及带 `--keep-notebook` 的 `decision=fresh`。四次的 root-only case 都以 `strict_canonical` 验证单页 RichText/Table/Image，full-subtree case 精确映射父子两页，并分别以 `strict_canonical`、`semantic_list_tag` 验证父页和 List/Tag 子页；全部 Copy report 均为 `verified=true`、`lossless=true`，没有 issue 或 skipped content。cached run 证明 `opened_template=false`、template inventory 不变；默认 hit 与 fresh 都精确清理三个 Copy 目标并 `restored=true`，前者关闭 working Notebook，后者仅按 `--keep-notebook` 保留已恢复的 fresh 源 Notebook，且未生成 cache runtime artifact。四次都只启动一个 MCP process；总耗时/bridge calls 分别为 90.271 秒/279、66.802 秒/210、86.440 秒/274 和 84.381 秒/237。该矩阵闭合了 TODO 014 的单 role A 验收，但单机观测不能推广为固定性能提升比例。

2026-08-11 用户真实验证：recipe version 3 的双 Notebook、六 case 合同使用 fingerprint `ad0bf5be9c5eee60d0dfdebfca6cfa27a3dc5ae223f4dcb7327b5cee24736212`。`run-2026-08-11-14-27-08` 完成 cold build、逐 role live validation、关闭发布和重新 materialize，随后在 Copy 前因 runner 缺少 destination snapshot evidence 失败；该问题及重名 Page created-target 定位问题修复后，`run-2026-08-11-14-54-05` 与 `run-2026-08-11-14-57-01` 连续以 `decision=validated_hit` 完成全部六 case。两次运行的每个 Copy report 均为 `verified=true`、`lossless=true`，source/destination Notebook ID 互异，`opened_template=false`，且各自只启动一个 MCP process。前一次按默认语义反向清理六个根目标、两侧 `restored=true` 并关闭 working bundle；后一次以 `--keep-worksite` 保留全部六个目标和两个 working Notebook。用户确认不再补跑，TODO 014 阶段 B 据此闭合。

2026-08-11 TODO 015 增强复验：`run-2026-08-11-15-41-20` 的同标题 Create 返回两个 fresh、互异且 allocated/read-back 一致的 ID，正文独立可读，并完成默认非永久 cleanup、restore 和 close；`run-2026-08-11-15-43-26` 的 Move 返回两个 fresh target、`verified=true/lossless=true`、anchor unchanged，之后才按叶到根非永久删除源并关闭 Notebook。v4 `copy-page` 的 `run-2026-08-11-15-46-34` 暴露空 selection T 比较误报；`run-2026-08-11-16-06-07` 随后暴露同一占位符因转换顺序造成“目标标题 + 原标题”；`run-2026-08-11-16-11-01` 再暴露最终 restore 对无关 Description Page 后台重序列化比较过宽。三项均按严格保护对象边界修复。最终 `run-2026-08-11-16-18-20` 以同一 v4 fingerprint validated-hit，六个 case 按 `1/2/1/2/1/2` 映射 9 个 fresh、互异且与 source/anchors 不相交的 target；全部 `verified=true/lossless=true`，source/anchors 不变。默认反向清理 9 个 target 后 `restored=true`，source/destination 双 Notebook 均 closed，cache template inventories unchanged；全程只启动一个 MCP process。TODO 015 据此闭合。

当前保留的具体交互 recipe 和一个 bounded UserAuthored recipe 各有固定、不会进入 `all` 的 bootstrap Scenario。它们创建 fresh disposable Canvas/authoring zones，写 run-bound checkpoint，以有界 timeout 等待用户本人添加 synthetic 内容并给出精确 verdict；成功后关闭源、发布模板、再 materialize 第二份 working copy并 live validate。`--keep-worksite` 明确阻止发布。Agent、pytest、CI、hook 和后台进程不得执行这些真实命令。

交互 detector 只接受公开 Page 对象模型的 `kind`，并把 `Outline`/`OE` 作为结构支撑节点；请求类型必须精确匹配。TODO 004 的当前产品范围只继续尝试 `InkDrawing`（墨迹）、OneNote UI `Shape`（形状）和 `MediaFile`（在线视频）。`Shape` 目前只是 UI 内容类别，尚无证据证明公开对象模型存在字面量 `kind=Shape`；对应 bootstrap 必须先保存 content-free projection 并确认实际表示，未知或不同表示继续 fail closed。`FileAttachment` 的专属 bootstrap/Recipe 已删除，因为当前 OneNote GUI 多次只生成 `InsertedFile`、无法形成独立可验证 fixture；`MeetingInfo` 的专属入口也已删除，因为内容小众、难生成且当前价值低。两者仍保持 unverified，不获得 Copy/Move 放权。FileAttachment 的历史证据、`kind` 边界和观察环境只保留在 [`docs/lesson/onenote_page_object_kind_and_file_attachment_representation.md`](../../docs/lesson/onenote_page_object_kind_and_file_attachment_representation.md)。

用户确认后，runner 在验证前先写 `interactive-authored-snapshot.json` 和 content-free `interactive-detection.json`。后者固定记录 requested/observed/missing/unexpected/supporting、对象计数和 capability projection。失败时初始 `fixture-snapshot.json` 不被覆盖，Notebook 保持打开，cache 不初始化、不发布；错误摘要会显示精确类型和计数。

固定 `cache-invalidation --use-cache` 只绑定自己的 programmatic Recipe fingerprint/instance，不接受任何 path、ID 或 fingerprint 参数。若已有 entry，它会在 materialize/open 之前精确失效；若是 cold miss，则先发布受验证 entry、立即对该精确 entry 执行同一清理门，再重新发布并 materialize。cleanup tombstone 必须证明 cache-root containment、ownership、无 reparse point、无 open source/working lease；任何清理失败都会停止且不覆盖。

`user-authored-fixture-consumer --use-cache --template-instance-id authored-<24 hex>` 与 bootstrap 共享同一 contract fingerprint，但拥有独立 Scenario/Recipe instance。Consumer 不枚举或猜测实例；缺失、格式错误、未知实例以及 `evidence_only` 都在 working Notebook 打开前 fail closed。省略 `--use-cache` 的 dry-run 只报告 `preflight-cache-required`，真实执行也会在 lifecycle/MCP/cache 访问之前拒绝；只有显式选择的 `ready` 实例会 materialize，并再次通过 reserved marker、authoring-zone 和 live content validation。

`inserted-file-fixture-consumer` 是只读、cache-only 的 working Scenario，与 `bootstrap-inserted-file-fixture` 共享 fingerprint 和固定 instance。它不创建 fixture、不等待人工输入，也不进入 `all`；只 materialize 已缓存模板、显式加载层级、重绑定 live ID，并重新执行 `InsertedFile` detector/projection 验收。缺少可命中的 `ready` entry 时会在 Notebook/MCP 启动前返回 `interactive_bootstrap_required` 并提示具名 bootstrap：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py inserted-file-fixture-consumer --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py inserted-file-fixture-consumer --use-cache
```

2026-08-11 用户真实验证：`run-20260811T022911Z` 的 consumer 以 validated hit 观察到精确 `InsertedFile=1`，完成 live materialized revalidation、证明 template 未打开并关闭 working Notebook；`run-20260811T023122Z` 的 bootstrap 完成人工 ACCEPT、发布 ready template，并在 ID 全部重建的第二份 working bundle 上完成结构重绑定和二次 live validation。后续两次使用过长物理名称的 consumer 在 Notebook folder 首次 `OpenHierarchy` 上返回 `0x80042006`；命名缩短为 `__<scenario>-<?CACHED>-<YYYY-MM-DD-HH-MM-SS>__` 后，`run-2026-08-11-12-30-34` 与 `run-2026-08-11-12-31-13` 连续以 `decision=validated_hit` 通过 hierarchy open、live materialized revalidation、`InsertedFile=1` 和 `opened_template=false` 证明；前者正常关闭，后者按用户的 `--keep-worksite` 选择保持打开。该证据只覆盖当前环境的 InsertedFile recipe/cache 路径，不代表所有 OneNote 版本共享同一路径上限，也不代表 FileAttachment 或 Copy/Move 保真已获放权。

失败 run 的 working Notebook 被用户手动关闭后，下一次 cache consumer 会用只读 COM ID/path probe 把遗留 active lease 标记为 `stale_closed_observed`；它不会接管或关闭仍然打开的旧 working Notebook。

同一个 Scenario registry 还导出冻结的 `dry_run_cases`：每个公开场景自动拥有 `default` 与 `keep-worksite` case，Rename 和 Page Reorder 在自身类中声明有限参数变体，另有 runner 级 `all.default`。pytest 使用正式 parser、无 I/O pure plan builder 和 side-effect sentinel 运行 catalog；`included_in_all=False` 不影响 dry-run 收集。Case 不得携带 `--dry-run`、`--json`、`--run-dir` 或授权参数，这些值只能由 harness 强制注入。

`all` 本身不是 scenario，不创建共享 Notebook 或共享证据目录，也不接受 `--run-dir`、`--notebook-label`、`--notebook-name`、`--keep-notebook` 或 `--keep-worksite`。每个已注册子命令仍创建自己的默认 Notebook 和 `.local-validation\run-<YYYY-MM-DD-HH-MM-SS>`，使用自己的 MCP 子进程、最小权限、报告与关闭/失败保留语义。一个 scenario 失败后，`all` 会显示其错误并继续后续已注册 scenario，最终返回第一个失败的非零退出码。

每个命令本身就是一次完整的隔离闭环，只运行所选 scenario：

```text
create fresh isolated Notebook through the narrow lifecycle wrapper
→ start exactly one scenario-scoped MCP process
→ create only that scenario's fixture and run exactly the selected scenario
→ write local evidence report
→ close the exact leased source Notebook（默认）或 keep open
```

内部 scenario 类、`scenarios/common/report.py` 以及其他共享库不能被直接调用或组合成另一个隐式入口；公开的 `create` 仍只通过统一 parser 作为完整 scenario 运行。

`create` scenario 先按以下预设结构创建隔离 Notebook，再在专用空 Section 中连续创建两个同标题 Page：

```text
Group-A
├─ Content-Section
│  ├─ Parent        rich text + table + image
│  ├─ Child         pageLevel=2
│  └─ Sibling       pageLevel=1
└─ Duplicate-Title-Target
Group-B
Delete-Sandbox
├─ Disposable-Group
└─ Disposable-Section
   └─ Disposable-Page
```

场景保存 `before.json/create-results.json/after.json`，要求两次 COM allocated/read-back ID 完全一致、互异、均为 fresh Page 且属于 `Duplicate-Title-Target`，两份不同正文可独立回读。默认按两个精确 Page ID 非永久删除并以 `restored.json` 证明恢复；`--keep-worksite` 跳过该清理、保持 Notebook 打开并记录精确 IDs。

## 安全审查与执行

用户应先查看 dry-run；它不创建目录、不启动 MCP、不访问 OneNote：

以下 canonical 命令由 `SCENARIO_REGISTRY.dry_run_cases` 投影并由纯文档合同检查；Markdown 只用于显示，从不由测试执行：

<!-- dry-run-case: create.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py create --dry-run --json
```

<!-- dry-run-case: rename.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json
```

<!-- dry-run-case: reorder-page.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-page --dry-run --json
```

<!-- dry-run-case: reorder-section.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section --dry-run --json
```

<!-- dry-run-case: reorder-section-group.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section-group --dry-run --json
```

<!-- dry-run-case: reparent-section.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section --dry-run --json
```

<!-- dry-run-case: reparent-page.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
```

<!-- dry-run-case: reparent-section-group.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section-group --dry-run --json
```

<!-- dry-run-case: delete.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py delete --dry-run --json
```

<!-- dry-run-case: copy-page.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --dry-run --json
```

<!-- dry-run-case: copy-section.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section --dry-run --json
```

<!-- dry-run-case: copy-section-group.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section-group --dry-run --json
```

<!-- dry-run-case: copy-notebook.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-notebook --dry-run --json
```

<!-- dry-run-case: move-page.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py move-page --dry-run --json
```

<!-- dry-run-case: bootstrap-inserted-file-fixture.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-inserted-file-fixture --dry-run --json
```

<!-- dry-run-case: bootstrap-ink-drawing-fixture.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-ink-drawing-fixture --dry-run --json
```

<!-- dry-run-case: bootstrap-media-file-fixture.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-media-file-fixture --dry-run --json
```

<!-- dry-run-case: bootstrap-user-authored-fixture.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-user-authored-fixture --dry-run --json
```

<!-- dry-run-case: cache-invalidation.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py cache-invalidation --dry-run --json
```

<!-- dry-run-case: user-authored-fixture-consumer.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py user-authored-fixture-consumer --dry-run --json
```

<!-- dry-run-case: inserted-file-fixture-consumer.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py inserted-file-fixture-consumer --dry-run --json
```

<!-- dry-run-case: all.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run --json
```

确认 Notebook 名、目录、步骤、权限和 allowlist 后，真实命令只能由用户本人运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

若要验证多个 scenario，可分别运行多条命令，也可由用户本人显式运行 `all` 来执行注册的稳定测试集合。两种方式都会让每个 scenario 创建自己的全新 Notebook 和证据目录，没有跨命令前置依赖。

### `all` 参数与输出

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all `
  [--timeout <seconds>] `
  [--dry-run] `
  [--json] `
  [--verbosity quiet|normal|verbose]
```

- 默认 `quiet`：只输出每个场景的开始、PASS/FAIL、总进度，以及失败场景的 stdout/stderr。
- `normal`：额外输出每个成功场景的结果；检查全部 dry-run 计划时建议使用 `all --dry-run --verbosity normal`。
- `verbose`：在 `normal` 基础上输出每个子进程命令及成功场景的 stderr。
- `--dry-run`、`--json` 和显式 `--timeout` 原样传给每个 scenario；`--json` 时聚合进度使用 JSON Lines。
- 未指定 `--timeout` 时保留各 scenario 自己的默认值（普通场景 180 秒，Copy/Move 1800 秒）。
- `all` 没有自己的 `run-dir`，因此不支持 `--run-dir`；它也不会把多个场景放进同一目录。

## 参数与生命周期

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> `
  [--notebook-label <lowercase-kebab-label>] `
  [--run-dir <path>] `
  [--keep-notebook] `
  [--keep-worksite] `
  [--timeout <seconds>] `
  [--dry-run] `
  [--json]
```

- Fresh Notebook：`__<scenario>-<YYYY-MM-DD-HH-MM-SS>__`。
- Cache working Notebook：`__<scenario>-CACHED-<同一时间戳>__`；多 role bundle 在 `CACHED` 前增加 role。
- 默认目录：`.local-validation\run-<同一个 YYYY-MM-DD-HH-MM-SS>`。
- `--notebook-label` 只替换名称中的 `<scenario>` label，必须是 lowercase kebab-case；deprecated `--notebook-name` 暂时作为同义 alias，不再接受任意完整 Notebook 名称。
- 显示时间戳来自运行主机本地时区，例如 `2026-08-11-11-05-49`；完整时区证据保存在 `run_identity`，事件时间仍为 UTC ISO-8601。
- `--run-dir` 必须不存在或为空；同名 Notebook 已存在时拒绝复用。
- 默认仅在当前 scenario 和报告成功后，按 lifecycle lease 的精确 ID/name/path 并经即时回读后关闭源 Notebook。
- `--keep-notebook` 保持源 Notebook 打开，供用户人工检查。
- `--keep-worksite` 适用于全部公开具名场景，并同时保持源 Notebook 打开。可恢复的 `rename/reorder-page/reorder-section/reparent-page/reparent-section/reparent-section-group` 不执行反向恢复；Page/Section/SectionGroup Copy 不执行回收站 cleanup；Notebook Copy 不关闭副本；其余 action 记录本来就会留下的 fixture、回收站或 Move 状态。`worksite.json` 和 `result.json` 记录精确目标 ID、当前位置/名称/路径以及 `manual_cleanup_required=true`；Page reparent 还记录新旧 ID 历史。未进入批处理的场景均设置 `included_in_all=False`，但仍注册 default/keep dry-run cases；特殊入口 `all` 不接受 `--keep-worksite`。
- Runner 永不删除本地 Notebook 文件或目录；Notebook Copy 文件夹同样保留。
- `delete` 自动使用本次 manifest 中的 `disposable_group`，不接受外部 target ID，并保持非永久删除。
- `rename` 另支持 `--target group_a|group_b|content_section` 和 `--new-name`。
- `reorder-page` 另支持 `--page-level <n>`。

## Isolated、单进程与最小权限边界

`scenarios/` 根目录中的每个可执行模块只提供一个具名 `Scenario` 子类；四个 Copy 入口分别位于 `copy_page.py`、`copy_section.py`、`copy_section_group.py` 和 `copy_notebook.py`，并共享基础设施 `copy_scenario_base.py`。根目录的 `base.py` 和 `__init__.py` 明确属于基础设施。类统一声明名称、help、默认 timeout、scenario 专属参数、fixture recipe、dry-run variants、manifest 参数准备、执行器和 `included_in_all`，并通过 registry wrapper 注册。`scenarios/__init__.py` 是公开场景导入顺序的唯一清单，`SCENARIO_REGISTRY` 则是 parser、dispatch、fixture metadata、dry-run catalog 和 `all` 的共同权威对象。

不代表单个 scenario 的依赖统一放在 `scenarios/common/`，包括 registry、闭环 orchestrator、静态 spec、fixture builders、fixture 编排、报告、Copy runtime 与 invariants。根目录因此不会混入名称看似 scenario、实际却只是共享函数的模块。

每个 scenario 都在本次独占的 disposable working Notebook 中运行，并最多启动一个 MCP 子进程。默认 fresh 路径创建新 Notebook；cache 路径只打开刚 materialize 的 working directory。Notebook 的 create/open/get/close 由窄 lifecycle wrapper 完成；wrapper 不提供 Section、Page 或内容写入能力。它立即写入 `lifecycle-lease.json`，绑定本次 run 的精确 Notebook ID、名称、本地 working path、template paths 和 `opened_template=false`。

唯一 MCP 子进程同时完成该 scenario 的最小 fixture、所选 mutation、before/after/restored 回读和契约内 restore/cleanup。它启动时使用 `scenarios/common/specs.py` 中固定的完整闭包 policy 和 tool allowlist，并在 fixture 创建前用 `health_check` 精确核对 policy、timeout 和 Copy budget；启动后不得扩权。Runner 不使用所有 scenario 的权限并集。

| Scenario | Fixture 与权限限制 |
| --- | --- |
| `create` | 完整预设 fixture 加空 `Duplicate-Title-Target`；连续两次 `create_page` 验证同标题 fresh allocated/read-back IDs，默认用 typed `delete_page(permanently=false)` 精确清理；不暴露 `create_notebook`，永久删除关闭；`--keep-worksite` 保留两个目标 Page |
| `rename` | 一个选定 Group/Section；fixture 写入加对应 rename 工具；`--keep-worksite` 保留新名称并记录原名称 |
| `reorder-page` | `Description/00-Reorder-Description` 明示操作前 `01,02,03`、正向操作后 `01,03,02`、恢复后 `01,02,03`；`01-Reorder-Page-Section` 下使用 `01-Parent`、`02-Child`、`03-Sibling`，让 UI 顺序和缩进变化可直接肉眼验收；`--keep-worksite` 保留 `01,03,02` 与新 predecessor/level |
| `reorder-section` | `00-Description/00-Reorder-Section-Description` 分别说明 Notebook 父级和 `01-Section-Parent`（SectionGroup）父级的 before/after/restore；两组 Section 及其 Page 均使用 `01/02/03` 编号，UI 可直接核对 `01,02,03 → 01,03,02 → 01,02,03`；只开启 Writes 与 Section Reorder；用户已确认真实 UI 排序证据 |
| `reorder-section-group` | **功能受限 / 验证失败 / 不注册到 `all`**。保留完整 fixture、mutation 和写后回读实现作为可单独调用的诊断场景；真实后端对 Notebook 直属 Group 返回 UpdateHierarchy 成功但保持按名称固定升序，嵌套操作未执行。dry-run 和运行状态证据写入 `capability_assessment={capability_status: limited, validation_status: failed, ...}`。 |
| `reparent-section` | `00-Description/00-Reparent-Section-Description` 说明三种 before/after/restore：`01-Notebook-To-Group-Section` 从 Notebook 根换父级到 `01-Destination-Group`，`02-Group-To-Notebook-Section` 从 `02-Source-Group` 换父级到 Notebook 根，`03-Group-To-Group-Section` 从 `03-Source-Group` 换父级到 `03-Destination-Group`。三个 Section 及其 Page 均编号；每次 Reparent 后刷新快照，验证 ID、父级、Page 拓扑和内容，默认逆序恢复，`--keep-worksite` 保留三项目标父级。只允许同一 Notebook。 |
| `reparent-page` | **typed 实验工具 / 用户确认迁移后真实验证通过 / 不注册到 `all`**。通过 `reparent_page` 提交精确 ID 与 confirmation，不要求 Raw XML。`00-Description/00-Reparent-Page-Description` 解释原生 `UpdateHierarchy` 与 ID 重映射；`01-Source-Section/01-Reparent-Page` 同页包含 Rich Text、Table、List、Tag、Image，再请求改属 `02-Destination-Section`，编号锚点保持无关。工具与 runner 双层验证 Page/内容对象一对一映射、富内容和无关对象；默认使用新 ID 逻辑移回，或由 `--keep-worksite` 保留。 |
| `reparent-section-group` | **typed 实验工具 / 用户确认迁移后真实验证通过 / 不注册到 `all`**。通过 `reparent_section_group` 提交精确 ID 与 confirmation，不要求 Raw XML。三组编号 Group/Section/Page 覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup；要求目标及后代 ID、关系、Page 内容保持。默认按 `03→02→01` 逆序恢复，`--keep-worksite` 保留三组父级。 |
| `delete` | Delete-Sandbox 与 allowlisted disposable group；写入加非永久 Delete，永久删除关闭；`--keep-worksite` 保持 Notebook 打开并记录回收站目标 |
| Page Copy | 双 Notebook `source`/`destination` bundle；同一 source Page 对同 Section、跨 Section、跨 Notebook 三种目标分别执行 root-only（省略参数）与 subtree（显式 `true`），合计六个 case。同 Section 以源 Child 自然形成同名碰撞，跨 Section/Notebook 目标各预置一个同标题、不同正文 anchor；每个 case 分别稳定 plan、执行、双侧回读并断言 fresh/disjoint target IDs、源端和 anchors 的 hash/order/level 不变；默认清理六个根目标并验证两侧恢复，`--keep-worksite` 保留六个目标和两个 working Notebook |
| Section/Group Copy | 对应最小源和目标；源容器含严格富内容父页与三个混合 List/Tag 项的语义子页，并继续递归复制完整子树；默认执行可恢复清理，显式 `--keep-worksite` 在 after/mapping 验证后保留精确目标 ID |
| Notebook Copy | 最小 Notebook 同样包含严格父页和 List/Tag 语义子页；Copy 开启、Delete 关闭，默认关闭副本；显式 `--keep-worksite` 保持副本打开并记录路径 |
| Page Move | disposable 源 Page 子树与目标 Section；目标预置一个与源子页同标题、不同正文的 anchor。仅开放专用 experimental/copy/delete 闭包；要求完整 fresh/disjoint `id_map` 与 anchor hash/order 不变后才接受叶到根源删除证据；`--keep-worksite` 记录 active Copy、非永久删除的 source IDs 与回收站诊断状态 |
| Report | 只读取本地 artifacts，不启动 MCP |
| Source lifecycle | wrapper 仅支持 fresh create、working-copy open、受控 SectionGroup/`.one` 加载、精确 get/close 与只读 open-state probe；不启动额外 MCP，也不打开 template |

永久 OneNote Delete 始终关闭。三个 Reparent 场景只启用 Writes 与统一 Reparent 实验门；Raw XML 在全部 Reparent 场景中关闭，runner 不构造、不接收也不传递 hierarchy XML。

### 同 Notebook Reparent 能力

先查看无副作用计划：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section-group --dry-run --json
```

真实运行只能由用户本人分别显式启动。`reparent-page` 的 Description 明示编号 Page 从 `01-Source-Section` 到 `02-Destination-Section` 再恢复的状态和富内容门限；`reparent-section-group` 的 Description 明示 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup 三条编号路线。两个场景都把 COM 返回成功仅视为“请求已返回”，不视为能力成立。

Page typed 场景接受两种原生结果：目标 ID 保持，或全树中恰好发生 `旧 Page ID 消失 + 目标 Section 新增一个 Page ID` 的一对一替换。后者必须记录 `old→new`，且新 Page 的 Notebook、标题、page level、父子缩进、富内容语义摘要和内容对象语义必须与原 Page 一致；Rich Text、Table、List、Tag、Image 的 fixture 能力在 mutation 前已经过门限。富内容摘要忽略 Page/内容对象 ID，并把 TagDef/Tag index 解析为类型和符号后比较，其余格式、结构、文本和 Image Data 保持严格。所有无关对象仍要求 ID、关系、稳定内容 hash 和内容对象身份不变。默认恢复使用正向回读得到的新 ID；OneNote 再次重映射时记录第三个 ID，并按逻辑位置和相同富内容摘要验证恢复，不虚构原 ID 已恢复。场景没有 Copy/Delete 权限，不调用 `copy_page` 或 `DeleteHierarchy`，也不把回收站可见性作为验收条件。

SectionGroup typed 场景仍要求同一目标 ID、全树 ID 集合、全部后代和 Page 内容身份保持不变；每步回读，默认按第三、第二、第一条路线逆序恢复。`--keep-worksite` 只在全部正向验证通过时保留现场。请求被忽略、Page ID 转换不是精确一对一、富内容变化、无关对象变化或恢复失败都会非零退出并保留 Notebook 与证据。一次通过只证明当前 OneNote/Office 组合，不构成跨版本保证。

`reorder-section` 与 `reorder-page` 一样，不要求也不收集 OneNote 版本或 Office channel 参数。跨版本兼容性取证作为独立低优先级工作跟踪，见 [`docs/todo/007_cross_version_compatibility_evidence.md`](../../docs/todo/007_cross_version_compatibility_evidence.md)，不作为当前场景的运行前置条件。`reorder-section-group` 保留实现和扁平 CLI 注册，但只用于明确的独立诊断，不进入 `all`，也不得因跨版本取证重新解释为受支持能力。其静态状态可无副作用检查：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section-group --dry-run --json
```

输出必须包含 `capability_status=limited`、`validation_status=failed` 和后端固定名称升序的原因。真实命令仍受 HUMAN-GATED 规则约束，只能由用户本人显式启动；现有负能力证据已经充分，不要求重复运行。

## Page Move

`move-page` 的语义天然是重建：Copy 完整 Page 子树、验证新对象，再对源 Page 执行非永久删除。场景始终运行严格门禁，不会跳过或降级；它使用严格父页和 List/Tag 语义子页验证整棵 Page 子树，并在目标 Section 预置同标题、不同正文的 anchor。目标 `id_map` 必须 fresh、互异且与源/anchor IDs 不相交，anchor 的正文 hash、order、level 和 parent 保持，之后才接受 `attempted_source_ids` 与叶到根删除顺序。当前 validated 保真类型为 `Outline/Image/RichText/Table/List/Tag`；出现尚未确认的附件、墨迹、形状、媒体、`MeetingInfo` 或未知结构时，场景仍可能返回 `copy_only`、保留源 Page，或因保真门未通过而非零退出。当前取证优先级只覆盖墨迹、UI 形状和在线视频；排除项仍保持 fail closed。

源 Page 只通过 `DeleteHierarchy(permanently=false)` 非永久删除。生产删除服务会有界回读每个精确 ID：对象必须从活动 hierarchy 消失，或者明确带 `is_in_recycle_bin=true`；仍活动则失败。工具成功后，manual scenario 的 `after.json` 还会独立确认整棵源子树不再活动。由于实际环境可能在 OneNote UI 的“已删除的笔记”中显示源 Page、但 COM hierarchy 不返回其旧 ID，回收站标记已降为可选诊断信息，不再是成功关口。`copy-result.json` 和 `restored.json` 会用 `recycle_bin_verification`、`recycled_source_ids`、`recycle_unverified_source_ids` 区分“已取得标记”和“COM 未暴露标记”；后者仍需用户在 UI 中人工检查和清理。

如果 OneNote UI 已切换到回收站中的源 Page，可在启动真实 scenario 的同一个普通用户 PowerShell 会话中运行只读诊断脚本，对比窗口 API 返回的当前 Page ID 与原始 ID。脚本优先连接 ROT 中的活动 OneNote 对象，不执行导航、写入或删除，也不输出 Page 正文：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\inspect_current_onenote_page.ps1 `
  -ExpectedPageId '{SOURCE-PAGE-ID}'
```

`matches_expected_page_id=true` 表示回收站页面保留原 ID；`false` 表示当前回收站页面使用了不同 ID。若 `page_metadata.readable=false`，Current Page ID 的比较仍然有效，只是 `GetPageContent` 不接受该回收站 ID。

失败时不会关闭源 Notebook，也不会删除文件。`copy-result.json` 和失败交接会记录 `outcome`、`created_ids`、`allocated_ids`、`resolved_target_ids`、`possibly_untracked_allocated_ids`、`id_map`、`source_touched`、`topology_touched` 与 `manual_recovery_required`，供用户按已证明状态人工核对。

## 证据与失败语义

```text
.local-validation/run-<TIMESTAMP>/
├─ run-state.json
├─ run-result.json 或 run-failure.json
├─ manifest.json
├─ prepared.json
├─ fixture-result.json
├─ lifecycle-lease.json
├─ lifecycle-bridge-calls.jsonl # lifecycle COM bridge operation names/timing only
├─ lifecycle.json
├─ run-metrics.json          # phase timing、MCP starts/tool calls、bridge call counts
├─ report.md
├─ notebooks/                 # 始终保留
├─ notebook-copies/           # 若创建，始终保留
└─ scenarios/<scenario>/
   ├─ before.json
   ├─ plan-attempts.json / plan.json / copy-result.json
   ├─ copy-page: plans.json + plan/before/copy-result/after-<case>.json
   ├─ after.json / restored.json / worksite.json
   └─ result.json 或 failure.json
```

唯一 MCP 的 content-free bridge audit 位于 `scenario-mcp/bridge-calls.jsonl`；只记录 operation、成功状态、时间和耗时，不记录参数、OneNote 内容或返回值。`fixture-result.json` 的 `validation` 段记录 profile topology/content invariants 的实际通过证据。

Copy mutation 前会有界执行最多三次只读 `plan_copy`，只有连续两次 `plan_digest` 完全一致才继续；每次摘要、source modified 和有效 `include_descendants` 写入 plan-attempts 证据。`copy-page` 为六个静态 case 分别写入 `plan-attempts-<case>.json`、`plan-<case>.json`、`before-<case>.json.plan_binding` 和 `copy-result-<case>.json`；三个 root-only case 在 plan/execute 中均省略范围值并要求回显有效值为 `false`，三个 subtree case 显式提交并要求回显 `true`。Runner 在每次 mutation 前复核目标 Section、目标 role 和范围，并从 source/destination 两侧最新快照合并下一 case 的 before evidence，从而把六次 mutation 的增量逐项隔离。这用于等待 fixture 写入引发的 COM 容器时间延迟传播，不重试任何 mutation；任一 case 三次仍不稳定就会在该次写入前 fail closed。

随后 `before.json` 与稳定的 `plan.snapshots.source` 显式绑定：容器 `modified` 采用受 `plan_digest` 保护的值，而不是 fixture 刚写完时可能仍在被 COM 延迟更新的 pre-plan 值。生产 plan 的 raw XML SHA-256 单独保存在 `plan_binding.raw_page_hashes`；Runner 的 `before/after.page_hashes` 使用稳定内容 hash：忽略 Page 根级 hierarchy 字段、OneNote 在任意内容节点上延迟补写的时钟/作者/选择/视图元数据，以及空、无子节点且只携带 `selected/isSelected` 的 T 视图占位；普通空 T、非空文本、内容对象 ID、格式和二进制仍保留。`page_canonical_hashes` 忽略 Page/内容对象 ID，作为诊断摘要；Page reparent 的成功门限使用更精确的 `page_reparent_hashes`，额外把 Tag index 解析成类型/符号语义，同时保留 Rich Text、Table、List、Tag 状态、Image Data 和其他结构。原始 XML SHA-256 另记在 `page_xml_hashes`，只用于诊断 COM 重序列化，不作为内容变化成功门限；`page_objects` 仍独立记录内容对象投影。Page Copy 的逐 case 不变性门只绑定 manifest 中的 source Parent/Child 与跨 Section/Notebook anchors，并同时比较 topology、稳定内容 hash 和内容对象身份；Description 等非验收页的后台规范化不参与业务成功判断。执行 confirmation 使用 plan-bound 容器状态，默认 cleanup 恢复仍比较一致的 Runner 证据；任何受保护 Page 的真实稳定内容变化都会 fail closed。

每次通用 snapshot 在完成逐 Page 取证后都会再读取一次完整 hierarchy：最终 `items/modified` 来自这次末尾回读，并要求前后 ID 集合一致。这避免把 fixture 刚创建时的旧 `modified` 用作随后 mutation 的 confirmation，同时不会重试 mutation。

任一步失败立即停止。Mutation 失败时最终 close 不会启动，源 Notebook 保持打开；close 失败按恢复失败返回非零。`run-failure.json` 记录失败步骤、已完成步骤、finalization 状态和人工检查建议。

成功的可恢复 action 默认仍完成 restore/cleanup，并用 `restored.json` 证明恢复。显式 `--keep-worksite` 才写入 `worksite.json`、保留动作后的精确状态和源 Notebook；该模式只在 scenario 自身的 read-back invariant 通过后报告成功。对 Copy，此成功还表示每页按其内容类型选择的 read-back tier 与 mapping invariant 均通过；UI 人工检查仍用于记录具体 OneNote 环境的真实证据。

`run-metrics.json` 记录 lifecycle create、唯一 scenario process、report、finalize 和总耗时，以及实际 MCP 启动数、MCP tool call 数和 scenario/lifecycle bridge call 数。真实性能对比只能由用户本人运行后据此确认；合同测试只验证结构和计数，不把 mock 耗时作为性能收益。

## 仅限纯合同测试

以下命令不访问 OneNote，可以由 Agent 或自动化运行：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
```

真实后端验收必须由用户本人先运行目标 scenario 的 `--dry-run`，再运行同一个扁平 scenario 命令。
