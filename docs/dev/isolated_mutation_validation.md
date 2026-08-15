# OneNote mutation 隔离验证流程

> 本文只定义用户本人在终端显式触发的隔离流程。CI、hook、前台/后台 Agent 或默认测试不得执行。
> 真实验证对象必须是专用、无业务数据、可丢弃的本地 Notebook。

推荐由用户按 [Human-gated Manual Validation Runner](../../tests/manual_validation/README.md) 显式运行一个扁平的 `run.py <scenario>`。每个 scenario 本身就是完整隔离 suite：一个用户命令创建全新 Notebook、准备 fixture、运行所选 mutation、生成报告并按选项关闭或保留 Notebook。`create` 也会在 fixture 后连续创建两个同标题 Page，验证 allocated/read-back ID 互异，并在默认模式下按精确 ID 非永久清理。`validate`、`inspect`、`read`、`report` 和聚合 `suite` 均不是公开 action；本页的手工 tool 调用只保留为故障排查说明，不构成可执行入口。Agent 不得通过 Codex CLI、shell 或 MCP 代用户执行真实 mutation；历史 Codex CLI 编排记录见 [已停用流程](codex_cli_mcp_validation.md)。实现进度见 [TODO 001](../todo/001_programmatic_isolated_mutation_runner.md)。

对于需要反复复现复杂输入、自动比较真实 COM 结果并由用户检查 UI 的新功能或高风险回归，推荐采用[缓存 Fixture → 待验证操作 → 自动比较 → 人工 verdict](cached_fixture_operation_validation.md)的分阶段证据链。它适用于 Copy 之外的 Reorder、Reparent、Move、非永久 Delete 和内容转换，但不改变各操作独立的最小权限与安全门。

反复调试复杂 fixture 时可显式加 `--use-cache`。它不会放宽 policy/tool allowlist，也不会打开 cache template；validated hit 始终 materialize 新的 run-scoped working copy。Cache 与 run 不维持 working lease、所有权或生命周期关系。相同 fingerprint/instance 可以同时服务多个 consumer：每个 run 必须使用唯一 working paths，打开过程由短时全局锁串行化，并把实际 live Notebook ID 写入本 run 的 `lifecycle-lease*.json`；实际 ID/path 相交、role 内重复或身份尚未可靠重绑定时才拒绝。`--keep-worksite` 只保留一组独立 working bundle，不会阻止下一次 validated hit，也不会阻止物理独立 cache entry 的 invalidation/cleanup；cache cleanup 仅在 template 自身的实际路径仍被 OneNote 打开时拒绝。Materialized hierarchy 只使用下文定义的 parent-aware batch：所有物理 child 请求先冻结，再在同一个短命 COM session 中按 parent-before-child 激活；不存在逐对象 global/exact-self fallback。随后以 Notebook-relative typed address 重绑完整 live hierarchy、连续确认两次结构稳定，并对每个 Page 只做一次完整内容读取。Working-copy activation、重绑或内容验证失败保留本次 working Notebook、实际 live ID lease 和诊断，但不污染已验证的 immutable template。省略 `--use-cache` 仍是默认、最保守的 fresh 路径，并保证零 cache lookup/read/write/invalidation/cleanup。

Programmatic cold build 发布前会在 lifecycle 边界内对每个 exact Notebook 调用一次 `SyncHierarchy`，随后执行 `CloseNotebook(force=false)` 并确认精确关闭，再复制已关闭的 bytes。Sync 调用失败时不发布 cache，并保留 active lease 交给默认失败收尾；成功证据记录在 lifecycle close result。该持久化 checkpoint 不会 reopen Notebook，也不会改变只有 Search 可以 close/reopen 激活 index 的限制。

Cache 只在 `.local-validation/fixture-cache/` managed root 内保存关闭的 disposable Notebook opaque bytes。失效清理不是通用 Notebook 删除：只能命中由 fingerprint/instance 精确定位、ownership/containment/reparse/open-state/lease 全部通过的单一 template/staging entry，并留下 root-level tombstone。工作副本、失败现场、普通 artifact 和用户 Notebook 永远不属于该清理能力。

Lookup 不会把目录仍存在的 `invalid` entry 当成普通 miss。历史上仅因 working-copy materialized-open failure 被误隔离的 entry，可在原 validation 与 byte inventory 重新通过时恢复；其他 `invalid` entry 会在首次 lookup 和 programmatic publish 前的 fingerprint lock 内精确清理，再以 `invalidated_rebuild` 重建。`cleanup_failed`、缺失 ownership metadata、未知状态或 template 实际路径仍打开都会 fail closed；run-local active lifecycle lease 不参与 cache cleanup，原子 publish 继续拒绝覆盖现有实例。

P2 Copy、Page Move 与容器 Move 只能使用 Runner 中各自的具名场景；精确命令、权限矩阵、目标清理和 Notebook 残留规则见 [tests/manual_validation/README.md](../../tests/manual_validation/README.md)，Page 进度见 [TODO 002](../todo/002_p2_copy_and_reconstructive_page_move.md)，容器进度见 [TODO 012](../todo/012_reconstructive_section_and_section_group_move.md)。不得把本页的 raw/manual 片段组合成另一个隐式 Copy/Move 入口。

历史 run 与 cache payload 的维护使用独立的 `run.py clear runs|cache|all` 分组。Agent 只能运行 `--dry-run --json`；真实执行必须由用户本人从交互式前台终端启动，并在命令后续提示中现场输入 `CLEAR-RUNS`、`CLEAR-CACHE` 或 `CLEAR-ALL`，不得把确认值放进 CLI。该分组不属于 Scenario/`all`，不启动 MCP、不关闭或修改 OneNote，只用一次只读实际路径快照逐项拒绝仍打开、越界、无 ownership、含 reparse point 或 receipt 无法落盘的目标。成功 receipt 在证据完整嵌入 summary 后自动收敛；pending/failed/unbound receipt 保留。Cache clear 还会移除无 payload tombstone index 项，并逐层清理可证明为空的 typed scaffold。详细命令和结果语义以 [Manual Validation README](../../tests/manual_validation/README.md) 为准。

Working identity 冲突扫描在短时 open lock 内于打开前后各捕获一次当前 Notebook ID/实际目录 snapshot。全部历史 `lifecycle-lease*.json` 只与该 snapshot 做内存比较；历史 run 数量不得放大 COM 调用次数。Snapshot 获取失败按 MCP/lifecycle failure fail closed，并保留本次 working 现场。

### Cache activation 恢复与批处理隔离

以下 parent-aware 规则细化并取代上文对所有节点统一“absolute 优先”的概括。

Lifecycle 在第一次 child COM 调用前预收集并校验 exact working tree 内全部用户 SectionGroup/`.one` 请求，然后在一个内部 PowerShell/COM session 中按 parent-before-child 批量激活并尝试读取一次 pages hierarchy。精确的顶层 `OneNote_RecycleBin` 及其子树属于 OneNote 系统状态，不发送 `OpenHierarchy`；它仍参与 cache byte inventory，且在忽略前仍执行 containment/reparse 检查并写入 content-free evidence。Notebook 直属 child 使用 absolute working path/空 relative ID；SectionGroup 下的嵌套 child 只使用文件名和同批精确 parent ID。只有逐项 `OpenHierarchy` 错误才最多重试该失败项一次，确定性冲突立即失败。请求成功后不再 close/reopen；完整 manifest 层级必须在同一 live identity 上从新的完整枚举中按 typed relative address 重绑并连续稳定两次，每个 Page 随后只完整读取一次。只有这套稳定结构与内容证据可以进入 mutation。

单次打开流程不复制第二份 working bundle，不修改、重建或打开模板，也不重放 mutation。首次打开、ID 重绑、双稳定或内容验证任一步失败都会保留 working files、lease 和诊断并在 mutation 前 fail closed；通用 failure finalizer 默认精确关闭当前 Notebook，显式 keep 模式才保持打开。

具名 scenario 的失败收尾默认关闭本次 run 的每个 exact leased working Notebook，并把逐 role close、稳定关闭证明、`filesystem_deleted=false` 和 `cache_modified=false` 写入 `failure-finalization.json`；`copy-notebook` 已创建的额外目标在原 scenario MCP 退出前以 plan/result 的 exact ID/path binding 单独关闭，绑定或证明不足同样使隔离失败。单独运行与 `all` child 使用同一策略。`--keep-notebook` 或 `--keep-worksite` 才显式保持打开。真实 `all` 在某个 child 失败后，只有收到与 durable evidence 一致的内部 isolation handshake 才继续；close 失败、握手缺失或异常退出立即 fail-fast，避免一次 materialized activation 问题扩散为连续 `0x8004201D` 或 ID rebind 失败。失败文件和 validated cache templates 都保留复用，不做自动删除或重建。`all --dry-run` 没有真实现场，继续遍历全部注册计划。

## 1. 目标

隔离验证 COM `UpdateHierarchy` 在以下 P1 操作中的真实语义：

1. SectionGroup/Section Rename 是否保持对象 ID 和子对象；
2. Page Reorder/Page Level 是否保持 Page ID、正文和子树；
3. Section 同父级 Reorder 是否在 Notebook 与 SectionGroup 两种父级下保持 Section/Page ID、Page 顺序和正文；
4. SectionGroup Reorder 的负能力证据：后端是否忽略请求并维持按名称固定升序；该能力现已判定不支持，不再作为正向验收项；
5. Section 在同 Notebook 内 Move 是否保持 Section ID、Page ID、顺序和完整 Page XML；
6. Page 是否能在同 Notebook 的两个 Section 之间通过原生 `UpdateHierarchy` 更新父级，并在允许 Page/内容对象 ID 一对一重映射时保持富内容语义；
7. SectionGroup 是否能在同 Notebook 的两个 SectionGroup 父级之间 Reparent 并保持自身及后代 ID；
8. 回收站 Delete 是否满足默认非永久语义。

其中 `reparent_section` 保持实验状态并只允许同 Notebook。永久删除不属于本流程。

## 2. 具名 Scenario 自动准备与人工后备

推荐流程无需在 OneNote UI 中预先创建结构：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --verbosity normal
.venv\Scripts\python.exe tests\manual_validation\run.py rename --verbosity normal
```

第一条只展示计划；第二条必须由用户本人明确运行。把 `rename` 替换为另一个顶层 scenario 即可验证其他行为；每条命令都独立创建完整 Notebook bundle，不依赖上一条。单 role Fresh Notebook 名称为 `__<scenario>-<YYYY-MM-DD-HH-MM-SS>__`；cache working Notebook 增加 `CACHED`。多 role bundle（当前代表为 `copy-page`）还在 scenario 后增加 `source`/`destination` role，并为每个 role 写独立 lifecycle lease。默认 run 目录使用同一本地时间戳，例如 `.local-validation\run-2026-08-11-11-05-49`。完整本地 ISO 时间、UTC offset 和时区名称仍写入 `run_identity`，JSON 事件字段仍使用 UTC ISO-8601。只有在 scenario 失败后排障或专门验证附件、墨迹、媒体等没有稳定 typed 创建工具的内容时，才需要下面的 UI 人工准备。

具名 scenario 默认使用 `normal` 进度；`quiet` 仅保留主要阶段和紧凑结尾，`verbose` 增加 content-free 的 mutation attempt/耗时/convergence 标量、每 25 次 read 汇总、policy/allowlist 与阶段统计。非 JSON `all` 按行实时转发每个串行 child 的 stdout，并加 `<scenario> |` 前缀；`verbose` 同时实时转发 stderr，`quiet/normal` 仅在失败时显示有界 stderr 尾部，避免长场景执行期间看似无响应。普通文本不会展开完整 summary，也不会显示 tool arguments、OneNote ID、正文、XML、binary、query 或完整响应。完整计划/结果只在显式 `--json` 时输出；该选项覆盖 verbosity，具名场景保持一个 JSON document，`all` 保持 JSON Lines。失败诊断在普通文本下有行数/字节上限，完整证据保留在 run artifact。

在 OneNote UI 中手工创建仅用于测试的 Notebook：

```text
__LOCAL_ONENOTE_MCP_ISOLATED__
├─ Group-A
│  └─ Content-Section
│     ├─ Parent        pageLevel=1，正文含唯一 token
│     ├─ Child         pageLevel=2，正文含唯一 token
│     └─ Sibling       pageLevel=1，含图片或附件副本
├─ Group-B
└─ Delete-Sandbox
   ├─ Disposable-Group
   │  └─ Disposable-Section
   └─ Disposable-Section
```

不得复用真实 Notebook，也不得在测试结构中放置唯一副本。开始前先启动并保留一个可见的 OneNote Desktop GUI，再等待 OneNote 完成同步，并保留 UI 截图或导出副本供人工比对。`health_check` 与标准 Runner 都只执行 fail-closed readiness 检查，不会隐式启动 OneNote；显式启动使用独立的 HUMAN-GATED `tests/manual_validation/launch_onenote_gui_check.py` 验收入口。该入口不属于 Scenario Registry 或 `all`，通过两个顺序冻结的 MCP policy 验证未授权拒绝、单次启动、重复调用幂等、health readiness、只读 hierarchy COM 和人工单窗口 verdict；它只把 MCP runtime progress/stderr 流向前台终端，结构化证据照常落盘，OneNote/Office 自管的隔离 TEMP diagnostics/cache 不在此承诺内。Agent 只可运行其 `--dry-run`。

## 3. 独立进程配置

使用推荐 Runner 时无需修改任何 MCP 配置；每条场景命令最多启动一个独立 server。Working Notebook fresh create 或 materialized open，以及精确 get/close，均由 lease 约束的窄 lifecycle wrapper 完成；cache open 还必须从 COM hierarchy 证明 actual path 只等于 working path。该场景唯一的 MCP 进程使用固定的 fixture + mutation + evidence + restore/cleanup 最小权限闭包，并在 fixture 前用 `health_check` 核验。仅在使用后文手工 tool 调用排障时，才复制一份只用于该 Notebook 的 MCP 配置并重启独立 server 进程：

```toml
[mcp_servers.local-onenote-isolated.env]
LOCAL_ONENOTE_ENABLE_WRITES = "true"
LOCAL_ONENOTE_ENABLE_DELETES = "false"
LOCAL_ONENOTE_ENABLE_ORGANIZE = "true"
LOCAL_ONENOTE_ENABLE_COPY = "false"
LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO = "false"
LOCAL_ONENOTE_ENABLE_UI_CONTROL = "false"
LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE = "false"
```

先调用 `health_check`，确认 `onenote_desktop.ready=true`，且只有 `writes_enabled` 和 `organize_enabled` 为 `true`。禁止设置内部 raw XML 或永久删除开关。

## 4. 建立只读基线

1. 用 `list_notebooks` 或 `query_notebook(name_equals="__LOCAL_ONENOTE_MCP_ISOLATED__")` 找到候选，并用 `get_notebook_metadata(notebook_id)` 固定精确 Notebook ID；后续 mutation 禁止继续用名称或路径。
2. 调用 `expand_hierarchy(root_id=notebook_id)`，记录所有 ID、父级、Page `order/page_level`。
3. 对 3 个 Page 调用 `get_page_text` 并保存有界内容摘要；具名 Runner 会用非 MCP 的内部验证 capability 计算 raw Page snapshot SHA-256。
4. 对含图片/附件的 Page 调用 `list_page_content_objects`，记录 `id/callback_id/media_type`，但不要把二进制粘贴到日志。
5. 调用 `expand_notebook` 和相关 `expand_section`，确认没有回收站对象混入。

任何 ID、标题、父级或内容与准备结构不一致时立即停止。

## 5. Rename 验证

依次人工调用并在每次后执行 `expand_hierarchy`：

```json
{
  "tool": "rename_section_group",
  "arguments": {
    "section_group_id": "<Group-A ID>",
    "new_name": "Group-A-Renamed",
    "expected_name": "Group-A",
    "expected_parent_id": "<Notebook ID>",
    "expected_modified": null
  }
}
```

然后以新快照确认字段改回 `Group-A`。对 `Content-Section → Content-Section-Renamed → Content-Section` 重复同一流程。验收：对象 ID、父级、Page ID/顺序及 Page XML SHA-256 均不变。

## 6. Reorder 与缩进验证

具名 `reorder-page` 场景会额外创建 `Description` 分区，其 `00-Reorder-Description` Page 明示三种可视状态；目标 `01-Reorder-Page-Section` 分区中的 Page 全部使用固定编号：

```text
操作前：01-Parent(level=1), 02-Child(level=2), 03-Sibling(level=1)
操作后：01-Parent(level=1), 03-Sibling(level=2), 02-Child(level=2)
恢复后：01-Parent(level=1), 02-Child(level=2), 03-Sibling(level=1)
```

因此 OneNote UI 中标签顺序会清楚显示 `01,02,03 → 01,03,02 → 01,02,03`。具体步骤：

1. 将 `03-Sibling` 放到 `01-Parent` 后，并设 `page_level=2`；确认其 `parent_page_id=01-Parent ID`。
2. 将 `03-Sibling` 恢复到原位置和 `page_level=1`。
3. 每一步后读取 `expand_section`、`expand_hierarchy` 和包括 Description Page 在内的全部 Page 完整 XML 摘要。

调用模板：

```json
{
  "tool": "reorder_page",
  "arguments": {
    "page_id": "<Sibling ID>",
    "expected_title": "03-Sibling",
    "expected_section_id": "<01-Reorder-Page-Section ID>",
    "after_page_id": "<01-Parent ID>",
    "page_level": 2,
    "expected_modified": null
  }
}
```

验收：调用返回的 `order/page_level` 与只读回读一致；所有 Page ID 和正文摘要保持不变；UI 中缩进树与 `expand_hierarchy` 一致。

Section Reorder 不使用本节的手工 raw/tool 模板。用户应先审查 dry-run，再显式运行独立场景：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section
```

`reorder-section` 会创建 `00-Description/00-Reorder-Section-Description`，并用两套编号 fixture 覆盖 Section 的两种合法父级：

```text
Notebook 父级：
  操作前：00-Description, 01-Root-Section-A, 02-Root-Section-B, 03-Root-Section-C
  操作后：00-Description, 01-Root-Section-A, 03-Root-Section-C, 02-Root-Section-B

01-Section-Parent（SectionGroup）父级：
  操作前：01-Group-Section-A, 02-Group-Section-B, 03-Group-Section-C
  操作后：01-Group-Section-A, 03-Group-Section-C, 02-Group-Section-B
```

各 Section 内的 Page 也对应编号，以便目视确认后代没有互换。

不要再运行 `reorder-section-group` 作为正向能力验收。该内部诊断场景不进入 `all`，也不存在公开 Tool 或用户授权开关。2026-08-10 保存的真实后端证据显示：Notebook 直属 Group 的 `01,02,03 → 01,03,02` 请求中，`UpdateHierarchy(xs2013)` 返回成功，但即时回读仍保持按名称固定升序 `01,02,03`。产品层据此对 Notebook 和 SectionGroup 两种父级统一不发布 reorder。

`reorder-section` 默认完成正向 reorder、before/after read-back、反向 restore 和 restored read-back，并记录稳定 Page 内容 hash、原始 XML 诊断 hash 与内容对象投影；`--keep-worksite` 可保留新 predecessor 供 UI 检查。稳定 hash 忽略 OneNote 延迟补写的作者/时钟/选择/视图元数据，但保留内容对象 ID、格式、文本和二进制内容；原始 XML hash 的单独变化不判定正文变化。逐 Page 取证完成后会末尾刷新 hierarchy，mutation confirmation 使用这次最新回读的 `modified`。正向两个 case 和反向两个 restore step 分别以静态 `notebook-parent` / `section-group-parent` 标签发出 content-free progress，不投影 Section 名称、ID、请求参数或响应。场景只开启 Section Reorder 实验开关，不启用 Delete、Permanent Delete、Copy、Move 或 Raw XML；完成稳定性与权限审查后已显式设置 `included_in_all=True`。场景不要求环境元数据参数；跨版本取证另见 [`TODO 007`](../todo/007_cross_version_compatibility_evidence.md)，不作为当前验收前置条件。

## 7. Section Reparent 验证

用户先审查 dry-run，再显式运行具名场景：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section
```

场景创建 `00-Description/00-Reparent-Section-Description`，并以编号 fixture 覆盖同一 Notebook 内全部三种合法父级变化：

```text
场景一：Notebook → SectionGroup
  操作前：Notebook/01-Notebook-To-Group-Section/01-Notebook-To-Group-Page
  操作后：01-Destination-Group/01-Notebook-To-Group-Section/01-Notebook-To-Group-Page

场景二：SectionGroup → Notebook
  操作前：02-Source-Group/02-Group-To-Notebook-Section/02-Group-To-Notebook-Page
  操作后：Notebook/02-Group-To-Notebook-Section/02-Group-To-Notebook-Page

场景三：SectionGroup → SectionGroup
  操作前：03-Source-Group/03-Group-To-Group-Section/03-Group-To-Group-Page
  操作后：03-Destination-Group/03-Group-To-Group-Section/03-Group-To-Group-Page
```

三次正向 Reparent 按上述顺序执行；每次都重新读取完整 hierarchy 和 Page 证据，使下一次 mutation 使用最新 confirmation。统一 after 快照必须证明所有 hierarchy ID、三个 Section 的 parent、Page ID/顺序/缩进关系和稳定正文 hash 不变。默认按场景三、二、一的逆序逐项移回并生成 `restored.json`；`--keep-worksite` 不恢复，记录三个目标 Section 的原父级、当前父级和人工清理顺序。

只有三次正向 Reparent 和三次恢复 Reparent 全部通过，才可确认本次真实运行；该结论不等同于对所有 Office/OneNote 版本解除实验状态，也不包含跨 Notebook Move。跨版本兼容性证据的统一采集与矩阵另见 [`TODO 007`](../todo/007_cross_version_compatibility_evidence.md)。

## 7.1 Page 与 SectionGroup Reparent

这两项已迁移为 typed mutation 工具，必须使用 runner 中受控的具名场景，不能手工拼接 raw XML：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section-group --dry-run --json
```

两个场景都创建全新的 disposable Notebook。独立真实验收通过后，2026-08-13 又完成批处理稳定性与权限审查，现与 `reparent-section` 一起显式设置 `included_in_all=True`；这不改变它们各自的 policy、tool allowlist、fixture 或恢复边界。它们只启用 Writes + 统一 Reparent 实验门；Delete、Permanent Delete、Copy、Move、Reorder 与 Raw XML 保持关闭。runner 仅向 `reparent_page` / `reparent_section_group` 提交 manifest 绑定的精确 ID、confirmation 和可选 modified，不构造或传递 hierarchy XML。`all` 仍为每个场景启动相互独立的子命令，Page 正向与恢复均跟踪原生 ID remap，SectionGroup 三个 case 默认逆序恢复。

`reparent-page` 创建 Description 说明页和以下编号结构：

```text
操作前：01-Source-Section/01-Reparent-Page
操作后：02-Destination-Section/01-Reparent-Page
无关锚点：02-Destination-Section/02-Destination-Anchor
恢复后：01-Source-Section/01-Reparent-Page
```

目标 Page 本身包含 Rich Text、Table、三个混合 List/Tag 项和 Image；不是另建一个只读旁证页。fixture 构建使用普通 Page 写工具，正向 reparent 只调用一次 `reparent_page`。场景 policy 不启用 Copy、Delete 或 Raw XML，因此不会调用 `copy_page`、`UpdatePageContent` 重建目标、`DeleteHierarchy` 或任意 XML mutation 工具。

2026-08-13 的真实 run 曾让实现把 `SyncHierarchy` 后失败归因为 fixture 尚未持久化，并为 `reparent-page` v3 加入 close/reopen checkpoint。2026-08-14 的稳定对照随后确认，共性变量是 scenario 启动前 OneNote Desktop GUI 是否已存在，而不是 `CloseNotebook(false)` 动作；GUI preflight 落地后当前 manual validation 全绿。因此该 checkpoint、fresh persistence 分支及其 evidence 已移除。保留的 v3 cache identity、typed structure/evidence ID rebind、完整 read-back、默认恢复、template inventory 和精确最终关闭仍分别承担原有安全职责。

三个生产 Reparent 共用两阶段 mutation 后验证：先用不读取 Page XML 的 bounded hierarchy observer 连续两次观察相同的目标、父级、ID remap 与完整关系/同级顺序签名；随后只做一次完整 Page evidence capture，并以 capture 前后的 hierarchy 签名证明取证期间结构未变化。只有瞬态读取错误或该 bookend 不一致时才允许再读取一次，绝不重放 Reparent mutation；确定性内容、scope 或 topology invariant 失败立即返回带 `readback_phase` 的 partial failure。通用 4 秒 convergence deadline 保持不变，但不再包围可能超过该时限的完整 Page XML 取证。

`reparent-section-group` 创建 Description 说明页和三组带编号 Section/Page 后代的目标 Group：

```text
01：Notebook/01-Notebook-To-Group-Target
    → 01-Destination-Parent/01-Notebook-To-Group-Target

02：02-Source-Parent/02-Group-To-Notebook-Target
    → Notebook/02-Group-To-Notebook-Target

03：03-Source-Parent/03-Group-To-Group-Target
    → 03-Destination-Parent/03-Group-To-Group-Target
```

Page typed 场景的验收标准是：

1. `UpdateHierarchy` 返回后，原 ID 仍在目标 Section，或者全树恰好出现 `旧 Page ID 消失 + 目标 Section 新增一个 Page ID`；不接受多个新增、多个消失或无法唯一关联的结果；
2. 记录 `target_id`、`current_target_id` 和完整 `id_history`，不把新 ID 冒充成原 ID；
3. 新 Page 仍在原 Notebook，标题、page level 和父子缩进不变；
4. `page_reparent_hashes` 相等。该摘要忽略 Page/内容对象 ID、时钟和视图字段，把 TagDef/Tag index 解析成类型/符号语义，并保留富文本格式、Table/List/Tag 状态、可见文本和 Image Data；`page_canonical_hashes` 另作诊断；
5. 去除对象身份字段后的内容对象类型/媒体语义相等，所有无关 Page 继续使用保留对象 ID 的稳定内容 hash；
6. 非目标 hierarchy 对象的 ID、父级、顺序及内容完全不变；
7. 默认恢复必须使用正向回读得到的当前 Page ID。反向操作若再次生成 ID，则记录第三个 ID，并以源 Section 位置和同一富内容语义摘要验证“逻辑恢复”，不要求恢复最初的 Page ID。

SectionGroup 三次正向请求逐项执行并立即回读；前一步未通过时不继续。它仍要求原目标及全部后代 ID 保持不变，全 Notebook ID 集合、Page 稳定内容 hash 和内容对象 ID 投影不变；默认按 `03→02→01` 逆序恢复并与 before 完整比较。`--keep-worksite` 保留当前父级及人工清理顺序。

COM 返回成功但父级未变化、Page ID 转换不是精确一对一、富内容语义摘要变化、无关对象变化或恢复不完整都必须判为失败并保留现场。用户曾明确确认迁移后的三个 typed 场景通过；2026-08-13 的 Page fixture/首次 mutation 回归又由当前 v3 fresh/cold-build 两次真实运行闭环。历史证据完成 TODO 009 的迁移验证，本轮证据关闭 Page 回归，但两者都不构成跨 OneNote/Office 版本保证；当前 `all` 三 Reparent 联合验收仍由 TODO 027 单独跟踪。

## 8. 非永久 Delete 验证（可选、单独重启）

关闭测试进程，将 `LOCAL_ONENOTE_ENABLE_DELETES` 改为 `true`。仅对 `Delete-Sandbox` 下的 disposable 对象调用公开的可恢复 typed delete，且完整提供 `expected_name/expected_parent_id`；公开 schema 不提供永久删除选项。

验收：返回 `permanently=false`，对象从默认列表消失；启用 `include_recycle_bin=true` 时对象缺失或标记 `is_in_recycle_bin=true`。不要调用 `permanently=true`。

## 9. 停止与清理

1. 关闭独立 MCP server，移除全部 enable 环境变量；
2. 用普通只读 profile 再次运行 `health_check`，确认写、删、实验 Move、raw XML 全部为 `false`；
3. 默认具名 scenario suite 在成功或失败时都只对本次 run 的 exact lease 执行 typed `close_notebook` 并回读确认，不删除本地 Notebook 目录。显式 `--keep-notebook` 或 `--keep-worksite` 时源 Notebook 保持打开；`--keep-worksite` 还会在该 action 的 after/read-back 通过后跳过适用的 restore/cleanup，并在 `worksite.json` 中记录精确 ID 和人工清理步骤；特殊入口 `all` 不接受也不透传这两个选项；
4. 若任一步发生 ID 变化、内容摘要变化、重复 Section/Page 或恢复失败，保留隔离 Notebook，不继续后续 mutation，并保留操作前后快照。

Page Move 只对本次 `page_scope` 选定范围执行源侧非永久删除。默认 `page_only` 只移动根 Page：生产服务先把被排除的完整后代子树整体提升一级，回读其精确 ID、Section、相对层级与内容，再删除根 Page；`indentation_subtree` 才按叶到根处理完整子树。生产删除服务会有界回读每个精确选定 Page ID，并拒绝仍留在活动 hierarchy 的对象；manual scenario 的双 Notebook `after.json` 再确认选定源已消失、root-only 排除后代仍活动且内容未变。回收站 metadata 若能取得只作为额外诊断证据。背景与适用边界见 [`lesson/onenote_com_recycle_bin_visibility.md`](../lesson/onenote_com_recycle_bin_visibility.md)。

Section/SectionGroup Move 使用不同的删除策略：只允许跨 Notebook，完整容器子树 Copy 与生产验证通过后，只对源容器根调用一次 `DeleteHierarchy(permanently=false)`，不得逐个删除后代“补齐”。`move-section` / `move-section-group` 的双 Notebook after snapshot 必须证明计划中的全部源 ID 都不再活动、完整目标映射只位于 destination role；同 Notebook 请求必须在 mutation 前拒绝并改用对应 `reparent-*`。两个新场景均不进入 `all`，真实执行只能由用户本人分别启动。

2026-08-11 用户真实验收记录：`run-2026-08-11-20-29-19` 的 `move-page` 两种跨 Notebook 范围均通过；`run-2026-08-11-20-31-28` 的 `move-section` 与 `run-2026-08-11-20-33-29` 的 `move-section-group` 均完成 verified/lossless Copy、一次非永久根删除、全部源 ID 活动态缺席和双 Notebook 精确关闭。SectionGroup 的 COM 回读未提供回收站标记，只记录活动态缺席，不虚构 recycle metadata。后续复跑仍必须由用户显式启动，且单环境证据不替代跨版本验证。

## 10. 自动化边界

### 仓库开发规则

凡真实执行时需要 mutation policy 权限的 tool，包括 Write、Delete、Permanent Delete、Experimental Mutation、Raw XML 以及未来新增的非只读权限，都必须采用本页这种半自动化手动验证：

1. 自动化 pytest 只允许 mock/纯合同测试，不能访问真实 OneNote；
2. 真实场景统一放在 [`tests/manual_validation/`](../../tests/manual_validation/README.md)，通过一个总入口由用户显式选择顶层场景；每个 `run.py <scenario>` 自身包含 lifecycle create、该场景最小 fixture、mutation、report 与 close/keep；不得公开辅助 action。唯一批处理例外是用户显式运行的 `run.py all`，它只能串行启动显式注册的稳定测试 scenario，不得共享 run-dir、Notebook、MCP、权限或 lifecycle；新增的探索性/验证性 scenario 默认不得进入该注册表；
3. 每个 scenario 最多启动一个 MCP 子进程。Runner 为其推导覆盖 fixture、mutation、evidence 与 restore/cleanup 的静态最小权限闭包，并在 fixture 前用 `health_check` 精确核验；源 Notebook 生命周期只能通过精确 lease 约束的窄 wrapper 操作；
   每个 Scenario 显式持有唯一 fixture recipe；common runtime 不按名称分派，并在每次登记精确 ID 后增量保存 pending/failed evidence。每个公开 Scenario 还自动注册 default/keep dry-run cases，与 `included_in_all` 资格完全分离；pytest harness 强制安全参数并以 sentinel 证明零 MCP、零 lifecycle、零 subprocess 和零目录副作用；
4. 使用专用可丢弃 Notebook、精确 ID、最新确认字段和 before/after 证据；可恢复操作默认执行恢复与 restored 回读。所有具名 scenario 都提供显式 `--keep-worksite` 人工验收模式，用于保留各自动验证通过的动作现场，并必须写入带精确目标 ID 和清理说明的 `worksite.json`；该模式不得扩权；
5. 不可恢复操作只能命中 manifest 白名单中的 disposable 对象，并在报告中明确最终状态和人工处理方式；
6. 新增或修改非只读 tool 时，必须同步新增/更新对应 manual scenario 和使用命令；用户完成隔离实测前，不得声明真实后端验证完成。

该规则不授权自动运行 mutation，也不允许用普通集成测试、临时脚本或直接手调 tool 绕过 Runner 的权限矩阵、身份检查和证据链。

### 默认测试边界

仓库中的 `write_contract` pytest 只使用 mock，不接触 OneNote；可在明确授权后单独运行：

```powershell
.venv\Scripts\python.exe -B -m pytest -m write_contract -p no:cacheprovider
```

真实 COM mutation 永远不能进入默认 CI、pre-commit 或 smoke test。`write_contract` 仅是 mock 合同测试；真实隔离验证必须由用户在终端明确启动。

本地程序化 Runner 不是默认自动化：只有用户本人手动运行具体 `run.py <scenario>` 才构成授权；Agent 只能修改 runner、运行不接触 OneNote 的合同测试或把命令交给用户，不能代为执行。Runner 为该场景唯一的 MCP 子进程开启完整闭环所需的静态最小权限，不要求额外权限开关或二次确认，也不跨场景合并权限。永久 OneNote Delete 在所有场景中始终关闭；四个具名 Reparent 场景全部使用 typed 工具且保持 Raw XML 关闭。所有 scenario suite 都不删除本地 Notebook 文件。
