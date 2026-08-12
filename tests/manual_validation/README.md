# OneNote Manual Validation — HUMAN-GATED / ISOLATED / LEAST-PRIVILEGE

> [!CAUTION]
> 本目录只承载由用户本人显式启动的真实 OneNote mutation 验证。Agent、CI、pytest、hook、安装脚本、timer、watcher、前台或后台任务不得执行真实 scenario。每次运行必须创建全新隔离 Notebook，并使用 scenario 级静态最小权限。智能体的强制行动边界见本目录的 [AGENTS.md](AGENTS.md)。

## 公开接口：扁平 Scenario、特殊 `all` 与 `clear`

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
.venv\Scripts\python.exe tests\manual_validation\run.py move-section
.venv\Scripts\python.exe tests\manual_validation\run.py move-section-group
```

历史验证 artifact 与 fixture cache 只通过独立的 `clear` maintenance 分组维护。它不是 Scenario，不进入 registry 或 `all`，不会启动 scenario MCP、修改或关闭 OneNote，也不接受任意路径或强制绕过参数：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py clear runs --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py clear cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py clear all --dry-run --json
```

Dry-run 只读受管 metadata，并获取一次当前 OneNote 已打开 Notebook 的实际本地路径快照；不创建目录、receipt，不删除文件，也不修改或关闭 Notebook。真实执行只能由用户本人在交互式前台终端明确运行。命令行不接受 `--confirm`；安全检查完成后，Runner 才在后续提示中要求用户现场输入对应的动作绑定值：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py clear runs
.venv\Scripts\python.exe tests\manual_validation\run.py clear cache
.venv\Scripts\python.exe tests\manual_validation\run.py clear all
```

三个命令分别提示输入 `CLEAR-RUNS`、`CLEAR-CACHE`、`CLEAR-ALL`。提示写入 stderr，最终 `--json` 结果仍只写 stdout；stdin 不是交互式终端、输入不匹配或 EOF 时，在创建 marker/receipt 或删除任何目标前拒绝。

`runs` 逐个评估直接 `run-*` 子目录，`cache` 逐个评估 index/磁盘相互证明的 exact `(fingerprint, template_instance_id)` entry 以及受管 staging/legacy lease metadata，`all` 在同一次 open-path snapshot 下组合两者。任一目标只有在固定 root、ownership、无 reparse point、实际未打开和 pending receipt 全部通过后才删除；开放中或无法证明的目标单独 `refused`，其他安全目标仍可处理，整体以非零 partial result 返回。

清理结束后会自动收敛 maintenance 自身的残留：成功 target 的完整逐项证据先嵌入 durable summary，再删除对应 `deleted` receipt；pending、failed、无 summary 绑定或内容不匹配的 receipt 原样保留。`clear cache/all` 还会从 index 移除已无 payload 的 tombstone，并用逐层 `rmdir` 清理可证明为空、名称为 canonical fingerprint 的 `instances`/fingerprint scaffold；非空、含 lock、未知形状或 reparse point 的目录不碰。Dry-run 的 `finalization_plan` 会列出可收敛数量。`.local-validation/` 根、managed marker、summary，以及 cache marker/quarantine/recovery history 始终保留。

每个具名 action 都可显式保留已验证的操作现场，供 OneNote UI 人工验收：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-page --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --keep-worksite
```

`--keep-worksite` 会隐含保持源 Notebook 打开，并在成功 read-back 验证后保留该 action 的现场：`rename/reorder-page/reorder-section/reparent-page/reparent-section/reparent-section-group` 跳过反向恢复，Copy 跳过目标 cleanup，`create/delete/move-page/move-section/move-section-group` 保留其原本最终状态以供查看。精确目标 ID、原/现 predecessor、现场状态和人工清理说明写入 `worksite.json`。Page reparent 若由 OneNote 重映射 ID，会同时记录 `target_id`、`current_target_id` 与完整 `id_history`。该选项不会扩权；Copy 场景反而从 policy/tool allowlist 移除不再需要的 Delete/Close cleanup 权限。默认不传时仍执行各 scenario 原有的 restore/cleanup 与生命周期策略。`reorder-section`、`reorder-section-group`、`reparent-page` 和 `reparent-section-group` 不进入 `all`，但仍全部进入注册 dry-run 自动测试。

四个 Copy 层级场景都自动创建两页组成的完整 Page 保真 fixture。Section/Group/Notebook Copy 使用通用名称；Page Copy 为便于 UI 对照，使用等价的编号名称：

- `Rich-Page` / `01-Source-Parent`（父页）：基础内容为已确认的 `Outline/RichText/Table/Image`；无公式页面使用严格 canonical 验收。其中 `copy-page` 的 `01-Source-Parent` 额外包含一个行内 Presentation MathML 公式和一个 `display="block"` 单行公式；行内公式仍属于 `RichText`，单行公式分类为 `DisplayEquation`，整页因此使用 `semantic_display_equation`；
- `List-Tag-Page` / `02-Source-Child`（子页）：程序通过受限 HTML 自动生成三个编号/项目符号与 To Do 标签混合项（完成、未完成、完成），使用 `semantic_list_tag` 验收。

`copy-section` 与 `copy-section-group` 都使用固定的 `source`/`destination` 双 Notebook recipe，并在同一个 scenario MCP 中顺序执行两个 case：先复制到 source Notebook 内的精确目标父级，再复制到 destination Notebook 的精确目标父级。每个 case 都有独立的稳定 plan、before/after、Copy response 和角色证据；第二个 case 还保护第一个 case 已创建的 Page 内容不被改写。默认按跨 Notebook→Notebook 内部的反向顺序，对两个完整 target subtree 逐叶到根执行非永久 cleanup，并验证两个 Notebook 恢复；`--keep-worksite` 则保留两个目标和整个 working bundle。两类 Recipe version 3 使旧单 Notebook cache entry 不会命中新合同。

`copy-notebook` recipe version 3 的 source 除原根 `Source-Section` 富内容子树外，还包含 `Source-Group/Grouped-Section/Grouped-Page`，因此 Notebook Copy 必须同时证明根 Section 与嵌套 SectionGroup 子树的完整映射。Copy 目标完成 snapshot 验证后，Runner 会立即通过精确 target Notebook ID 重新读取最新 `name/modified`，把该值保存到 `close-confirmation.json`，再调用一次 `close_notebook`；不会继续使用 Copy response 中可能因 COM 延迟更新时间传播而过期的 `modified`，也不会重试 close mutation。

2026-08-11 用户真实验证：`run-2026-08-11-21-33-01` 的 `copy-section` 与 `run-2026-08-11-21-36-13` 的 `copy-section-group` 都以 `decision=cold_build` 完成 source/destination 双 role materialization，并分别通过 Notebook 内部与跨 Notebook 两个 case。Section 的两个 case 各映射 3 个对象，SectionGroup 的两个 case 各映射 4 个对象；四份 Copy report 均为 `verified=true`、`lossless=true`。两次运行随后反向执行精确非永久 cleanup，两个 Notebook 都恢复并关闭，`opened_template=false` 且 cache template inventories unchanged。`run-2026-08-11-21-31-17` 的 `copy-notebook` 同样以 `decision=cold_build` 通过，完整映射 Notebook 根、根 Section 的 Rich/List-Tag 双页，以及新增的 SectionGroup/Section/Page 子树，共 7 个对象；三页分别通过 strict、semantic List/Tag、strict comparator。目标 Notebook 在精确 ID 最新回读后以刷新后的 `modified` 一次关闭，`close-confirmation.json` 与 `closed_not_deleted` 证据通过；源 working Notebook 正常关闭，模板未打开且 inventory 不变。Notebook Copy 的 `restored=false` 是预期语义：COM 只提供关闭目标 Notebook，不提供 typed Notebook 删除，目标目录和 run evidence 均保留。

`move-page` 不重复承担内容类型取证。它使用仅含已验证 Outline/RichText 的两个独立最小源子树，以及另一个 Notebook 中的目标 Section；场景只验证生产 Copy 已返回 `verified/lossless`、范围与 `id_map` 精确，以及后续非永久源删除和排除后代保留正确。

`move-section` 与 `move-section-group` 同样不重复内容类型取证。每个场景创建精确的 `source`/`destination` 双 Notebook bundle，只把一个最小 Outline/RichText 容器子树移动到 destination Notebook 根。场景要求完整 `id_map` 和 verified/lossless Copy，只允许一次源容器根删除，且结果必须声明 `source_deleted_nonpermanently=true`、全部原源子树 ID inactive、目标 ID 全部位于 destination role。两者在独立真实验收与稳定性审查完成后均设置 `included_in_all=True`，与 `move-page` 一起进入显式 human-gated 的 `all` 批处理；真实命令仍只能由用户本人运行。

`copy-page` 是一个 `source`/`destination` 双 Notebook bundle。早期 recipe 在严格 Parent 中新增一个行内公式和一个独立单行公式，并要求回读恰好两个 MathML root、一个 `display="block"` 与两个规范 namespace 声明。`run-2026-08-12-01-11-36` 与 `run-2026-08-12-01-25-30` 证明 MathML 本身保持，但单行公式前出现空白；后续 detector/capture 又把该差异定位为 OneNote 在 DisplayEquation 前写入纯空白 `span + br`。v9 首次进入完整 `all --use-cache` 时，`run-2026-08-12-15-54-16` 与单独复跑 `run-2026-08-12-16-11-26` 都证明把 display marker 与公式放在同一次 append 会被真实 COM 合并，旧 detector 对“公式独占一个 OE、前驱 OE 非空”的布局假设不成立。当前 recipe v10 先写入普通富内容、行内公式和 marker，再用独立 append 写入 block MathML；fixture 门要求公式所在文本没有可见残留，并精确观察到当前环境已验证的一个 `span + br` 前置空行，不再依赖 OE 独占或前驱 OE。`run-2026-08-12-16-30-58` 已证明 v10 fixture/cold publish/materialization 成功，首个 Copy 目标的拓扑、正文、对象、binary 和两条公式语义也保持；失败只剩清除已知 span 后的页面 canonical 差异。增强诊断后的 `run-2026-08-12-16-44-30` 进一步证明 source/target 条件包装数同为 2，并把首差异定位到 formula-only Outline 的 `Size.width/height`。Comparator 现在只规范化完整配对公式条件包装，以及节点集合受限、恰好一个完整 block 公式且没有正文/其他 markup 的独立 Outline 派生 Size；Position、混合内容 Outline 和普通/不完整注释继续 fail closed。Copy 发送前仍清除已知空白包装并使用 `semantic_display_equation`，行内公式继续作为 RichText 的有界 MathML 严格比较。v10 fingerprint 使旧 cache 不会命中；独立 `copy-display-equation` 的三跳真实运行已经证明空白包装规范化稳定，但新增 formula-only Size 规则和完整 `copy-page` 六 case 仍需单独用户运行确认。六个 case 继续覆盖同 Section、同 Notebook 跨 Section、跨 Notebook三种目标范围，各自再覆盖 root-only 与完整子树；每个 case 都要求新 target IDs 与 source/anchor IDs 不相交，保持 Parent/Child 相对层级和两个 destination anchor 的正文、order、level 与 parent。默认按反向 case 顺序清理六个根目标并验证两个 Notebook 恢复；`--keep-worksite` 则保留全部目标供 UI 对照。

唯一特殊入口 `all` 会按显式 `included_in_all` 资格的顺序串行启动其中的 scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

唯一注册表对象位于 `scenarios/common/registry.py` 的 `SCENARIO_REGISTRY`。每个场景类使用 `@SCENARIO_REGISTRY.register` wrapper；`scenarios/__init__.py` 按审查后的固定顺序导入所有公开场景，导入时自动完成实例注册。Registry 本身不导入具体场景，也不维护第二份构造列表。新增公开 scenario 默认 `included_in_all = False`；只有经过稳定性和权限审查才可改为 `True`。`get_all_scenario_names()` 只表示用户真实 `all` 批处理资格，不能用于 pytest collection。

每个 Scenario 同时显式持有一个 `fixture_recipe`。场景专属 Description、构建与 validator 位于 `scenarios/fixture_recipes/<scenario>.py`；`common/fixture_runtime.py` 只负责 recorder、snapshot、通用 profile 检查和证据持久化，不按 scenario 名称分派。Recorder 在每个精确 ID 创建后增量写入 pending manifest；中途失败会保留已登记 ID、lifecycle lease 路径、Notebook 路径和 failed validation handoff。Copy/Move 只共享不含 scenario 名称分支的 layered Page component。

所有 recipe 统一继承 `fixture_recipes.recipe_base.RecipeBase`；原并行的 `FixtureRecipe` Protocol 已移除。Recipe 以有序 `notebook_roles`、profile、fixture parameters、manifest keys、creation tools、validation conditions 和 bundle invariant 形成无 I/O 的 canonical SHA-256 fingerprint。单 Notebook 是仅含 `source` role 的普通 bundle，不存在 `MultiNotebookRecipe` 或按 Notebook 数量分派的 cache registry。

普通具名 Scenario 默认仍使用 fresh fixture，并且不查询、创建、失效或清理 cache。重复调试复杂 fixture 时可显式使用：

需要在真实 COM 后端上反复执行某项待验证操作、先做自动比较、再由用户检查精确 UI 结果时，推荐遵循[缓存 Fixture 驱动的操作验收指南](cached_fixture_operation_validation.md)。该实践不限于 Copy，但只有已经注册并明确实现 machine comparison 与 run-bound verdict 的 Scenario 才能采用相应人工结论；本文不会把占位 operation 变成新的公开命令。

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py all --use-cache --dry-run --json
```

`--use-cache` 只改变 fixture 来源：validated hit 把关闭的 immutable template opaque-copy 到本次 run 的 role-specific working 路径。lifecycle 为每个 role 使用独立的 run-local lease（`source` 保持 `lifecycle-lease.json`，其他 role 使用 `lifecycle-lease-<role>.json`），先证明实际打开路径是 working path、不是任一 template path，并立即记录实际 working Notebook ID/name/path。Cache 不保存 working lease，也不与 run 维持所有权或生命周期关系；短时全局 open lock 串行化跨 run 的 lease 扫描、打开与 live identity 绑定。多个 run 可以从同一 immutable entry materialize 各自唯一的 working paths；只有实际 live Notebook ID/path 集相交、role 内重复或身份尚未可靠重绑定时才拒绝。Run-local active lease 不阻止物理独立 entry 的 invalidation/cleanup；cache cleanup 只在 template 自身的实际路径仍被 OneNote 打开时拒绝。随后逐 role、逐级调用 `OpenHierarchy` 打开 SectionGroup 和 `.one` Section：先用绝对 working path 与空 relative ID，必要时回退到文件名与精确 parent ID；不能把绝对 path 与非空 parent ID 混用，也不能把“Notebook shell 已打开”或 COM 仅返回 object ID 误当成内容层级已加载。每次都必须回读 actual parent；若全局 hierarchy snapshot 暂时不可见，还必须对同一返回 ID 做 exact-self 回读，严格证明类型、名称、非回收站状态和 parent，随后仍执行完整 live Recipe validation。Working Notebook/Section/Page ID 允许由 OneNote 重建，但必须按 role 内唯一的 Notebook-relative 类型化结构地址形成 old→live ID evidence，之后所有 validation/mutation 只使用 live ID。Programmatic miss 先构建并 live-validate 完整 fresh bundle，精确 close-all，生成逐 role per-file SHA-256 inventory 并原子发布，再从发布物 materialize 完整 working bundle；旧 receipt/hash 不能代替全部 role 的 live validation。多 Notebook 名称在 scenario 后增加 `source`/`destination` role。任一 working-copy open/activation 失败都保留整个 working bundle、逐 role lease 与 `materialized-hierarchy-open[-<role>].json`，lease 必须绑定已实际打开的 live working Notebook ID；这类 run-local 失败不污染已验证的 immutable template。active lease 冲突必须报告精确旧 run ID 和 working paths。ID rebind 或 live validator 失败仍会 quarantine exact entry。`--keep-worksite` 只保留该 run 的 working bundle 及 active lease，不写回 template，也不接管其他 run。

每个命令在 dispatch 时只读取一次主机本地时区，并冻结 run identity。Notebook、默认 run 目录以及 Copy/Move 目标名称共享 Windows-safe 的本地显示时间，例如 `2026-08-11-11-05-49`。完整本地 ISO 时间、UTC offset 和时区名称仍保存在 `run_identity`；JSON 中的 `created_at`、`failed_at`、`closed_at` 等事件字段仍使用 UTC ISO-8601。immutable template 继续使用内部 `template-notebook` 目录名，不作为 OneNote Notebook 打开。

Cache 固定为未纳入版本控制的 `.local-validation/fixture-cache/`。只有带 managed marker 的该根目录可被 cache runtime 操作；失效清理只允许精确 `(fingerprint, template_instance_id)` entry，并要求 root containment、ownership、无 reparse point且 template 实际路径未被 OneNote 打开。Run-local working Notebook 是物理独立副本，不参与 cache cleanup 门禁。`.one` 和 `.onetoc2` 只作为 opaque bytes 复制/散列，绝不解析、编辑或回写。模板从不由 OneNote 打开。

Manual-validation 的本地原子发布（cache entry、working directory、JSON/XML evidence 及 maintenance receipt/summary）在 Windows `WinError 5/32` 下使用状态守卫的短时退避：首次失败后最多等待 `50/100/200/400/800ms`，总预算约 1.55 秒。每次重试前必须证明 source 和 destination 与首次尝试时完全一致；任何出现、消失或身份变化都会 fail closed。该机制只处理本地 `os.replace` 的扫描/共享冲突，不重试文件删除、OneNote COM、MCP tool 或任何 mutation，也不放宽既有权限和人工门限。

Cache lookup 会区分真正不存在的实例与目录仍被保留的 `invalid` entry。历史上仅因 working-copy `materialized-open` 阶段被误隔离的 entry，可在原始 validation 与 byte inventory 重新通过时恢复；其余 `invalid` entry 必须先在 fingerprint lock 内通过上述安全门限执行精确清理，再以 `decision=invalidated_rebuild` 重建。该检查在首次 lookup 与 programmatic publish 前都会执行，避免并发隔离再次退化为发布冲突。`cleanup_failed`、缺失 ownership metadata、未知状态或 template 实际路径仍打开都会阻止重建；publish 始终拒绝覆盖任何现有实例。

2026-08-11 用户真实验证：layered Copy recipe version 2 将 fixture/live validator 与 Copy plan 统一到 live Page XML capability projection 后，`run-2026-08-11-13-31-57`、`run-2026-08-11-13-33-47`、`run-2026-08-11-13-37-37` 和 `run-2026-08-11-13-39-13` 使用同一旧版单 role `copy-page` fingerprint，依次覆盖 `decision=cold_build`、带 `--keep-worksite` 的 `decision=validated_hit`、执行默认 cleanup/restore 的 `decision=validated_hit`，以及带 `--keep-notebook` 的 `decision=fresh`。四次的 root-only case 都以 `strict_canonical` 验证单页 RichText/Table/Image，full-subtree case 精确映射父子两页，并分别以 `strict_canonical`、`semantic_list_tag` 验证父页和 List/Tag 子页；全部 Copy report 均为 `verified=true`、`lossless=true`，没有 issue 或 skipped content。cached run 证明 `opened_template=false`、template inventory 不变；默认 hit 与 fresh 都精确清理三个 Copy 目标并 `restored=true`，前者关闭 working Notebook，后者仅按 `--keep-notebook` 保留已恢复的 fresh 源 Notebook，且未生成 cache runtime artifact。四次都只启动一个 MCP process；总耗时/bridge calls 分别为 90.271 秒/279、66.802 秒/210、86.440 秒/274 和 84.381 秒/237。该矩阵闭合了 TODO 014 的单 role A 验收，但单机观测不能推广为固定性能提升比例。

2026-08-11 用户真实验证：recipe version 3 的双 Notebook、六 case 合同使用 fingerprint `ad0bf5be9c5eee60d0dfdebfca6cfa27a3dc5ae223f4dcb7327b5cee24736212`。`run-2026-08-11-14-27-08` 完成 cold build、逐 role live validation、关闭发布和重新 materialize，随后在 Copy 前因 runner 缺少 destination snapshot evidence 失败；该问题及重名 Page created-target 定位问题修复后，`run-2026-08-11-14-54-05` 与 `run-2026-08-11-14-57-01` 连续以 `decision=validated_hit` 完成全部六 case。两次运行的每个 Copy report 均为 `verified=true`、`lossless=true`，source/destination Notebook ID 互异，`opened_template=false`，且各自只启动一个 MCP process。前一次按默认语义反向清理六个根目标、两侧 `restored=true` 并关闭 working bundle；后一次以 `--keep-worksite` 保留全部六个目标和两个 working Notebook。用户确认不再补跑，TODO 014 阶段 B 据此闭合。

2026-08-11 用户真实验证补齐 working lease 的身份边界：`run-2026-08-11-19-07-17` 以 `decision=validated_hit` 和 `--keep-worksite` 保留第一组双 Notebook working bundle；在其 lease 仍为 active 时，`run-2026-08-11-19-10-38` 从相同 fingerprint/instance 再次 `validated_hit`，materialize 到另一 run directory，并为 source/destination 获得与第一组全部互异的 live Notebook ID。第二个 run 的六个 case、cleanup/restore 和双 Notebook close 独立通过，未关闭或修改第一组 worksite。该证据确认 fingerprint/instance 不是排他 lease key；只有实际 ID 集相交、同 ID 异路径或身份尚未可靠重绑定才拒绝。相反，`run-2026-08-11-18-46-59` 在 hierarchy activation 中途失败并保留未完成独立 live identity 建立的 bundle，`run-2026-08-11-18-50-54` 因实际 ID 冲突在 mutation 前精确拒绝；用户关闭旧 working Notebook 后，`run-2026-08-11-18-51-26` 完成 stale reconciliation、validated hit、六 case、cleanup/restore 和 close。结合既有两次 `invalidated_rebuild`，TODO 014 阶段 C 的并发隔离、真实 ID 冲突保护、关闭后恢复和受控失效证据据此闭合。

2026-08-11 TODO 015 增强复验：`run-2026-08-11-15-41-20` 的同标题 Create 返回两个 fresh、互异且 allocated/read-back 一致的 ID，正文独立可读，并完成默认非永久 cleanup、restore 和 close；`run-2026-08-11-15-43-26` 的 Move 返回两个 fresh target、`verified=true/lossless=true`、anchor unchanged，之后才按叶到根非永久删除源并关闭 Notebook。v4 `copy-page` 的 `run-2026-08-11-15-46-34` 暴露空 selection T 比较误报；`run-2026-08-11-16-06-07` 随后暴露同一占位符因转换顺序造成“目标标题 + 原标题”；`run-2026-08-11-16-11-01` 再暴露最终 restore 对无关 Description Page 后台重序列化比较过宽。三项均按严格保护对象边界修复。最终 `run-2026-08-11-16-18-20` 以同一 v4 fingerprint validated-hit，六个 case 按 `1/2/1/2/1/2` 映射 9 个 fresh、互异且与 source/anchors 不相交的 target；全部 `verified=true/lossless=true`，source/anchors 不变。默认反向清理 9 个 target 后 `restored=true`，source/destination 双 Notebook 均 closed，cache template inventories unchanged；全程只启动一个 MCP process。TODO 015 据此闭合。

当前保留的具体交互 recipe 和一个 bounded UserAuthored recipe 各有固定、不会进入 `all` 的 bootstrap Scenario。它们创建 fresh disposable Canvas/authoring zones，写 run-bound checkpoint，以有界 timeout 等待用户本人添加 synthetic 内容并给出精确 verdict；成功后关闭源、发布模板、再 materialize 第二份 working copy并 live validate。`--keep-worksite` 明确阻止发布。Agent、pytest、CI、hook 和后台进程不得执行这些真实命令。

交互 detector 只接受公开 Page 对象模型的 `kind`，并把 `Outline`/`OE` 作为结构支撑节点；请求类型必须精确匹配。TODO 004 已闭合 `InkDrawing`（自由墨迹）、OneNote UI Shape（形状）、通过“插入 → 录制视频”创建的 `MediaFile`，以及复用既有 ready fixture 的 `InsertedFile` Copy 证据。两次用户 discovery 证明矩形和箭头都公开为 `kind=InkDrawing`，而不是字面量 `kind=Shape`；两者共同含 `ShapeInfo`，箭头还含形状相关的 `AnchorPoint`。因此 Shape detector 固定要求“恰好一个公开 `InkDrawing` 对象 + content-free projection 中恰好一个 `ShapeInfo` + capability `UIShape`”，普通自由墨迹必须拒绝；`AnchorPoint` 作为可选结构保存并在 Copy 前后精确比较。`FileAttachment` 的专属 bootstrap/Recipe 已删除，因为当前 OneNote GUI 多次只生成 `InsertedFile`、无法形成独立可验证 fixture；`MeetingInfo` 的专属入口也已删除，因为内容小众、难生成且当前价值低。`Embedded Spreadsheet`（内嵌电子表格）同样明确不支持且没有专属入口；尚未观察到它的公开 `kind` 或 XML 表示，不得把它映射为 Table、InsertedFile 或 FileAttachment。三类排除项都不获得共享 Copy 合同或 Move 源删除放行。FileAttachment 的历史证据、`kind` 边界和观察环境只保留在 [`docs/lesson/onenote_page_object_kind_and_file_attachment_representation.md`](../../docs/lesson/onenote_page_object_kind_and_file_attachment_representation.md)；完整排除边界见 [`docs/lesson/copy_content_type_exclusions.md`](../../docs/lesson/copy_content_type_exclusions.md)。

用户确认后，runner 在验证前先写 `interactive-authored-snapshot.json` 和 content-free `interactive-detection.json`。后者固定记录 requested/observed/missing/unexpected/supporting、对象计数和 capability projection。失败时初始 `fixture-snapshot.json` 不被覆盖，Notebook 保持打开，cache 不初始化、不发布；错误摘要会显示精确类型和计数。

`interactive-copy-ink-drawing`、`interactive-copy-ui-shape` 与 `interactive-copy-media-file` 是各自 bootstrap 的 cache-only、Copy-only consumer。它们只接受固定 `ready` instance，materialize 后先重跑精确 detector，再用连续两次相同的只读 plan 绑定 source/destination。InkDrawing/UIShape 各执行一次 root-only Page Copy；MediaFile 在同一场景中先复制到原 Section，再创建一个 run-bound 新 Section 并执行第二次 root-only Copy。第二个 case 必须证明第一个 target 和 source 都未变化，两个 target 分别写入独立 plan/copy/machine-comparison evidence，最后一个 run-bound 人工 verdict 同时确认两者的显示与播放。静态 policy 只有 Writes + Experimental Copy；MediaFile 为创建精确目标 Section额外允许 `create_section`，但仍没有 Delete、Move、Permanent Delete 或 Raw XML。Copy targets 永远保留在 disposable working artifact。若 `copy_page` 已精确创建唯一 target、源未触碰且唯一失败是 canonical read-back，场景会把结构化 `partial_failure` 作为诊断证据继续处理，而不是把它当作生产成功。MediaFile 仍要求 strict canonical；InkDrawing 使用 `semantic_ink_drawing`：请求/目标 detector、公开对象稳定签名、InkDrawing 节点结构、非几何属性和 Ink 数据 hash 必须精确一致；仅 `Position.x/y/z` 与 `Size.width/height` 允许基于真实 COM 量化证据的 `1e-4` 绝对容差。UI Shape 的 `semantic_ui_shape` 复用相同的 Decimal 逐字段 comparator、结构/data hash 和失败证据，但基于 `run-2026-08-11-22-18-48` 的真实 Shape bounding-box 重算证据使用独立 `0.02` 绝对容差；同时额外要求 source/target 各有一个 `ShapeInfo`、完整 shape marker 计数相等，因此矩形与箭头的 `AnchorPoint` 差异不会被动态忽略。evidence 固定记录每个几何字段的 source/target、absolute delta、最大 delta 和是否越界；非数字、缺失/额外字段或越界一律失败。visible text、content-object/binary checks、无 omitted content 和受限 issue 集也必须通过。Ink 子树之外的 Page canonical 漂移会被记录但不单独否定 Ink/Shape 证据。证据只保存 count/hash，并固定记录 `payloads_exposed=false`，不落盘 raw payload。随后用户必须对精确 target 给出 run-bound `ACCEPT` 或 `REJECT`；机器或人工任一失败都保留现场并且不产生放权。

`bootstrap-shape-fixture` 的 recipe version 5 已把上述真实 representation 冻结为可缓存的交互 fixture。它只接受一个小矩形作为稳定 fixture；discovery run `run-2026-08-11-21-57-18`（矩形）和 `run-2026-08-11-22-00-08`（箭头）是历史 `evidence_only` 证据，不会自动升级为 cache entry。后续 bootstrap 和 `interactive-copy-ui-shape --use-cache` 已完成；最终 `run-2026-08-11-22-23-29` 通过 `semantic_ui_shape`、完整 Shape marker/data 比较、`0.02` 有界几何门和人工 verdict，`UIShape` 已进入生产 validated allowlist 并可复用共享 Move 门。

`copy-display-equation` 是不读取 stdin、无需 bootstrap 或人工 verdict 的程序化场景。Recipe 会在 fresh 或显式 `--use-cache` 的 disposable working Notebook 中构建 `Source/01-Source-Parent` 富文本/表格/图片基线，并自动追加一个 `display="block"` 的独立单行 MathML。Fixture detector 要求 capability projection 明确包含 `DisplayEquation` 且恰好有一个完整 standalone MathML root；随后固定执行三跳 root-only Page Copy。每一跳都必须由生产 comparator 返回 `semantic_display_equation`、`verified=true`、`lossless=true`、`copy_contract_satisfied=true`，发送前恰好清理一个空白 span 和一个 break，目标回读仍只有一个已知空白 span/break。默认按逆序非永久清理三个 target 并验证原始 fixture 恢复；`--keep-worksite` 才保留三个目标供检查。场景不保存 Page 正文或 raw XML。

2026-08-12 用户运行 `run-2026-08-12-10-45-04` 取得未清理链的真实证据：Bootstrap source 已有一个空白 span/br，Copy 每跳在同一 span 内增加一个 br，形成 `1 → 2 → 3 → 4`；Outline/OE/T 数量、可见内容和 MathML hash 均保持不变。生产 Copy 因而把 standalone block MathML 单独建模为 `DisplayEquation`：每次写入前移除紧邻公式的整个纯空白 span 及全部 break，读回 comparator 只允许公式前没有包装或一个纯空白 `span + br`。修复后的 `run-2026-08-12-11-28-08` 三跳全部返回 `semantic_display_equation`、`verified=true`、`lossless=true`、`copy_contract_satisfied=true`；每跳发送前清理一个 span/break，目标均稳定回读一个 break，即 `1 → 1 → 1`。出现第二个 break、额外包装/markup、可见正文、MathML、对象或二进制差异仍 fail closed；InlineEquation 不受该规则影响。这里的 lossless 表示受限语义保真，不代表 CDATA 字节完全一致。

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-display-equation --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py copy-display-equation
.venv\Scripts\python.exe tests\manual_validation\run.py copy-display-equation --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py copy-display-equation --keep-worksite
```

`bootstrap-inline-equation-fixture` 与 `interactive-copy-inline-equation` 是用于对照 block MathML 空行问题的独立 recipe 对。Bootstrap 自动构建相同的 Source Parent 富文本/表格/图片基线，并通过受限 HTML 写入一个无 `display` 属性、前后均有普通正文的 Presentation MathML；用户不编辑 Page，只确认自动 fixture 在一行内正常显示。Detector 固定要求恰好一个完整 MathML、同一 `OE` 内存在可见前后文（可为同一个 `T`，也可为 OneNote 规范化产生的 `T(before) → T(math) → T(after)`）、零 `display` 属性和零相关 `<br>`。Copy consumer 要求 source/target 都继续满足相同 inline 门禁，任何公式周围新增 `<br>` 都单独失败；配合 `--capture-page-xml` 可直接保存两侧 XML。

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-inline-equation-fixture --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-inline-equation-fixture
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-inline-equation --use-cache --capture-page-xml --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-inline-equation --use-cache --capture-page-xml
```

TODO 004 的首次真实取证必须逐类型串行执行，任一非零结果立即停止并保留现场。InkDrawing 先发布精确 fixture，再消费 immutable cache 做一次 Copy：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-ink-drawing-fixture --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-ink-drawing-fixture
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ink-drawing --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ink-drawing --use-cache
```

Bootstrap Canvas 只用 OneNote Draw pen 画一条很短的 synthetic freehand stroke，不选择 Shapes、不添加第二个对象。Copy 阶段在终端显示精确 target title；用户比较笔迹、位置和大小后输入该 run 显示的 `ACCEPT ... InkDrawing COPY` 或 `REJECT ... InkDrawing COPY`。

MediaFile v8 使用 OneNote `Insert → Record Video` 创建一段 1–2 秒的 synthetic video recording；不要拖放、附加已有媒体文件或使用“文件附件”，否则可能得到不同的 `InsertedFile` 表示。录像结构先由 bootstrap detector/projection fail closed 验证；若出现尚未建模的节点，必须保留失败现场后再按真实证据更新，不能继承音频表示的假设：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-media-file-fixture --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-media-file-fixture
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-media-file --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-media-file --use-cache
```

真实 bootstrap `run-2026-08-11-22-26-30` 与 `run-2026-08-11-22-30-44` 证明录音操作公开一个 `kind=MediaFile`，并生成 `Page/MediaPlaylist/MediaReference`、同一含 MediaFile 的 Outline 中的 `OE/MediaIndex/MediaReference`，以及 `OE/MediaFile/MediaReference`。后续 `run-2026-08-11-22-35-53` 的 authored detector 和人工 verdict 已通过，但 template materialize 后 OneNote 将一个录音时间轴 OE 规范化为直接子节点 `MediaIndex + T`，其中 T 只含 `span` 富文本，旧 detector 因额外 `RichText` 在 live revalidation 阶段 quarantine v5 entry。recipe version 6 将 `MediaPlaylist` 作为 MediaFile 支撑根，只在上述媒体关联路径接受 `MediaIndex/MediaReference`，并且仅把“同一 Outline 存在 MediaFile、OE 直接结构恰好为 MediaIndex+T、标签集合恰好为 span”的 T 视为媒体时间轴支撑。普通富文本标签、额外 OE 子节点或没有同 Outline MediaFile 的 RichText 仍单独分类并拒绝。v6 bootstrap `run-2026-08-11-22-44-14` 发布 ready template；在修复 raw plan hash 漂移和失效 `pathSource` 后，同 Section consumer `run-2026-08-11-23-03-40` 的 strict canonical、detector、对象签名、人工播放验收和默认关闭全部通过。recipe version 7 将同一 consumer 扩展为同 Section + 跨 Section 两个 case；在尚未发布 v7 cache 前，version 8 又将新 fixture 明确改为录像，用于验证 Video MediaFile 的真实 XML 结构与两种目标拓扑。旧音频 cache 均不匹配，必须重新 bootstrap。

v8 录像 bootstrap `run-2026-08-11-23-21-38` 已发布 ready template 并通过 materialized live revalidation；projection 为一个 `MediaFile`、一个 `Outline`、七个 `OE`，无 unknown/unsupported。`run-2026-08-11-23-23-16` 随后从 validated cache 同时完成同 Section 与跨 Section 录像 Copy：两个机器 comparator 都通过 strict canonical、detector、对象签名和无 omitted content，用户对两个可播放目标给出同一个 run-bound `ACCEPT`，源未删除且 working Notebook 默认关闭。用户之后在 OneNote UI 中人工删除源 Section，并确认两个 Copy 仍然有效。结合已闭合的 InkDrawing/UIShape 证据，当日这三类进入静态生产保真 allowlist；2026-08-12 的 InsertedFile Copy 证据又把第四类加入同一集合。consumer 复验时允许生产结果直接无 issue 且 `lossless=true`，不再要求出现历史 `content_type_unverified`。

Copy 阶段除视觉对象外还应在 OneNote UI 中播放 source/target，确认两者都能播放且持续时间/控件表现一致，再输入 run-bound verdict。机器 evidence 会记录 COM Page XML 中可回读的 binary payload count/hash，但不会保存 raw payload；零 payload 不能写成已经证明二进制相等，只能结合 canonical metadata 与人工播放 verdict 评审。

UI Shape 使用一个小矩形作为固定 fixture；不要在 bootstrap 中改用箭头，因为箭头的 `AnchorPoint` 结构属于另一种形状变体：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-shape-fixture --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-shape-fixture
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ui-shape --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ui-shape --use-cache
```

在 Canvas 中只用 `Draw → Shapes` 添加一个小矩形，不画 freehand ink、不添加正文或其他对象。Bootstrap 必须报告 `observed={InkDrawing: 1}`、`shape_info_count=1`、`representation_status=requested_composite_observed` 后才可发布 cache。Copy 阶段肉眼比较矩形的形状、边框、位置和大小，再输入 run-bound `ACCEPT ... UIShape COPY` 或 `REJECT ... UIShape COPY`。

固定 `cache-invalidation --use-cache` 只绑定自己的 programmatic Recipe fingerprint/instance，不接受任何 path、ID 或 fingerprint 参数。若已有 entry，它会在 materialize/open 之前精确失效；若是 cold miss，则先发布受验证 entry、立即对该精确 entry 执行同一清理门，再重新发布并 materialize。cleanup tombstone 必须证明 cache-root containment、ownership、无 reparse point且 template 实际路径未打开；run-local working lease 不参与 cache cleanup 判断。任何清理失败都会停止且不覆盖。

`user-authored-fixture-consumer --use-cache --template-instance-id authored-<24 hex>` 与 bootstrap 共享同一 contract fingerprint，但拥有独立 Scenario/Recipe instance。Consumer 不枚举或猜测实例；缺失、格式错误、未知实例以及 `evidence_only` 都在 working Notebook 打开前 fail closed。省略 `--use-cache` 的 dry-run 只报告 `preflight-cache-required`，真实执行也会在 lifecycle/MCP/cache 访问之前拒绝；只有显式选择的 `ready` 实例会 materialize，并再次通过 reserved marker、authoring-zone 和 live content validation。该能力当前定位为足够开发取证使用的临时脚手架；完整 authoring-zone、多实例和状态真实矩阵已作为低优先级 [TODO 020](../../docs/todo/020_user_authored_fixture_development_scaffold.md) 单独维护，不再阻塞 TODO 014 或生产 Copy/Move。

`interactive-copy-inserted-file` 是 cache-only、HUMAN-GATED 的 Copy 验证 Scenario，与 `bootstrap-inserted-file-fixture` 共用同一个 fingerprint 和固定 instance。它不重新创建 fixture，也不进入 `all`；命中后 materialize 全新的 working copy，显式加载层级、重绑定 live ID、重跑 `InsertedFile` detector/projection，然后执行一次同 Section root-only `copy_page`。机器门要求 source/target 都精确观察到一个公开 `kind=InsertedFile`、稳定对象签名一致、可见文本/内容对象/strict canonical read-back 通过且没有 omitted content；当前公开 XML 没有内联 `Data`，因此普通 binary SHA-256 项只记录其 absence，用户还必须实际打开目标附件并确认其合成文件内容一致，再输入 run-bound `ACCEPT ... InsertedFile COPY`。Copy plan 优先使用仍存在的 `pathSource`，否则回退到可读 `pathCache/path`；全部不可读时在创建目标前 fail closed，普通 evidence 不保存实际路径。场景没有 Delete 权限，不会删除源。缺少可命中的 `ready` entry 时会在 Notebook/MCP 启动前返回 `interactive_bootstrap_required` 并提示运行已有 bootstrap，而不是隐式重建：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-inserted-file --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-inserted-file --use-cache
```

2026-08-11 用户真实验证：旧 cache-only detector consumer 的 `run-20260811T022911Z` 以 validated hit 观察到精确 `InsertedFile=1`，完成 live materialized revalidation、证明 template 未打开并关闭 working Notebook；`run-20260811T023122Z` 的 bootstrap 完成人工 ACCEPT、发布 ready template，并在 ID 全部重建的第二份 working bundle 上完成结构重绑定和二次 live validation。后续两次使用过长物理名称的旧 consumer 在 Notebook folder 首次 `OpenHierarchy` 上返回 `0x80042006`；命名缩短为 `__<scenario>-<?CACHED>-<YYYY-MM-DD-HH-MM-SS>__` 后，`run-2026-08-11-12-30-34` 与 `run-2026-08-11-12-31-13` 连续以 `decision=validated_hit` 通过 hierarchy open、live materialized revalidation、`InsertedFile=1` 和 `opened_template=false` 证明。

2026-08-12 用户执行的 `run-2026-08-12-12-34-58` 复用同一 ready cache，完成同 Section root-only Copy；source/target detector 均精确观察到 `InsertedFile=1`，strict canonical 的 canonical XML、可见文本、内容对象和 binary 项全部通过，无 omitted content，机器 comparator passed。用户实际打开目标附件确认合成内容一致并提交 run-bound ACCEPT，working Notebook 随后正常关闭。该证据已用于把 `InsertedFile` 加入生产 validated Copy 集合；它仍不代表程序化创建能力，也不改变 FileAttachment 的独立未验证状态。

失败 run 的 working Notebook 被用户手动关闭后，下一次 cache consumer 会在短时 open lock 内用只读 COM ID/path probe 忽略已关闭的历史 active lifecycle lease；它不会修改该历史 lease，也不会接管或关闭仍然打开的旧 working Notebook。

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

Working identity 冲突扫描在短时 open lock 内于打开 working bundle 前后各捕获一次当前 Notebook ID/实际目录 snapshot；全部历史 run-local lease 只与 snapshot 做内存比较，历史 run 数量不得放大 COM 调用次数。Snapshot 获取失败按 MCP/lifecycle failure fail closed，并保留本次 working 现场。

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

<!-- dry-run-case: move-section.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py move-section --dry-run --json
```

<!-- dry-run-case: move-section-group.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py move-section-group --dry-run --json
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

<!-- dry-run-case: bootstrap-shape-fixture.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-shape-fixture --dry-run --json
```

<!-- dry-run-case: copy-display-equation.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-display-equation --dry-run --json
```

<!-- dry-run-case: bootstrap-inline-equation-fixture.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py bootstrap-inline-equation-fixture --dry-run --json
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

<!-- dry-run-case: interactive-copy-inserted-file.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-inserted-file --dry-run --json
```

<!-- dry-run-case: interactive-copy-ink-drawing.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ink-drawing --dry-run --json
```

<!-- dry-run-case: interactive-copy-media-file.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-media-file --dry-run --json
```

<!-- dry-run-case: interactive-copy-ui-shape.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-ui-shape --dry-run --json
```

<!-- dry-run-case: interactive-copy-inline-equation.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py interactive-copy-inline-equation --dry-run --json
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
- 普通 Scenario 永不删除 run-scoped 本地 Notebook 目录、Notebook Copy 目录、普通 artifact 或失败现场。只有上述用户显式确认的 `clear` maintenance action 可以按逐目标安全门删除受管 payload；该授权不覆盖用户 Notebook 或任意外部路径。
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
| Page Move | 固定双 Notebook `source`/`destination` bundle，只覆盖跨 Notebook 两个 case：root-only 省略 `include_descendants`，subtree 显式为 `true`。前者只复制/删除根 Page，并要求被排除子页在源 Section 中提升一级、ID 与内容不变；后者复制两页并按叶到根非永久删除两页。Move 场景只审计 verified Copy→安全删除组合，不重复内容类型 comparator；`--keep-worksite` 保留三个目标 Page 和双 Notebook供 UI 检查 |
| Section/SectionGroup Move | 两个独立、已进入 `all` 的双 Notebook 场景；容器完整递归复制到 destination Notebook 根，要求完整单射 `id_map` 与 verified/lossless Copy，然后只允许一次对应 typed 根删除且固定非永久。after snapshot 必须证明全部原源子树 ID inactive、全部目标 ID 仅位于 destination role；不重复未验证 content type comparator |
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

## Section 与 SectionGroup Move

`move-section` 与 `move-section-group` 只覆盖跨 Notebook 重建式 Move，不覆盖同 Notebook 父级变化；后者已经由 `reparent-section` / `reparent-section-group` 负责。两个场景都使用 source/destination 两个全新 disposable Notebook，目标父级固定为 destination Notebook 根，源端分别是“一 Section + 一 Page”和“一 SectionGroup + 一 Section + 一 Page”的最小树。

生产计划必须返回 `operation=move_section|move_section_group`、精确源子树 snapshot，以及不同的 source/destination Notebook IDs。执行只消费生产 Copy 的 `verified/lossless` 结论和完整单射 `id_map`，不承担附件、墨迹、形状或媒体 comparator。Copy 和源 digest 重校验通过后，Section/SectionGroup 路径都只能调用一次对应 typed 根删除，公共 Move tool 不接受 `permanently`，service 固定传入 `false`。after snapshot 要求计划中的根及全部后代 ID 从 source role 消失，全部新 ID 只出现在 destination role；目标复核或任何删除证据不完整都会非零退出并保留双 Notebook 现场。

2026-08-11 用户真实运行结果：`run-2026-08-11-20-31-28` 的 `move-section --use-cache` 与 `run-2026-08-11-20-33-29` 的 `move-section-group --use-cache` 均为 `status=passed/outcome=moved`。两个 Copy report 都是 `verified=true/lossless=true`、映射完整且无 skipped content；各自只尝试删除一个源根，全部计划源 ID 均 inactive，`remaining_source_ids=[]`，source/destination lease 最终均关闭。Section 运行取得 `source_deleted_to_recycle_bin=true`；SectionGroup 运行的 COM 不暴露回收站元数据，因此只记录 `not_required_com_unavailable`，不影响活动态缺席门。该证据只覆盖最小 Outline/RichText fixture 与当前环境。

## Page Move

`move-page` 的语义是对显式范围执行重建。省略 `include_descendants` 时只选择根 Page；显式为 `true` 时选择完整缩进子树。场景固定使用两个 Notebook，并只覆盖 `cross-notebook-root-only` 与 `cross-notebook-subtree`：不再重复同 Notebook 跨 Section，因为该位置变化已经由 typed `reparent-page` 验证。

Move 场景不负责附件、墨迹、形状、媒体或其他内容类型的独立保真取证；这些结论由 Copy 场景及其逐类型 comparator 负责。Move 只要求生产 Copy 报告 `verified=true/lossless=true`，并验证实际 `id_map` 与选定范围一致，然后审计安全删除：root-only 只允许删除根 Page，并要求被排除子页先整体提升一级且保持 ID、Section、相对层级和内容；subtree 必须按叶到根删除父子两页。任何 Copy、提升、快照重校验或删除证据不完整都非零退出并保留现场。

2026-08-11 用户真实运行 `run-2026-08-11-20-29-19`（`move-page --use-cache`）通过两个固定 case。root-only 只映射并删除根 Page，被排除子页保持原 ID、仍活动并通过提升后验证；subtree 映射并非永久删除父子两页。两个 case 均为 `copy_verified=true/source_deleted_nonpermanently=true`，三个新目标只属于 destination role，两个 lifecycle role 最终均关闭。该运行确认了修复后的稳定内容摘要不会因预期的 `pageLevel`/时钟变化误报，同时仍保持独立拓扑与正文门。

选定源 Page 只通过 `DeleteHierarchy(permanently=false)` 非永久删除。生产删除服务会有界回读每个精确 ID：对象必须从活动 hierarchy 消失，或者明确带 `is_in_recycle_bin=true`；仍活动则失败。工具成功后，manual scenario 的双 Notebook `after-<case>.json` 还会独立确认选定源已消失、目标只在 destination role 中出现，并对 root-only case 确认排除子页仍活动且稳定。由于实际环境可能在 OneNote UI 的“已删除的笔记”中显示源 Page、但 COM hierarchy 不返回其旧 ID，回收站标记是可选诊断信息，不是成功关口。逐 case `copy-result-*.json` 与最终 `restored.json` 会记录 `recycle_bin_verification`、源/目标 IDs 和保留后代证据；用户仍需在 UI 中人工检查和清理。

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
├─ fixture-equation-detection.json # copy-page v8 公式 fixture 最终回读，成功与失败均先写入
├─ notebooks/                 # 始终保留
├─ notebook-copies/           # 若创建，始终保留
└─ scenarios/<scenario>/
   ├─ before.json
   ├─ plan-attempts.json / plan.json / copy-result.json
   ├─ copy-page/copy-section/copy-section-group: plans.json + plan/before/copy-result/after-<case>.json
   ├─ after.json / restored.json / worksite.json
   ├─ copy-notebook: close-confirmation.json（默认关闭副本时）
   └─ result.json 或 failure.json
```

唯一 MCP 的 content-free bridge audit 位于 `scenario-mcp/bridge-calls.jsonl`；只记录 operation、成功状态、时间和耗时，不记录参数、OneNote 内容或返回值。`fixture-result.json` 的 `validation` 段记录 profile topology/content invariants 的实际通过证据。

Copy mutation 前会有界执行最多三次只读 `plan_copy`，只有连续两次 `plan_digest` 完全一致才继续；每次摘要、source modified 和有效 `include_descendants` 写入 plan-attempts 证据。`copy-page` 为六个静态 case 分别写入 `plan-attempts-<case>.json`、`plan-<case>.json`、`before-<case>.json.plan_binding` 和 `copy-result-<case>.json`；三个 root-only case 在 plan/execute 中均省略范围值并要求回显有效值为 `false`，三个 subtree case 显式提交并要求回显 `true`。`copy-section` 与 `copy-section-group` 使用同一逐 case evidence 形状，分别写 `same-notebook` 与 `cross-notebook` 两组文件。Runner 在每次 mutation 前复核目标父级、目标 role 和范围，并从 source/destination 两侧最新快照合并下一 case 的 before evidence，从而把多次 mutation 的增量逐项隔离。这用于等待 fixture 写入引发的 COM 容器时间延迟传播，不重试任何 mutation；任一 case 三次仍不稳定就会在该次写入前 fail closed。

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
