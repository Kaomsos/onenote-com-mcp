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

`copy-page` 在一次隔离运行中依次执行两个独立 case：`root-only-default` 在 plan 与 execute 中都省略 `include_descendants`，证明默认值只复制 `01-Source-Parent`，且 `02-Source-Child` 仍以原 ID、父 Page、level、order 和内容 hash 留在源 Section；`full-subtree` 显式提交 `include_descendants=true`，证明 Parent 与 Child 都进入 plan snapshot 和 `id_map`，并在目标中保持父子关系与相对层级。fixture 另建 `00-Description/00-Copy-Page-Description`，逐项说明原始状态、两种目标状态和默认清理状态。使用 `--keep-worksite` 时，`01-Root-Only-Copy-*` 与 `02-Full-Subtree-Copy-*` 会同时保留供 UI 对照。Section/SectionGroup/Notebook Copy 与 Page Move 仍选择完整两页子树。整个过程不暂停、不要求用户编辑，也不启用 raw XML。第二层忽略 COM 重新编号 `TagDef`、列表序号状态和 Outline 布局重排，但仍严格比较可见文本、列表种类、标签类型、完成状态和二进制内容。`List/Tag` 已进入 validated/lossless allowlist；这表示其保真结论由 `semantic_list_tag` 而不是 canonical XML 相等来证明。`MeetingInfo` 暂不属于验证范围。

唯一特殊入口 `all` 会按显式 `included_in_all` 资格的顺序串行启动其中的 scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

唯一注册表对象位于 `scenarios/common/registry.py` 的 `SCENARIO_REGISTRY`。每个场景类使用 `@SCENARIO_REGISTRY.register` wrapper；`scenarios/__init__.py` 按审查后的固定顺序导入所有公开场景，导入时自动完成实例注册。Registry 本身不导入具体场景，也不维护第二份构造列表。新增公开 scenario 默认 `included_in_all = False`；只有经过稳定性和权限审查才可改为 `True`。`get_all_scenario_names()` 只表示用户真实 `all` 批处理资格，不能用于 pytest collection。

每个 Scenario 同时显式持有一个 `fixture_recipe`。场景专属 Description、构建与 validator 位于 `scenarios/fixture_recipes/<scenario>.py`；`common/fixture_runtime.py` 只负责 recorder、snapshot、通用 profile 检查和证据持久化，不按 scenario 名称分派。Recorder 在每个精确 ID 创建后增量写入 pending manifest；中途失败会保留已登记 ID、lifecycle lease 路径、Notebook 路径和 failed validation handoff。Copy/Move 只共享不含 scenario 名称分支的 layered Page component。

同一个 Scenario registry 还导出冻结的 `dry_run_cases`：每个公开场景自动拥有 `default` 与 `keep-worksite` case，Rename 和 Page Reorder 在自身类中声明有限参数变体，另有 runner 级 `all.default`。pytest 使用正式 parser、无 I/O pure plan builder 和 side-effect sentinel 运行 catalog；`included_in_all=False` 不影响 dry-run 收集。Case 不得携带 `--dry-run`、`--json`、`--run-dir` 或授权参数，这些值只能由 harness 强制注入。

`all` 本身不是 scenario，不创建共享 Notebook 或共享证据目录，也不接受 `--run-dir`、`--notebook-name`、`--keep-notebook` 或 `--keep-worksite`。每个已注册子命令仍创建自己的默认 Notebook 和 `.local-validation\run-<TIMESTAMP>`，使用自己的 MCP 子进程、最小权限、报告与关闭/失败保留语义。一个 scenario 失败后，`all` 会显示其错误并继续后续已注册 scenario，最终返回第一个失败的非零退出码。

每个命令本身就是一次完整的隔离闭环，只运行所选 scenario：

```text
create fresh isolated Notebook through the narrow lifecycle wrapper
→ start exactly one scenario-scoped MCP process
→ create only that scenario's fixture and run exactly the selected scenario
→ write local evidence report
→ close the exact leased source Notebook（默认）或 keep open
```

内部 scenario 类、`scenarios/common/report.py` 以及其他共享库不能被直接调用或组合成另一个隐式入口；公开的 `create` 仍只通过统一 parser 作为完整 scenario 运行。

`create` scenario 按以下预设结构创建隔离 Notebook，不执行额外 mutation：

```text
Group-A
└─ Content-Section
   ├─ Parent        rich text + table + image
   ├─ Child         pageLevel=2
   └─ Sibling       pageLevel=1
Group-B
Delete-Sandbox
├─ Disposable-Group
└─ Disposable-Section
   └─ Disposable-Page
```

随后生成 manifest、`prepared.json`/`fixture-snapshot.json`、`fixture-result.json` 和 report，并按默认 close、仅保持打开的 `--keep-notebook`，或写出现场证据的 `--keep-worksite` 处理生命周期。

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
  [--notebook-name <name>] `
  [--run-dir <path>] `
  [--keep-notebook] `
  [--keep-worksite] `
  [--timeout <seconds>] `
  [--dry-run] `
  [--json]
```

- 默认 Notebook：`__LOCAL_MCP_TEST_ISOLATED__<UTC_TIMESTAMP>`。
- 默认目录：`.local-validation\run-<同一个 UTC_TIMESTAMP>`。
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

每个 scenario 都在本次新建的 disposable Notebook 中运行，并最多启动一个 MCP 子进程。源 Notebook 的 create/get/close 由窄 lifecycle wrapper 完成；wrapper 不提供 Section、Page 或内容写入能力。创建后立即写入 `lifecycle-lease.json`，绑定本次 run 的精确 Notebook ID、名称和本地路径。

唯一 MCP 子进程同时完成该 scenario 的最小 fixture、所选 mutation、before/after/restored 回读和契约内 restore/cleanup。它启动时使用 `scenarios/common/specs.py` 中固定的完整闭包 policy 和 tool allowlist，并在 fixture 创建前用 `health_check` 精确核对 policy、timeout 和 Copy budget；启动后不得扩权。Runner 不使用所有 scenario 的权限并集。

| Scenario | Fixture 与权限限制 |
| --- | --- |
| `create` | 完整预设 fixture；仅 typed fixture 写入和读取，不暴露 `create_notebook`（Notebook 由 wrapper 创建）；`--keep-worksite` 记录整个 fixture Notebook |
| `rename` | 一个选定 Group/Section；fixture 写入加对应 rename 工具；`--keep-worksite` 保留新名称并记录原名称 |
| `reorder-page` | `Description/00-Reorder-Description` 明示操作前 `01,02,03`、正向操作后 `01,03,02`、恢复后 `01,02,03`；`01-Reorder-Page-Section` 下使用 `01-Parent`、`02-Child`、`03-Sibling`，让 UI 顺序和缩进变化可直接肉眼验收；`--keep-worksite` 保留 `01,03,02` 与新 predecessor/level |
| `reorder-section` | `00-Description/00-Reorder-Section-Description` 分别说明 Notebook 父级和 `01-Section-Parent`（SectionGroup）父级的 before/after/restore；两组 Section 及其 Page 均使用 `01/02/03` 编号，UI 可直接核对 `01,02,03 → 01,03,02 → 01,02,03`；只开启 Writes 与 Section Reorder；用户已确认真实 UI 排序证据 |
| `reorder-section-group` | **功能受限 / 验证失败 / 不注册到 `all`**。保留完整 fixture、mutation 和写后回读实现作为可单独调用的诊断场景；真实后端对 Notebook 直属 Group 返回 UpdateHierarchy 成功但保持按名称固定升序，嵌套操作未执行。dry-run 和运行状态证据写入 `capability_assessment={capability_status: limited, validation_status: failed, ...}`。 |
| `reparent-section` | `00-Description/00-Reparent-Section-Description` 说明三种 before/after/restore：`01-Notebook-To-Group-Section` 从 Notebook 根换父级到 `01-Destination-Group`，`02-Group-To-Notebook-Section` 从 `02-Source-Group` 换父级到 Notebook 根，`03-Group-To-Group-Section` 从 `03-Source-Group` 换父级到 `03-Destination-Group`。三个 Section 及其 Page 均编号；每次 Reparent 后刷新快照，验证 ID、父级、Page 拓扑和内容，默认逆序恢复，`--keep-worksite` 保留三项目标父级。只允许同一 Notebook。 |
| `reparent-page` | **typed 实验工具 / 用户确认迁移后真实验证通过 / 不注册到 `all`**。通过 `reparent_page` 提交精确 ID 与 confirmation，不要求 Raw XML。`00-Description/00-Reparent-Page-Description` 解释原生 `UpdateHierarchy` 与 ID 重映射；`01-Source-Section/01-Reparent-Page` 同页包含 Rich Text、Table、List、Tag、Image，再请求改属 `02-Destination-Section`，编号锚点保持无关。工具与 runner 双层验证 Page/内容对象一对一映射、富内容和无关对象；默认使用新 ID 逻辑移回，或由 `--keep-worksite` 保留。 |
| `reparent-section-group` | **typed 实验工具 / 用户确认迁移后真实验证通过 / 不注册到 `all`**。通过 `reparent_section_group` 提交精确 ID 与 confirmation，不要求 Raw XML。三组编号 Group/Section/Page 覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup；要求目标及后代 ID、关系、Page 内容保持。默认按 `03→02→01` 逆序恢复，`--keep-worksite` 保留三组父级。 |
| `delete` | Delete-Sandbox 与 allowlisted disposable group；写入加非永久 Delete，永久删除关闭；`--keep-worksite` 保持 Notebook 打开并记录回收站目标 |
| Page Copy | `00-Copy-Page-Description` 明示原始与两种目标状态；case 1 省略参数验收默认仅根页，case 2 显式 `include_descendants=true` 验收完整两页子树；每个 case 分别稳定 plan、执行、回读并断言源端不变；默认清理两个目标，`--keep-worksite` 同时保留两种目标供 UI 对照 |
| Section/Group Copy | 对应最小源和目标；源容器含严格富内容父页与三个混合 List/Tag 项的语义子页，并继续递归复制完整子树；默认执行可恢复清理，显式 `--keep-worksite` 在 after/mapping 验证后保留精确目标 ID |
| Notebook Copy | 最小 Notebook 同样包含严格父页和 List/Tag 语义子页；Copy 开启、Delete 关闭，默认关闭副本；显式 `--keep-worksite` 保持副本打开并记录路径 |
| Page Move | disposable 源 Page 与目标 Section；仅开放专用 experimental/copy/delete 闭包；`--keep-worksite` 记录 active Copy、非永久删除的 source ID 与回收站诊断状态 |
| Report | 只读取本地 artifacts，不启动 MCP |
| Source lifecycle | wrapper 仅支持 `create_fresh_notebook`、精确 get/close；不启动额外 MCP |

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

`move-page` 的语义天然是重建：Copy 完整 Page 子树、验证新对象，再对源 Page 执行非永久删除。场景始终运行严格门禁，不会跳过或降级；它使用严格父页和 List/Tag 语义子页验证整棵 Page 子树。当前 validated 保真类型为 `Outline/Image/RichText/Table/List/Tag`；出现尚未确认的 `MeetingInfo`、附件、墨迹、媒体或未知结构时，场景仍可能返回 `copy_only`、保留源 Page，或因保真门未通过而非零退出。

源 Page 只通过 `DeleteHierarchy(permanently=false)` 非永久删除。生产删除服务会有界回读每个精确 ID：对象必须从活动 hierarchy 消失，或者明确带 `is_in_recycle_bin=true`；仍活动则失败。工具成功后，manual scenario 的 `after.json` 还会独立确认整棵源子树不再活动。由于实际环境可能在 OneNote UI 的“已删除的笔记”中显示源 Page、但 COM hierarchy 不返回其旧 ID，回收站标记已降为可选诊断信息，不再是成功关口。`copy-result.json` 和 `restored.json` 会用 `recycle_bin_verification`、`recycled_source_ids`、`recycle_unverified_source_ids` 区分“已取得标记”和“COM 未暴露标记”；后者仍需用户在 UI 中人工检查和清理。

如果 OneNote UI 已切换到回收站中的源 Page，可在启动真实 scenario 的同一个普通用户 PowerShell 会话中运行只读诊断脚本，对比窗口 API 返回的当前 Page ID 与原始 ID。脚本优先连接 ROT 中的活动 OneNote 对象，不执行导航、写入或删除，也不输出 Page 正文：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\inspect_current_onenote_page.ps1 `
  -ExpectedPageId '{SOURCE-PAGE-ID}'
```

`matches_expected_page_id=true` 表示回收站页面保留原 ID；`false` 表示当前回收站页面使用了不同 ID。若 `page_metadata.readable=false`，Current Page ID 的比较仍然有效，只是 `GetPageContent` 不接受该回收站 ID。

失败时不会关闭源 Notebook，也不会删除文件。`copy-result.json` 和失败交接会记录 `outcome`、`created_ids` 与 `id_map`，供用户人工核对。

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

Copy mutation 前会有界执行最多三次只读 `plan_copy`，只有连续两次 `plan_digest` 完全一致才继续；每次摘要、source modified 和有效 `include_descendants` 写入 plan-attempts 证据。`copy-page` 为 `root-only-default` 与 `full-subtree` 分别写入 `plan-attempts-<case>.json`、`plan-<case>.json`、`before-<case>.json.plan_binding` 和 `copy-result-<case>.json`；前者在 plan/execute 参数中均省略范围值，并要求回显有效值为 `false`，后者显式提交并要求回显 `true`。Runner 在每次 mutation 前复核范围，并在第一次 after 快照上再规划第二个 case，从而把两个目标的影响相互隔离。这用于等待 fixture 写入引发的 COM 容器时间延迟传播，不重试任何 mutation；任一 case 三次仍不稳定就会在该次写入前 fail closed。

随后 `before.json` 与稳定的 `plan.snapshots.source` 显式绑定：容器 `modified` 采用受 `plan_digest` 保护的值，而不是 fixture 刚写完时可能仍在被 COM 延迟更新的 pre-plan 值。生产 plan 的 raw XML SHA-256 单独保存在 `plan_binding.raw_page_hashes`；Runner 的 `before/after.page_hashes` 使用稳定内容 hash：忽略 Page 根级 hierarchy 字段以及 OneNote 在任意内容节点上延迟补写的时钟、作者、选择和视图元数据，但仍保留内容对象 ID、格式、文本和二进制内容。`page_canonical_hashes` 忽略 Page/内容对象 ID，作为诊断摘要；Page reparent 的成功门限使用更精确的 `page_reparent_hashes`，额外把 Tag index 解析成类型/符号语义，同时保留 Rich Text、Table、List、Tag 状态、Image Data 和其他结构。原始 XML SHA-256 另记在 `page_xml_hashes`，只用于诊断 COM 重序列化，不作为内容变化成功门限；`page_objects` 仍独立记录内容对象投影。执行 confirmation 使用 plan-bound 容器状态，复制后“源未变化”检查和默认 cleanup 恢复比较使用一致的 Runner 内容 hash；执行工具仍会独立重算稳定 digest，任何稳定内容的真实变化都会 fail closed。

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
