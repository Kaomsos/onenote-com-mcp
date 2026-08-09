# OneNote Manual Validation — HUMAN-GATED / ISOLATED / LEAST-PRIVILEGE

> [!CAUTION]
> 本目录只承载由用户本人显式启动的真实 OneNote mutation 验证。Agent、CI、pytest、hook、安装脚本、timer、watcher、前台或后台任务不得执行真实 scenario。每次运行必须创建全新隔离 Notebook，并使用 scenario 级静态最小权限。智能体的强制行动边界见本目录的 [AGENTS.md](AGENTS.md)。

## 公开接口：扁平 Scenario 与特殊 `all`

`run.py` 后通常直接接一个具名 scenario。没有 `validate` 分组，也没有公开的 `inspect`、`read`、`report`、`suite` 或其他辅助 action。`create` 是正式的 fixture-only scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename
.venv\Scripts\python.exe tests\manual_validation\run.py create
.venv\Scripts\python.exe tests\manual_validation\run.py reorder
.venv\Scripts\python.exe tests\manual_validation\run.py move
.venv\Scripts\python.exe tests\manual_validation\run.py delete
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section-group
.venv\Scripts\python.exe tests\manual_validation\run.py copy-notebook
.venv\Scripts\python.exe tests\manual_validation\run.py reconstructive-move-page
```

每个具名 action 都可显式保留已验证的操作现场，供 OneNote UI 人工验收：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --keep-worksite
```

`--keep-worksite` 会隐含保持源 Notebook 打开，并在成功 read-back 验证后保留该 action 的现场：`rename/reorder/move` 跳过反向恢复，Copy 跳过目标 cleanup，`create/delete/reconstructive-move-page` 保留其原本最终状态以供查看。精确目标 ID、现场状态和人工清理说明写入 `worksite.json`。该选项不会扩权；Copy 场景反而从 policy/tool allowlist 移除不再需要的 Delete/Close cleanup 权限。默认不传时仍执行各 scenario 原有的 restore/cleanup 与生命周期策略。

所有会通过 COM 复制 Page XML 的具名场景（四个 Copy 层级以及
`reconstructive-move-page`）都自动创建两页组成的完整 Page 子树：

- `Rich-Page`（父页）：只含已确认的 `Outline/RichText/Table/Image`，使用严格 canonical 验收；
- `List-Tag-Page`（子页）：程序通过受限 HTML 自动生成三个编号/项目符号与 To Do 标签混合项（完成、未完成、完成），使用 `semantic_list_tag` 验收。

整个过程不暂停、不要求用户编辑，也不启用 raw XML。第二层忽略 COM 重新编号 `TagDef`、列表序号状态和 Outline 布局重排，但仍严格比较可见文本、列表种类、标签类型、完成状态和二进制内容。`List/Tag` 已进入 validated/lossless allowlist；这表示其保真结论由 `semantic_list_tag` 而不是 canonical XML 相等来证明。`MeetingInfo` 暂不属于验证范围。

唯一特殊入口 `all` 会按显式测试注册表的顺序串行启动其中的 scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

唯一注册表对象位于 `scenarios/common/registry.py` 的 `SCENARIO_REGISTRY`。每个场景类使用 `@SCENARIO_REGISTRY.register` wrapper；`scenarios/__init__.py` 按审查后的固定顺序导入所有公开场景，导入时自动完成实例注册。Registry 本身不再导入具体场景，也不维护第二份构造列表。新增公开 scenario 不会自动进入 `all`：探索性或仅用于某次隔离验证的类保持 `registered_for_all = False`；只有经过稳定性和权限审查并显式改为 `True`，才会被批量执行。

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
└─ Move-Source
   ├─ Parent        rich text + table + image
   ├─ Child         pageLevel=2
   └─ Sibling       pageLevel=1
Group-B
Delete-Sandbox
├─ Disposable-Group
└─ Disposable-Section
   └─ Disposable-Page
```

随后生成 manifest、prepared snapshot 和 report，并按默认 close、仅保持打开的 `--keep-notebook`，或写出现场证据的 `--keep-worksite` 处理生命周期。

## 安全审查与执行

用户应先查看 dry-run；它不创建目录、不启动 MCP、不访问 OneNote：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json
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
- 未指定 `--timeout` 时保留各 scenario 自己的默认值（普通场景 180 秒，Copy/重建式 Move 1800 秒）。
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
- `--keep-worksite` 适用于全部十个具名 scenario，并同时保持源 Notebook 打开。可恢复的 `rename/reorder/move` 不执行反向恢复；Page/Section/SectionGroup Copy 不执行回收站 cleanup；Notebook Copy 不关闭副本；其余 action 记录本来就会留下的 fixture、回收站或重建式 Move 状态。`worksite.json` 和 `result.json` 记录精确目标 ID、当前位置/名称/路径以及 `manual_cleanup_required=true`。特殊批处理入口 `all` 不接受该选项。
- Runner 永不删除本地 Notebook 文件或目录；Notebook Copy 文件夹同样保留。
- `delete` 自动使用本次 manifest 中的 `disposable_group`，不接受外部 target ID，并保持非永久删除。
- `rename` 另支持 `--target group_a|group_b|move_source` 和 `--new-name`。
- `reorder` 另支持 `--page-level <n>`。

## Isolated、单进程与最小权限边界

`scenarios/` 根目录中的每个可执行模块只提供一个具名 `Scenario` 子类；四个 Copy 入口分别位于 `copy_page.py`、`copy_section.py`、`copy_section_group.py` 和 `copy_notebook.py`，并共享基础设施 `copy_scenario_base.py`。根目录的 `base.py` 和 `__init__.py` 明确属于基础设施。类统一声明名称、help、默认 timeout、scenario 专属参数、manifest 参数准备、执行器和 `registered_for_all`，并通过 registry wrapper 注册。`scenarios/__init__.py` 是公开场景导入顺序的唯一清单，`SCENARIO_REGISTRY` 则是 parser、dispatch 和 `all` 的共同权威对象。

不代表单个 scenario 的依赖统一放在 `scenarios/common/`，包括 registry、闭环 orchestrator、静态 spec、fixture builders、fixture 编排、报告、Copy runtime 与 invariants。根目录因此不会混入名称看似 scenario、实际却只是共享函数的模块。

每个 scenario 都在本次新建的 disposable Notebook 中运行，并最多启动一个 MCP 子进程。源 Notebook 的 create/get/close 由窄 lifecycle wrapper 完成；wrapper 不提供 Section、Page 或内容写入能力。创建后立即写入 `lifecycle-lease.json`，绑定本次 run 的精确 Notebook ID、名称和本地路径。

唯一 MCP 子进程同时完成该 scenario 的最小 fixture、所选 mutation、before/after/restored 回读和契约内 restore/cleanup。它启动时使用 `scenarios/common/specs.py` 中固定的完整闭包 policy 和 tool allowlist，并在 fixture 创建前用 `health_check` 精确核对 policy、timeout 和 Copy budget；启动后不得扩权。Runner 不使用所有 scenario 的权限并集。

| Scenario | Fixture 与权限限制 |
| --- | --- |
| `create` | 完整预设 fixture；仅 typed fixture 写入和读取，不暴露 `create_notebook`（Notebook 由 wrapper 创建）；`--keep-worksite` 记录整个 fixture Notebook |
| `rename` | 一个选定 Group/Section；fixture 写入加对应 rename 工具；`--keep-worksite` 保留新名称并记录原名称 |
| `reorder` | 一个 Section 与 Parent/Child/Sibling Page 树；fixture 写入加 reorder；`--keep-worksite` 保留新 predecessor/level |
| `move` | 源/目标 Group 与一个 Section；仅增加 Section Move 权限；`--keep-worksite` 保留目标 parent |
| `delete` | Delete-Sandbox 与 allowlisted disposable group；写入加非永久 Delete，永久删除关闭；`--keep-worksite` 保持 Notebook 打开并记录回收站目标 |
| Page/Section/Group Copy | 对应最小源和目标；每个源容器均含严格富内容父页与三个混合 List/Tag 项的语义子页；默认执行可恢复清理，显式 `--keep-worksite` 在 after/mapping 验证后保留精确目标 ID |
| Notebook Copy | 最小 Notebook 同样包含严格父页和 List/Tag 语义子页；Copy 开启、Delete 关闭，默认关闭副本；显式 `--keep-worksite` 保持副本打开并记录路径 |
| Reconstructive Move | disposable 源 Page 与目标 Section；仅开放专用 experimental/copy/delete 闭包；`--keep-worksite` 记录 active Copy、非永久删除的 source ID 与回收站诊断状态 |
| Report | 只读取本地 artifacts，不启动 MCP |
| Source lifecycle | wrapper 仅支持 `create_fresh_notebook`、精确 get/close；不启动额外 MCP |

永久 OneNote Delete 与 raw XML 始终关闭。

## 严格重建式 Move

`reconstructive-move-page` 始终实际运行严格门禁，不会跳过或降级；它也使用严格父页和 List/Tag 语义子页验证整棵 Page 子树。当前 validated 保真类型为 `Outline/Image/RichText/Table/List/Tag`；出现尚未确认的 `MeetingInfo`、附件、墨迹、媒体或未知结构时，场景仍可能返回 `copy_only`、保留源 Page，或因保真门未通过而非零退出。

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
   ├─ after.json / restored.json / worksite.json
   └─ result.json 或 failure.json
```

唯一 MCP 的 content-free bridge audit 位于 `scenario-mcp/bridge-calls.jsonl`；只记录 operation、成功状态、时间和耗时，不记录参数、OneNote 内容或返回值。`fixture-result.json` 的 `validation` 段记录 profile topology/content invariants 的实际通过证据。

Copy mutation 前会有界执行最多三次只读 `plan_copy`，只有连续两次 `plan_digest` 完全一致才继续；每次摘要和 source modified 写入 `plan-attempts.json`。这用于等待 fixture 写入引发的 COM 容器时间延迟传播，不重试任何 mutation；三次仍不稳定就会在写入前 fail closed。

随后 `before.json` 与稳定的 `plan.snapshots.source` 显式绑定：容器 `modified` 采用受 `plan_digest` 保护的值，而不是 fixture 刚写完时可能仍在被 COM 延迟更新的 pre-plan 值。生产 plan 的 raw XML SHA-256 单独保存在 `plan_binding.raw_page_hashes`；Runner 的 `before/after.page_hashes` 始终使用去除根级可变元数据后的内容 hash，两个 hash 域不得互换。执行 confirmation 使用 plan-bound 容器状态，复制后“源未变化”检查和默认 cleanup 恢复比较使用一致的 Runner 内容 hash；执行工具仍会独立重算 digest，任何稳定 plan 后的真实变化都会在 mutation 前 fail closed。

任一步失败立即停止。Mutation 失败时最终 close 不会启动，源 Notebook 保持打开；close 失败按恢复失败返回非零。`run-failure.json` 记录失败步骤、已完成步骤、finalization 状态和人工检查建议。

成功的可恢复 action 默认仍完成 restore/cleanup，并用 `restored.json` 证明恢复。显式 `--keep-worksite` 才写入 `worksite.json`、保留动作后的精确状态和源 Notebook；该模式只在 scenario 自身的 read-back invariant 通过后报告成功。对 Copy，此成功还表示每页按其内容类型选择的 read-back tier 与 mapping invariant 均通过；UI 人工检查仍用于记录具体 OneNote 环境的真实证据。

`run-metrics.json` 记录 lifecycle create、唯一 scenario process、report、finalize 和总耗时，以及实际 MCP 启动数、MCP tool call 数和 scenario/lifecycle bridge call 数。真实性能对比只能由用户本人运行后据此确认；合同测试只验证结构和计数，不把 mock 耗时作为性能收益。

## 仅限纯合同测试

以下命令不访问 OneNote，可以由 Agent 或自动化运行：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
```

真实后端验收必须由用户本人先运行目标 scenario 的 `--dry-run`，再运行同一个扁平 scenario 命令。
