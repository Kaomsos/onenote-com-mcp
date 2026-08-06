# OneNote Manual Validation — HUMAN-GATED / ISOLATED / LEAST-PRIVILEGE

> [!CAUTION]
> 本目录只承载由用户本人显式启动的真实 OneNote mutation 验证。Agent、CI、pytest、hook、安装脚本、timer、watcher、前台或后台任务不得执行真实 scenario。每次运行必须创建全新隔离 Notebook，并使用 scenario 级静态最小权限。另见醒目的 [GATED.md](GATED.md)。

## 唯一公开接口：扁平 Scenario

`run.py` 后只能直接接一个具名 scenario。没有 `validate` 分组，也没有公开的 `inspect`、`read`、`report`、`suite` 或其他辅助 action。`create` 是正式的 fixture-only scenario：

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

每个命令本身就是一次完整的隔离闭环，只运行所选 scenario：

```text
create fresh isolated Notebook through the narrow lifecycle wrapper
→ start exactly one scenario-scoped MCP process
→ create only that scenario's fixture and run exactly the selected scenario
→ write local evidence report
→ close the exact leased source Notebook（默认）或 keep open
```

内部 `create.py`、`report.py` 以及非公开诊断库不能被直接调用或组合成另一个隐式入口；公开的 `create` 仍只通过统一 parser 作为完整 scenario 运行。

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

随后生成 manifest、prepared snapshot 和 report，并按默认 close 或 `--keep-notebook` 处理生命周期。

## 安全审查与执行

用户应先查看 dry-run；它不创建目录、不启动 MCP、不访问 OneNote：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json
```

确认 Notebook 名、目录、步骤、权限和 allowlist 后，真实命令只能由用户本人运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

若要验证多个 scenario，用户分别运行多条命令。每条命令都会创建自己的全新 Notebook 和证据目录，没有跨命令前置依赖。

## 参数与生命周期

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> `
  [--notebook-name <name>] `
  [--run-dir <path>] `
  [--keep-notebook] `
  [--timeout <seconds>] `
  [--dry-run] `
  [--json]
```

- 默认 Notebook：`__LOCAL_MCP_TEST_ISOLATED__<UTC_TIMESTAMP>`。
- 默认目录：`.local-validation\run-<同一个 UTC_TIMESTAMP>`。
- `--run-dir` 必须不存在或为空；同名 Notebook 已存在时拒绝复用。
- 默认仅在当前 scenario 和报告成功后，按 lifecycle lease 的精确 ID/name/path 并经即时回读后关闭源 Notebook。
- `--keep-notebook` 保持源 Notebook 打开，供用户人工检查。
- Runner 永不删除本地 Notebook 文件或目录；Notebook Copy 文件夹同样保留。
- `delete` 自动使用本次 manifest 中的 `disposable_group`，不接受外部 target ID，并保持非永久删除。
- `rename` 另支持 `--target group_a|group_b|move_source` 和 `--new-name`。
- `reorder` 另支持 `--page-level <n>`。

## Isolated、单进程与最小权限边界

每个 scenario 都在本次新建的 disposable Notebook 中运行，并最多启动一个 MCP 子进程。源 Notebook 的 create/get/close 由窄 lifecycle wrapper 完成；wrapper 不提供 Section、Page 或内容写入能力。创建后立即写入 `lifecycle-lease.json`，绑定本次 run 的精确 Notebook ID、名称和本地路径。

唯一 MCP 子进程同时完成该 scenario 的最小 fixture、所选 mutation、before/after/restored 回读和契约内 restore/cleanup。它启动时使用 `scenarios/specs.py` 中固定的完整闭包 policy 和 tool allowlist，并在 fixture 创建前用 `health_check` 精确核对 policy、timeout 和 Copy budget；启动后不得扩权。Runner 不使用所有 scenario 的权限并集。

| Scenario | Fixture 与权限限制 |
| --- | --- |
| `create` | 完整预设 fixture；仅 typed fixture 写入和读取，不暴露 `create_notebook`（Notebook 由 wrapper 创建） |
| `rename` | 一个选定 Group/Section；fixture 写入加对应 rename 工具 |
| `reorder` | 一个 Section 与 Parent/Child/Sibling Page 树；fixture 写入加 reorder |
| `move` | 源/目标 Group 与一个 Section；仅增加 Section Move 权限 |
| `delete` | Delete-Sandbox 与 allowlisted disposable group；写入加非永久 Delete，永久删除关闭 |
| Page/Section/Group Copy | 对应最小富内容源和目标；仅开放 Copy 与可恢复清理闭包 |
| Notebook Copy | 最小富内容 Notebook；Copy 开启、Delete 关闭，副本按场景契约关闭 |
| Reconstructive Move | disposable 源 Page 与目标 Section；仅开放专用 experimental/copy/delete 闭包 |
| Report | 只读取本地 artifacts，不启动 MCP |
| Source lifecycle | wrapper 仅支持 `create_fresh_notebook`、精确 get/close；不启动额外 MCP |

永久 OneNote Delete 与 raw XML 始终关闭。

## 严格重建式 Move

`reconstructive-move-page` 始终实际运行严格门禁，不会跳过或降级。当前保真 allowlist 为空时，场景可能返回 `copy_only`、保留源 Page，或因保真门未通过而非零退出；这是刻意的安全行为。

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
   ├─ plan.json / copy-result.json
   ├─ after.json / restored.json
   └─ result.json 或 failure.json
```

唯一 MCP 的 content-free bridge audit 位于 `scenario-mcp/bridge-calls.jsonl`；只记录 operation、成功状态、时间和耗时，不记录参数、OneNote 内容或返回值。`fixture-result.json` 的 `validation` 段记录 profile topology/content invariants 的实际通过证据。

任一步失败立即停止。Mutation 失败时最终 close 不会启动，源 Notebook 保持打开；close 失败按恢复失败返回非零。`run-failure.json` 记录失败步骤、已完成步骤、finalization 状态和人工检查建议。

`run-metrics.json` 记录 lifecycle create、唯一 scenario process、report、finalize 和总耗时，以及实际 MCP 启动数、MCP tool call 数和 scenario/lifecycle bridge call 数。真实性能对比只能由用户本人运行后据此确认；合同测试只验证结构和计数，不把 mock 耗时作为性能收益。

## 仅限纯合同测试

以下命令不访问 OneNote，可以由 Agent 或自动化运行：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
```

真实后端验收必须由用户本人先运行目标 scenario 的 `--dry-run`，再运行同一个扁平 scenario 命令。
