# OneNote Manual Validation — HUMAN-GATED / ISOLATED / LEAST-PRIVILEGE

> [!CAUTION]
> 本目录只承载由用户本人显式启动的真实 OneNote mutation 验证。Agent、CI、pytest、hook、安装脚本、timer、watcher、前台或后台任务不得执行真实 scenario。每次运行必须创建全新隔离 Notebook，并使用 scenario 级静态最小权限。智能体的强制行动边界见本目录的 [AGENTS.md](AGENTS.md)。

真实运行前必须先启动并保留一个可见的 OneNote Desktop GUI。单项 scenario 会在创建/打开任何 working Notebook 前执行不触发 COM 的原生进程/窗口 preflight；`all` 在启动首个 child 前检查一次。OneNote 未运行、只有后台 `ONENOTE.EXE` 或 GUI 状态无法证明时均 fail closed，不创建 Notebook、不 materialize cache、不启动 scenario MCP；`--dry-run` 不读取 GUI 状态。标准 runner 永不隐式启动 OneNote；显式启动工具只由下述独立 HUMAN-GATED 验收入口测试。

本框架把 MCP server 当作独立黑盒。Runner、Scenario Registry、fixture/cache、lifecycle 和共享证据代码不导入 Operation Runtime、canonical Registry、operation catalog、Tool runtime context/response mapper 或生产 server 对象，也不根据生产 Registry 动态扩大 tool allowlist。场景只通过 stdio MCP 调用冻结的公开 Tool，并可以像验证其他公开返回字段一样验证 content-free `execution` 投影。生产 Operation→具名 scenario 的完整性检查归属仓库顶层 Runtime 合同测试，不进入本目录核心。共享 stdio client 只用公开 envelope 的 `ok` 判断 Tool 调用成败；`complete` 是由各具名场景解释和验证的业务完成语义，因此 Sync 的 `ok=true, accepted=true, complete=false` 不会在核心 client 中被误判为 transport failure。

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
.venv\Scripts\python.exe tests\manual_validation\run.py onenote-convergence
.venv\Scripts\python.exe tests\manual_validation\run.py query
.venv\Scripts\python.exe tests\manual_validation\run.py hierarchy-navigation
```

### 独立 GUI 启动验收入口

`launch_onenote_gui_check.py` 是唯一不属于 Scenario Registry、也不进入 `run.py all` 的 GUI effect 验收入口。它不创建 Notebook、不修改 Notebook 内容、不关闭 OneNote；真实执行只允许用户本人在交互式前台终端启动，并把 OneNote 留在运行状态。Agent、pytest、CI、hook、timer、watcher、后台任务和重定向 stdin 均不得运行真实命令。

先检查零副作用静态计划：

```powershell
.venv\Scripts\python.exe tests\manual_validation\launch_onenote_gui_check.py --dry-run --json
```

真实验收前完全退出 OneNote，再运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\launch_onenote_gui_check.py --verbosity verbose
```

入口要求两次 run-bound 终端确认，并顺序启动两个权限不可扩展的短命 MCP：

1. UI Control 全关：两次 `health_check` 必须都证明进程和可见窗口不存在；中间的 `launch_onenote_gui` 必须以 `policy_disabled` 在 authorization stage、`backend_calls=0` 拒绝；
2. 仅开启 UI Control：第一次 Launch 必须返回 `started / launch_attempts=1 / ready=true`；第二次必须返回 `already_running / launch_attempts=0`；随后 `health_check` 必须 ready，`list_notebooks` 必须完成一次 typed hierarchy COM 读取；
3. 用户观察桌面并输入 run-bound verdict，确认只有一个可见 OneNote GUI，第二次调用没有打开额外窗口。

`--verbosity quiet|normal|verbose` 只控制前台终端细节，默认 `normal`；排障时使用 `verbose`。该特殊入口不把 `calls.jsonl`、bridge audit 或 server stderr 写入对应 MCP runtime 日志文件，两个 MCP 的 server stderr 与 content-free progress 只流向当前前台终端。结构化验收证据仍写入普通 `.local-validation/run-<timestamp>/`，包含逐阶段 JSON、`run-state.json` 和最终 `run-result.json` 或 `run-failure.json`；它沿用 managed run ownership 形状，可由既有 human-gated `clear runs` 安全评估。OneNote/Office 可能在这个 run 的隔离 TEMP 下生成自身管理的 diagnostics/cache；它们不是 MCP runtime 日志，入口不会重定向、解释或自动清理。失败不自动关闭 OneNote 或改写证据。

2026-08-16 用户真实运行 `run-2026-08-16-00-01-03` 通过：未授权请求在零 backend call 时拒绝；授权首调只发出一次 launch 并 ready；复调没有再次 launch；后续 health、typed hierarchy COM 读取及 run-bound 单窗口人工 verdict 全部通过。OneNote 按合同保持运行。

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

`runs` 逐个评估直接 `run-*` 子目录，`cache` 逐个评估新 schema 的 index/磁盘相互证明的 exact `(fingerprint, template_instance_id)` entry 与 `.s-<16 hex>` staging，`all` 在同一次 open-path snapshot 下组合两者。新 maintenance 不解析、迁移或删除 legacy lease、64-hex fingerprint/full-instance 目录和旧 run metadata；发现它们时逐项 `refused`，并要求回到升级前版本完成 human-gated `clear all`。任一新 schema 目标只有在固定 root、ownership、无 reparse point、实际未打开和 pending receipt 全部通过后才删除；开放中或无法证明的目标单独 `refused`，其他安全目标仍可处理，整体以非零 partial result 返回。已经存在的 owned run/cache payload 即使超过当前 240 UTF-16 units 创建预算，也不会仅因该超限而阻止 cleanup；这是只删除精确历史现场的恢复行为，不授权创建、复制、扩展或手工改写超预算路径。

升级前版本的成功 `clear all` 会保留 cache marker、空 index 和 history。新 runtime 不把它们当作 legacy payload：只有 durable v1 `clear-all` summary 同时证明交互确认、完整只读 open-path snapshot、零 refused/failed、精确 managed roots，且 cache 只剩允许的 ownership/history 文件、index 为空、validation root 没有旧 run 时，首次 `--use-cache` 初始化才会先原子 stamp 空 v2 index、再 stamp v2 marker。Summary 之后创建的 run 只有在 state 为 v2、ownership flags 完整且 `started_at`/mtime 均不早于 summary 时才允许共存。这个可续作步骤不读取或迁移旧 entry，也不删除任何路径；任一旧目录、非空 index、未知文件、旧/不确定 run 或证据不完整都会在创建 run evidence 或启动 OneNote mutation 前 fail closed。

清理结束后会自动收敛 maintenance 自身的残留：成功 target 的完整逐项证据先嵌入 durable summary，再删除对应 `deleted` receipt；pending、failed、无 summary 绑定或内容不匹配的 receipt 原样保留。`clear cache/all` 还会从 index 移除已无 payload 的 tombstone，并只对 32-hex fingerprint 下可证明为空的 `a`、`instances` 和 fingerprint scaffold 逐层 `rmdir`；非空、含 lock、未知形状或 reparse point 的目录不碰。Dry-run 的 `finalization_plan` 会列出可收敛数量。`.local-validation/` 根、managed marker、summary，以及 cache marker/quarantine/recovery history 始终保留。

每个具名 action 都可显式保留已验证的操作现场，供 OneNote UI 人工验收：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-page --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py reorder-section --keep-worksite
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --keep-worksite
```

`--keep-worksite` 会隐含保持源 Notebook 打开，并在成功 read-back 验证后保留该 action 的现场：`rename/reorder-page/reorder-section/reparent-page/reparent-section/reparent-section-group` 跳过反向恢复，Copy 跳过目标 cleanup，`create/delete/move-page/move-section/move-section-group` 保留其原本最终状态以供查看。精确目标 ID、原/现 predecessor、现场状态和人工清理说明写入 `worksite.json`。Page reparent 若由 OneNote 重映射 ID，会同时记录 `target_id`、`current_target_id` 与完整 `id_history`。该选项不会扩权；Copy 场景反而从 policy/tool allowlist 移除不再需要的 Delete/Close cleanup 权限。默认不传时仍执行各 scenario 原有的 restore/cleanup 与生命周期策略。支持的 `reorder-section` 和四个具名 Reparent 场景均已显式纳入 `all`；功能受限且真实验证失败的 `reorder-section-group` 已从生产 Tool 和公共 scenario 目录移除。

UT-004 不增加 `batch-*` scenario 或 `batch_*` Tool。既有 `create`、`rename`、`delete` 与三个 `reparent-*` 场景通过原公开工具名的 `items` 参数覆盖 bounded batch 路径，并强制检查全部 item 完成后的 content-free `final_hierarchy`。`delete` 使用独立的测试期 Batch Mutation Page 上限 5：fixture 总 Page 数高于该值，混合 `include_subpages=false/true` 的两个无重叠 Page 根以整批方式验证“保护并提升排除子页”和“完整子树删除”；另一个包含六个 Page 的 Section scope 必须以 `effective_pages` 在 mutation 前拒绝，bridge audit 只允许 `get_hierarchy` 且前后 snapshot 不变。`create` 默认路径会先提交两个规范化后重名的 Section item，要求 `validation_error`、`mutation_stage=preflight`、`mutation_attempted=false`，bridge audit 仅允许 `get_hierarchy`，并以前后 snapshot 证明零改变。`reorder-page` 先分别验证 `include_subpages=false/true` 的子页保护与完整块移动并恢复，再对 Page parent 的成功 Sort 后提交 `child_type=section` 冲突请求，以同样的预检拒绝、read-only bridge audit 和 unchanged snapshot 证明 Sort 负路径。`reorder-page` 和 `reorder-section` 的正向路径继续覆盖唯一的 `sort_children`。`sort_children` 由父类型推断子类型：Notebook/SectionGroup 只排序直属 Section，Section/Page 只排序直属 Page；不排序 SectionGroup，也没有 recursive 模式。其完整直接子序列最多接受 1000 个确认 ID，并继续受 Notebook resource/Page 预算约束，不复用 batch 的 20 项上限。

真实 OneNote 验收不故意制造中途后端故障。Batch partial-failure 的最终依据是确定性 fault-injection 合同：Create/Rename/Reparent/Delete 各要求第一项 `applied`、第二项 `failed`、第三项 `not_attempted`，公开 `partial_failure` envelope 保留输入序号、人工恢复指引、`rollback_attempted=false` 和 `mutation_replayed=false`。用户只需 human-gated 运行上述可确定、mutation 前的拒绝探针；不以不稳定或有意损坏真实后端作为完成门限。

Cache materialization 会先按 Notebook-relative typed address 重绑结构 ID。Reparent Page 的 run-local evidence 还会显式重绑顶层与 List/Tag 两个 `page_id` 字段，并把两项映射写入 `cache-structure-remap.json.evidence_rebinding`；缓存模板保持不变，字段缺失或 source ID 不一致会在 scenario mutation 前 quarantine 并保留失败现场。四个具名 Reparent 场景的**生产 Tool** read-back 只做轻量 hierarchy 稳定观察与 content-free hierarchy evidence capture：Page/Section 要求连续两次稳定，SectionGroup 要求连续四次；随后 manual runner 自己保留带 hierarchy bookend 的完整逐 Page evidence capture。完整读取只允许重试一次且不会重放 mutation；失败 evidence 用 `readback_phase` 区分 hierarchy convergence、hierarchy evidence capture 与 manual invariant validation。

四个 Copy 层级场景都自动创建两页组成的完整 Page 保真 fixture。Section/Group/Notebook Copy 使用通用名称；Page Copy 为便于 UI 对照，使用等价的编号名称：

- `Rich-Page` / `01-Source-Parent`（父页）：基础内容为已确认的 `Outline/RichText/Table/Image`；无公式页面使用 `semantic_content_v1`，分别验证有效标题、富文本、表格、非空 Outline、对象类型与 binary hash，并只容忍 TODO 037/UT-009 冻结的三类 COM 规范化。其中 `copy-page` 的 `01-Source-Parent` 额外包含一个行内 Presentation MathML 公式和一个 `display="block"` 单行公式；行内公式仍属于 `RichText`，单行公式分类为 `DisplayEquation`，整页因此使用 `semantic_display_equation`；
- `List-Tag-Page` / `02-Source-Child`（子页）：程序通过受限 HTML 自动生成三个编号/项目符号与 To Do 标签混合项（完成、未完成、完成），使用 `semantic_list_tag` 验收。

UT-008 的 `get_page_text` 真实后端验收复用上述既有 scenario，不新增场景。`copy-page` 在六个 Copy case 前执行完整读取合同：对严格父页与语义子页省略 `mode` 并要求默认 `rich/sanitized_html_v1`，对父页显式读取 `mode=plain`，再以 `max_chars=192` 验证默认 rich 的有界、well-formed 截断与 raw XML/binary/script/unsafe URI 负合同。`reparent-page` 对目标页在正向 mutation 前、ID remap 后及默认恢复后执行 default-rich 投影 smoke，并比较 content-free 语义签名；`--keep-worksite` 只比较 before/after 并明确记录未请求恢复。`copy-section` 对源父/子页及同 Notebook、跨 Notebook 两个复制目标比较 default-rich 语义签名；`copy-section-group` 不重复该职责。三者都写 `page-text-projection.json`，只保存结构计数、安全布尔值和可见文本 SHA-256，不保存 HTML、plain text、Page ID、raw XML 或 binary；MCP audit 的 `html` 字段也统一按长度与 SHA-256 脱敏。

本轮由用户本人在前台完成的定向复验如下；Agent、pytest、CI 和后台任务不得执行这些真实命令：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section
```

完成判定：三项顶层与 scenario 均为 `passed`、lifecycle 均已关闭；每项 `page-text-projection.json` 均声明 `content_persisted=false`，全部 safety 项为 true；`copy-page` 的 default rich、explicit plain、bounded rich 三组证据通过；`reparent-page` 的 forward/restore signature comparison 通过；`copy-section` 两个 case 的 parent/semantic-child signature comparison 均通过。

2026-08-17 定向复验最终全部通过。`run-2026-08-17-21-48-22` 的 `reparent-page` 在 before、ID remap 后与 restore 后均返回默认 `rich/sanitized_html_v1`，三阶段 safety 全 true，forward/restore signature 匹配，最终 restored 且 Notebook closed。`run-2026-08-17-21-49-09` 的 `copy-section` 对源、同 Notebook 目标和跨 Notebook 目标的六份默认-rich 投影完成全部 safety 检查，两个 case 的父/子 signature 均匹配，目标清理后 restored，两个 Notebook closed。`copy-page` 的前两次 run 分别暴露 conditional-comment MathML 丢失和 scenario client metadata 误判，均在 mutation 前 fail closed 且精确关闭；两处修复后的 `run-2026-08-17-22-18-10` 顶层/scenario 均 passed、restored=true：父页默认 rich 含两个 MathML 及 formatting/table/image，语义子页含 List/Tag，explicit plain 业务字段为 `{text, chars}`，bounded-rich 从 1363 字符截断为 189/192 字符且 well-formed/safety 全通过，证据 `content_persisted=false`。随后六个 Copy case 全部 `copy_contract_satisfied/verified/lossless=true`、零 issue，9 个目标均完成非永久 cleanup，两个 Notebook closed。该结果与前两项通过共同闭合 UT-008，但不外推为跨 OneNote 版本保证。

`copy-section` 与 `copy-section-group` 都使用固定的 `source`/`destination` 双 Notebook recipe，并在同一个 scenario MCP 中顺序执行两个 case：先复制到 source Notebook 内的精确目标父级，再复制到 destination Notebook 的精确目标父级。每个 case 都有独立的稳定 plan、before/after、Copy response 和角色证据；第二个 case 还保护第一个 case 已创建的 Page 内容不被改写。默认按跨 Notebook→Notebook 内部的反向顺序，对两个完整 target subtree 逐叶到根执行非永久 cleanup，并验证两个 Notebook 恢复；`--keep-worksite` 则保留两个目标和整个 working bundle。两类 Recipe version 3 使旧单 Notebook cache entry 不会命中新合同。

`copy-notebook` recipe version 3 的 source 除原根 `Source-Section` 富内容子树外，还包含 `Source-Group/Grouped-Section/Grouped-Page`，因此 Notebook Copy 必须同时证明根 Section 与嵌套 SectionGroup 子树的完整映射。Copy 目标完成 snapshot 验证后，Runner 会立即通过精确 target Notebook ID 重新读取最新 `name/modified`，把该值保存到 `close-confirmation.json`，再调用一次 `close_notebook`；不会继续使用 Copy response 中可能因 COM 延迟更新时间传播而过期的 `modified`，也不会重试 close mutation。

`run-2026-08-16-21-48-24` 再次证明 Notebook 根、根 Section、嵌套 SectionGroup 与三页均完成 `verified=true/lossless=true` 的七对象 Copy，但随后精确关闭复制品时因 scenario 未启用 Notebook Lifecycle 而 fail closed；源 Notebook 已关闭，复制品 `Copy-Notebook-2026-08-16-21-48-24` 保持打开并保留完整证据。当前 `copy-notebook` 使用专属最小 policy：Create + Writes + Local File IO + Notebook Lifecycle，仍不启用 Delete。注册期现在同时校验 fixture creation tools 和完整 scenario allowlist 的独立 policy gates；声明 `close_notebook` 却缺少 lifecycle gate 会在注册时失败。

Lifecycle policy 修复后，用户连续运行的 `run-2026-08-16-22-02-40` 与 `run-2026-08-16-22-12-29` 均通过：七对象/三页 Copy 为 `copy_contract_satisfied=true`、`verified=true`、`lossless=true`，复制目标均为 `closed_not_deleted`，源 Notebook lifecycle 均为 `closed_preserved`，没有启用或执行 Delete。这两份独立真实证据闭合了 `copy-notebook` 的 Copy 与双侧关闭合同。

2026-08-11 用户真实验证：`run-2026-08-11-21-33-01` 的 `copy-section` 与 `run-2026-08-11-21-36-13` 的 `copy-section-group` 都以 `decision=cold_build` 完成 source/destination 双 role materialization，并分别通过 Notebook 内部与跨 Notebook 两个 case。Section 的两个 case 各映射 3 个对象，SectionGroup 的两个 case 各映射 4 个对象；四份 Copy report 均为 `verified=true`、`lossless=true`。两次运行随后反向执行精确非永久 cleanup，两个 Notebook 都恢复并关闭，`opened_template=false` 且 cache template inventories unchanged。`run-2026-08-11-21-31-17` 的 `copy-notebook` 同样以 `decision=cold_build` 通过，完整映射 Notebook 根、根 Section 的 Rich/List-Tag 双页，以及新增的 SectionGroup/Section/Page 子树，共 7 个对象；三页分别通过 strict、semantic List/Tag、strict comparator。目标 Notebook 在精确 ID 最新回读后以刷新后的 `modified` 一次关闭，`close-confirmation.json` 与 `closed_not_deleted` 证据通过；源 working Notebook 正常关闭，模板未打开且 inventory 不变。Notebook Copy 的 `restored=false` 是预期语义：COM 只提供关闭目标 Notebook，不提供 typed Notebook 删除，目标目录和 run evidence 均保留。

`move-page` 不重复承担内容类型取证。它使用仅含已验证 Outline/RichText 的两个独立最小源子树，以及另一个 Notebook 中的目标 Section；场景验证生产 Copy 已返回 `verified/lossless`、范围与 `id_map` 精确，以及后续非永久源删除和排除后代保留正确。Table/Image 的 `semantic_content_v1` 取证由共享 `copy-page` 富内容 fixture 承担；只有 Copy comparator 已验证时 `move-page` 才可进入相同的源删除门，二者共同覆盖 UT-009，不能把其中任一项单独外推为新真实证据。

`move-section` 与 `move-section-group` 同样不重复内容类型取证。每个场景创建精确的 `source`/`destination` 双 Notebook bundle，只把一个最小 Outline/RichText 容器子树移动到 destination Notebook 根。场景要求完整 `id_map` 和 verified/lossless Copy，只允许一次源容器根删除，且结果必须声明 `source_deleted_nonpermanently=true`、全部原源子树 ID inactive、目标 ID 全部位于 destination role。两者在独立真实验收与稳定性审查完成后均设置 `included_in_all=True`，与 `move-page` 一起进入显式 human-gated 的 `all` 批处理；真实命令仍只能由用户本人运行。

`copy-page` 是一个 `source`/`destination` 双 Notebook bundle。早期 recipe 在严格 Parent 中新增一个行内公式和一个独立单行公式，并要求回读恰好两个 MathML root、一个 `display="block"` 与两个规范 namespace 声明。`run-2026-08-12-01-11-36` 与 `run-2026-08-12-01-25-30` 证明 MathML 本身保持，但单行公式前出现空白；后续 detector/capture 又把该差异定位为 OneNote 在 DisplayEquation 前写入纯空白 `span + br`。v9 首次进入完整 `all --use-cache` 时，`run-2026-08-12-15-54-16` 与单独复跑 `run-2026-08-12-16-11-26` 都证明把 display marker 与公式放在同一次 append 会被真实 COM 合并，旧 detector 对“公式独占一个 OE、前驱 OE 非空”的布局假设不成立。当前 recipe v10 先写入普通富内容、行内公式和 marker，再用独立 append 写入 block MathML；fixture 门要求公式所在文本没有可见残留，并精确观察到当前环境已验证的一个 `span + br` 前置空行，不再依赖 OE 独占或前驱 OE。`run-2026-08-12-16-30-58` 已证明 v10 fixture/cold publish/materialization 成功，首个 Copy 目标的拓扑、正文、对象、binary 和两条公式语义也保持；失败只剩清除已知 span 后的页面 canonical 差异。增强诊断后的 `run-2026-08-12-16-44-30` 进一步证明 source/target 条件包装数同为 2，并把首差异定位到 formula-only Outline 的 `Size.width/height`。Comparator 现在只规范化完整配对公式条件包装，以及节点集合受限、恰好一个完整 block 公式且没有正文/其他 markup 的独立 Outline 派生 Size；Position、混合内容 Outline 和普通/不完整注释继续 fail closed。Copy 发送前仍清除已知空白包装并使用 `semantic_display_equation`，行内公式继续作为 RichText 的有界 MathML 严格比较。v10 fingerprint 使旧 cache 不会命中；独立 `copy-display-equation` 的三跳真实运行已经证明空白包装规范化稳定，formula-only Size 规则和完整 `copy-page` 六 case 后续也已由真实 run 闭合。六个 case 继续覆盖同 Section、同 Notebook 跨 Section、跨 Notebook 三种目标范围，各自再覆盖 root-only 与完整子树；每个 case 都要求新 target IDs 与 source/anchor IDs 不相交，保持 Parent/Child 相对层级和两个 destination anchor 的正文、order、level 与 parent。默认按反向 case 顺序清理六个根目标并验证两个 Notebook 恢复；`--keep-worksite` 则保留全部目标供 UI 对照。Recipe v12 还会在 fresh fixture 阶段从 `get_page_content_objects` 选择唯一 Image 的公开 `id`，调用 `get_page_content_object_binary(page_id, page_content_object_id)`，只记录媒体类型、解码字节数与 SHA-256，绝不保存 Base64 或内部 `callback_id`；该证据已由后续 v13 和最终批次真实 run 闭合。

`run-2026-08-16-21-43-52` 的首次 v12 真实复验已证明 Local File IO policy 闭合，但同时暴露旧 `page_info=all` 对象快照把 binary 直接嵌入 XML、没有返回 `callbackID`，导致新增 Image 无公开可寻址 ID。生产读取随后改用不嵌入 payload 的 `page_info=file_type` 获取 callback metadata；没有 `objectID` 的二进制叶对象把微软定义的 binary-object OneNote ID 作为 Page-scoped fallback，同时具有 `objectID/callbackID` 时仍执行两者转换。该阶段修复的不足由下一次 run 继续暴露，最终由 v13 闭合。

`run-2026-08-16-22-01-46` 的第二次真实复验推翻了“只切换 `page_info` 即可闭合”的假设：公开 mapper 仍回读到唯一 Image 的 `id=null/callback_id=null`。结合 COM 的 callback 合同与 OneNote 合法 XML 中直接子节点 `<CallbackID callbackID="…"/>` 的表示，代码审查定位到高可信 parser 形状遗漏；旧 parser 只读取内容元素自身的 `callbackID` 属性，但该 run 没有 opt-in 保存 raw Page XML，因此不能把当前 Image 的底层节点形状写成已经直接捕获的事实。Recipe v13 同时接受这两种合法表示，并以 content-free 布尔结论证明公开 Page-scoped ID 已重新定位到 callback；证据只保留媒体类型、解码字节数与 SHA-256，不保存 Base64、公开对象 ID 或内部 `callback_id`。两次失败均在 mutation 后完成隔离 Notebook 关闭并保留证据；下一次 `run-2026-08-16-22-09-07` 随后闭合了 v13 的真实证明。

用户随后完成 `run-2026-08-16-22-09-07`，v13 `copy-page` 真实复验通过。唯一 Image 取得非空公开 Page-scoped ID 与 callback，`get_page_content_object_binary` 使用该公开 ID 成功返回 PNG；当前对象的公开 ID 与 callback 相同，因此证据为 `callback_resolution_verified=true`、`public_id_distinct_from_callback=false`，不能把这次运行扩大解释成“真实观察到两条不同 ID”。Base64 在 scenario call log 中只保留长度与 SHA-256 redaction，manifest 只保留 content-free resolution 布尔值和 payload hash。六个 Copy case 全部 `verified/lossless`，九个目标按精确 ID 清理、`restored=true`，source/destination Notebook 均 `closed_preserved`。

全部定向复验通过后，用户于 2026-08-16 22:16–22:37 执行最终真实 `run.py all`。该批次留下从 `run-2026-08-16-22-16-13`（`create`）到 `run-2026-08-16-22-35-25`（`query`）的 18 个连续、顺序匹配注册表的 child run；18 份均具有 durable `run-result.json`、顶层与 scenario 状态 `passed`、`lifecycle.closed=true`，所有单 Notebook 或双 Notebook role 都为 `closed_preserved`。最终批次内的 `copy-page`（`run-2026-08-16-22-24-28`）再次记录 `callback_resolution_verified=true`、`public_id_distinct_from_callback=false`、`payload_persisted=false`；Search 预算探针按预期产生受控失败 envelope，而 scenario 最终通过。最终结果为 18/18 通过、0 个缺失 result、0 个未关闭 lifecycle。此前定向 run 已由用户交互式 `clear runs` 清理；root-level `cleanup-summary-61fee4694db642259faaaa96e50371ee.json` 证明 84/84 owned run roots 删除成功、0 failed/refused，并保留被清理 run ID，本文保留其清理前已审核的 content-free结论，不声称旧明细仍在磁盘上。

唯一特殊入口 `all` 会按显式 `included_in_all` 资格的顺序串行启动其中的 scenario：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

非 JSON `all` 会实时转发当前 child 的 content-free progress，并用 `<scenario> |` 前缀区分 child 内的 `[phase/total]` 与 `all` 外层的 `[scenario/total]`。`quiet` 实时显示主要阶段，`normal` 继续显示 case/mutation progress 和紧凑结果，`verbose` 再实时显示 command、timing 与 stderr；父进程不会等整个 scenario 结束后才一次性回放 stdout。`quiet/normal` 的成功 stderr 保持隐藏，失败时输出经过脱敏和行数/字节限制的 stderr 尾部。`--json` 保持稳定 JSON Lines，不混入终端文本 progress。

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

`--use-cache` 只改变 fixture 来源：validated hit 把关闭的 immutable template opaque-copy 到本次 run 的 role-specific working 路径；cache build 已负责生成并验证 immutable template 的权威基线。每个 role 只打开一次 exact working path，并在同一个短命 PowerShell/COM session 内按精确 parent 批量 `OpenHierarchy`。随后 runner 重新枚举完整 hierarchy，按 Notebook-relative typed address 重绑全部 SectionGroup、Section 与 Page ID，要求连续两次结构稳定，然后只捕获一次完整 `scenario before` snapshot。默认内容场景会从同一次 `get_page_xml(page_info=all)` 派生每个 Page 的 hash、能力与 normalized object evidence；声明 recipe-owned metadata snapshot 的只读场景则只使用自己的工具族取证，不跨入另一浏览工具族。Cached manifest 中明确属于旧 run 的 `notebook_copy_root`、逐 role working Notebook path 和 lifecycle lease 会在先证明同源关系后按字段重绑到当前 run，并记录 `cache-run-local-path-remap.json`；不会递归替换内容，也不会修改模板，旧字段缺失或彼此不一致时 mutation 前 fail closed。Materialization 只复用 fixture 内容/结构证据；`scenario_spec`、`scenario_policies` 与 `mcp_process_contract` 每次都由当前 runtime spec 重建，报告不会继承 template 中已经过期的工具名或权限声明。`cache-materialization.json` 将 materialize 决策与真正的 validated-hit/cold-build/bootstrap 来源分开记录，`cache-hierarchy-convergence.json` 细分 hierarchy/`scenario before` 耗时，`scenario-before-snapshot-handoff.json` 记录单次消费状态。Template 永不打开或修改；Cache 不保存 working lease，也不与 run 维持所有权或生命周期关系，多个 run 可从同一 immutable entry 得到各自唯一的 working paths。Programmatic miss 仍先构建并 live-validate fresh bundle、精确关闭、发布 immutable template，再只打开一次新的 materialized working copy。working activation、COM 或 convergence 失败只保留 run-local 失败证据，不再自动 quarantine 已验证 template；确定性的 template inventory、身份或缓存证据失败仍会在既有 cache 门限 fail closed。任一 ID rebind、双稳定、内容验证或 handoff 失败均保留 working files/evidence；默认 failure finalizer 精确关闭当前 lease，显式 keep 才保持打开。

每个命令在 dispatch 时只读取一次主机本地时区，并冻结 run identity。Notebook、默认 run 目录以及 Copy/Move 目标名称共享 Windows-safe 的本地显示时间，例如 `2026-08-11-11-05-49`。完整本地 ISO 时间、UTC offset 和时区名称仍保存在 `run_identity`；JSON 中的 `created_at`、`failed_at`、`closed_at` 等事件字段仍使用 UTC ISO-8601。immutable template 继续使用内部 `template-notebook` 目录名，不作为 OneNote Notebook 打开。

Cache 固定为未纳入版本控制的 `.local-validation/fixture-cache/`。除上述 proof-backed 空壳激活外，只有带新 schema managed marker 的该根目录可被 cache runtime 操作；完整 64-hex fingerprint 与 logical instance identity 保存在 index/entry/lock/evidence，磁盘只使用 `<fp32>/instances/p` 或 `<fp32>/instances/a/<1..24 hex>`。失效清理只允许精确 typed entry，并要求 root containment、ownership、无 reparse point且 template 实际路径未被 OneNote 打开。Run-local working Notebook 是物理独立副本，不参与 cache cleanup 门禁。`.one` 和 `.onetoc2` 只作为 opaque bytes 复制/散列，绝不解析、编辑或回写。模板从不由 OneNote 打开。

所有新建的 cache、`.s-<16 hex>` publish staging、`.m-<16 hex>` materialize staging、working copy、inventory/artifact 和 JSON/XML 原子临时路径都使用普通绝对 Windows 路径，并在 copy、atomic publish 或 OneNote open 前完成 240 UTF-16 code units preflight。Role 最多 12 字符，working leaf 最多 64 UTF-16 units，run evidence leaf 最多 64 units，opaque relative path 最多 96 units/8 层；项目不依赖 `LongPathsEnabled`，不使用 `\\?\`。Opaque tree 每层先预算子路径再 `stat`/进入/读取，避免无界扫描先触发裸 `WinError 3`；authored working bundle 的 live projection 同时核对完整 64-hex digest 和 24-hex instance key。Maintenance 在取得只读 COM snapshot 前预算自身将生成的 open lock、marker、receipt、summary、必要 index 与原子临时路径，但把已经存在的 managed payload 当作 cleanup 输入：路径预算不会成为删除门限，ownership、固定根 containment、plain-tree/reparse、实际打开状态和交互确认仍全部 fail closed。新建路径预算失败仍以非零 `path_budget_exceeded` 返回 phase、target、limit/actual/over-by、触发路径、零/已发生副作用、`failure_evidence_written` 和 typed remediation；`WinError 3` 不进入仅面向 `WinError 5/32` 的状态守卫重试。

OneNote COM 返回的 Notebook、SectionGroup、Section、Page 和内容对象 ID 永不进入受管物理名称。Scenario artifact 使用固定语义名与有界 ordinal（例如 `cleanup-created-page-02-result.json`）；完整 ID 继续保存在 response、manifest、lease 和其他 JSON evidence 内。JSON/XML evidence 与 working name 在运行时拒绝 canonical OneNote ID，纯测试还会扫描 manual-validation 源码，阻止 `*_id` 或 `["id"]` 再被插值到路径型 f-string。

Manual-validation 的本地原子发布（cache entry、working directory、JSON/XML evidence 及 maintenance receipt/summary）在 Windows `WinError 5/32` 下使用状态守卫的短时退避：首次失败后最多等待 `50/100/200/400/800ms`，总预算约 1.55 秒。每次重试前必须证明 source 和 destination 与首次尝试时完全一致；任何出现、消失或身份变化都会 fail closed。该机制只处理本地 `os.replace` 的扫描/共享冲突，不重试文件删除、OneNote COM、MCP tool 或任何 mutation，也不放宽既有权限和人工门限。

Cache lookup 会区分真正不存在的实例与目录仍被保留的 `invalid` entry。历史上仅因 working-copy `materialized-open` 阶段被误隔离的 entry，可在原始 validation 与 byte inventory 重新通过时恢复；其余 `invalid` entry 必须先在 fingerprint lock 内通过上述安全门限执行精确清理，再以 `decision=invalidated_rebuild` 重建。该检查在首次 lookup 与 programmatic publish 前都会执行，避免并发隔离再次退化为发布冲突。`cleanup_failed`、缺失 ownership metadata、未知状态或 template 实际路径仍打开都会阻止重建；publish 始终拒绝覆盖任何现有实例。

2026-08-11 用户真实验证：layered Copy recipe version 2 将 fixture/live validator 与 Copy plan 统一到 live Page XML capability projection 后，`run-2026-08-11-13-31-57`、`run-2026-08-11-13-33-47`、`run-2026-08-11-13-37-37` 和 `run-2026-08-11-13-39-13` 使用同一旧版单 role `copy-page` fingerprint，依次覆盖 `decision=cold_build`、带 `--keep-worksite` 的 `decision=validated_hit`、执行默认 cleanup/restore 的 `decision=validated_hit`，以及带 `--keep-notebook` 的 `decision=fresh`。四次的 root-only case 都以 `strict_canonical` 验证单页 RichText/Table/Image，full-subtree case 精确映射父子两页，并分别以 `strict_canonical`、`semantic_list_tag` 验证父页和 List/Tag 子页；全部 Copy report 均为 `verified=true`、`lossless=true`，没有 issue 或 skipped content。cached run 证明 `opened_template=false`、template inventory 不变；默认 hit 与 fresh 都精确清理三个 Copy 目标并 `restored=true`，前者关闭 working Notebook，后者仅按 `--keep-notebook` 保留已恢复的 fresh 源 Notebook，且未生成 cache runtime artifact。四次都只启动一个 MCP process；总耗时/bridge calls 分别为 90.271 秒/279、66.802 秒/210、86.440 秒/274 和 84.381 秒/237。该矩阵闭合了 TODO 014 的单 role A 验收，但单机观测不能推广为固定性能提升比例。

2026-08-11 用户真实验证：recipe version 3 的双 Notebook、六 case 合同使用 fingerprint `ad0bf5be9c5eee60d0dfdebfca6cfa27a3dc5ae223f4dcb7327b5cee24736212`。`run-2026-08-11-14-27-08` 完成 cold build、逐 role live validation、关闭发布和重新 materialize，随后在 Copy 前因 runner 缺少 destination snapshot evidence 失败；该问题及重名 Page created-target 定位问题修复后，`run-2026-08-11-14-54-05` 与 `run-2026-08-11-14-57-01` 连续以 `decision=validated_hit` 完成全部六 case。两次运行的每个 Copy report 均为 `verified=true`、`lossless=true`，source/destination Notebook ID 互异，`opened_template=false`，且各自只启动一个 MCP process。前一次按默认语义反向清理六个根目标、两侧 `restored=true` 并关闭 working bundle；后一次以 `--keep-worksite` 保留全部六个目标和两个 working Notebook。用户确认不再补跑，TODO 014 阶段 B 据此闭合。

2026-08-11 用户真实验证补齐 working lease 的身份边界：`run-2026-08-11-19-07-17` 以 `decision=validated_hit` 和 `--keep-worksite` 保留第一组双 Notebook working bundle；在其 lease 仍为 active 时，`run-2026-08-11-19-10-38` 从相同 fingerprint/instance 再次 `validated_hit`，materialize 到另一 run directory，并为 source/destination 获得与第一组全部互异的 live Notebook ID。第二个 run 的六个 case、cleanup/restore 和双 Notebook close 独立通过，未关闭或修改第一组 worksite。该证据确认 fingerprint/instance 不是排他 lease key；只有实际 ID 集相交、同 ID 异路径或身份尚未可靠重绑定才拒绝。相反，`run-2026-08-11-18-46-59` 在 hierarchy activation 中途失败并保留未完成独立 live identity 建立的 bundle，`run-2026-08-11-18-50-54` 因实际 ID 冲突在 mutation 前精确拒绝；用户关闭旧 working Notebook 后，`run-2026-08-11-18-51-26` 完成 stale reconciliation、validated hit、六 case、cleanup/restore 和 close。结合既有两次 `invalidated_rebuild`，TODO 014 阶段 C 的并发隔离、真实 ID 冲突保护、关闭后恢复和受控失效证据据此闭合。

2026-08-11 TODO 015 增强复验：`run-2026-08-11-15-41-20` 的同标题 Create 返回两个 fresh、互异且 allocated/read-back 一致的 ID，正文独立可读，并完成默认非永久 cleanup、restore 和 close；`run-2026-08-11-15-43-26` 的 Move 返回两个 fresh target、`verified=true/lossless=true`、anchor unchanged，之后才按叶到根非永久删除源并关闭 Notebook。v4 `copy-page` 的 `run-2026-08-11-15-46-34` 暴露空 selection T 比较误报；`run-2026-08-11-16-06-07` 随后暴露同一占位符因转换顺序造成“目标标题 + 原标题”；`run-2026-08-11-16-11-01` 再暴露最终 restore 对无关 Description Page 后台重序列化比较过宽。三项均按严格保护对象边界修复。最终 `run-2026-08-11-16-18-20` 以同一 v4 fingerprint validated-hit，六个 case 按 `1/2/1/2/1/2` 映射 9 个 fresh、互异且与 source/anchors 不相交的 target；全部 `verified=true/lossless=true`，source/anchors 不变。默认反向清理 9 个 target 后 `restored=true`，source/destination 双 Notebook 均 closed，cache template inventories unchanged；全程只启动一个 MCP process。TODO 015 据此闭合。

当前保留的具体交互 recipe 和一个 bounded UserAuthored recipe 各有固定、不会进入 `all` 的 bootstrap Scenario。它们创建 fresh disposable Canvas/authoring zones，写 run-bound checkpoint，以有界 timeout 等待用户本人添加 synthetic 内容并给出精确 verdict；成功后关闭源、发布模板、再 materialize 第二份 working copy并 live validate。`--keep-worksite` 明确阻止发布。Agent、pytest、CI、hook 和后台进程不得执行这些真实命令。

交互 detector 只接受公开 Page 对象模型的 `kind`，并把 `Outline`/`OE` 作为结构支撑节点；请求类型必须精确匹配。TODO 004 已闭合 `InkDrawing`（自由墨迹）、OneNote UI Shape（形状）、通过“插入 → 录制视频”创建的 `MediaFile`，以及复用既有 ready fixture 的 `InsertedFile` Copy 证据。两次用户 discovery 证明矩形和箭头都公开为 `kind=InkDrawing`，而不是字面量 `kind=Shape`；两者共同含 `ShapeInfo`，箭头还含形状相关的 `AnchorPoint`。因此 Shape detector 固定要求“恰好一个公开 `InkDrawing` 对象 + content-free projection 中恰好一个 `ShapeInfo` + capability `UIShape`”，普通自由墨迹必须拒绝；`AnchorPoint` 作为可选结构保存并在 Copy 前后精确比较。`FileAttachment` 的专属 bootstrap/Recipe 已删除，因为当前 OneNote GUI 多次只生成 `InsertedFile`、无法形成独立可验证 fixture；`MeetingInfo` 的专属入口也已删除，因为内容小众、难生成且当前价值低。`Embedded Spreadsheet`（内嵌电子表格）同样明确不支持且没有专属入口；尚未观察到它的公开 `kind` 或 XML 表示，不得把它映射为 Table、InsertedFile 或 FileAttachment。三类排除项都不获得共享 Copy 合同或 Move 源删除放行。FileAttachment 的历史证据、`kind` 边界和观察环境只保留在 [`docs/lesson/onenote_page_object_kind_and_file_attachment_representation.md`](../../docs/lesson/onenote_page_object_kind_and_file_attachment_representation.md)；完整排除边界见 [`docs/lesson/copy_content_type_exclusions.md`](../../docs/lesson/copy_content_type_exclusions.md)。

用户确认后，runner 在验证前先写 `interactive-authored-snapshot.json` 和 content-free `interactive-detection.json`。后者固定记录 requested/observed/missing/unexpected/supporting、对象计数和 capability projection。失败时初始 `fixture-snapshot.json` 不被覆盖，cache 不初始化、不发布；working files 和诊断保留，Notebook 默认由 failure finalizer 精确关闭，只有显式 keep 模式才保持打开。错误摘要会显示精确类型和计数。

`interactive-copy-ink-drawing`、`interactive-copy-ui-shape` 与 `interactive-copy-media-file` 是各自 bootstrap 的 cache-only、Copy-only consumer。它们只接受固定 `ready` instance，materialize 后先重跑精确 detector，再用连续两次相同的只读 plan 绑定 source/destination。InkDrawing/UIShape 各执行一次 root-only Page Copy；MediaFile 在同一场景中先复制到原 Section，再创建一个 run-bound 新 Section并执行第二次 root-only Copy。第二个 case 必须证明第一个 target 和 source 都未变化，两个 target 分别写入独立 plan/copy/machine-comparison evidence，最后一个 run-bound 人工 verdict 同时确认两者的显示与播放。静态 policy 使用 Create + Writes；MediaFile 为创建精确目标 Section 使用同一 Create gate，但仍没有 Delete、Move、Permanent Delete 或 Raw XML。Copy targets 永远保留在 disposable working artifact。若 `copy_page` 已精确创建唯一 target、源未触碰且唯一失败是 canonical read-back，场景会把结构化 `partial_failure` 作为诊断证据继续处理，而不是把它当作生产成功。MediaFile 仍要求 strict canonical；InkDrawing 使用 `semantic_ink_drawing`：请求/目标 detector、公开对象稳定签名、InkDrawing 节点结构、非几何属性和 Ink 数据 hash 必须精确一致；仅 `Position.x/y/z` 与 `Size.width/height` 允许基于真实 COM 量化证据的 `1e-4` 绝对容差。UI Shape 的 `semantic_ui_shape` 复用相同的 Decimal 逐字段 comparator、结构/data hash 和失败证据，但基于 `run-2026-08-11-22-18-48` 的真实 Shape bounding-box 重算证据使用独立 `0.02` 绝对容差；同时额外要求 source/target 各有一个 `ShapeInfo`、完整 shape marker 计数相等，因此矩形与箭头的 `AnchorPoint` 差异不会被动态忽略。evidence 固定记录每个几何字段的 source/target、absolute delta、最大 delta 和是否越界；非数字、缺失/额外字段或越界一律失败。visible text、content-object/binary checks、无 omitted content 和受限 issue 集也必须通过。Ink 子树之外的 Page canonical 漂移会被记录但不单独否定 Ink/Shape 证据。证据只保存 count/hash，并固定记录 `payloads_exposed=false`，不落盘 raw payload。随后用户必须对精确 target 给出 run-bound `ACCEPT` 或 `REJECT`；机器或人工任一失败都保留现场并且不产生放权。

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

`all` 本身不是 scenario，不创建共享 Notebook 或共享证据目录，也不接受 `--run-dir`、`--notebook-label`、`--notebook-name`、`--keep-notebook` 或 `--keep-worksite`。每个已注册子命令仍创建自己的默认 Notebook 和 `.local-validation\run-<YYYY-MM-DD-HH-MM-SS>`，使用自己的 MCP 子进程、最小权限、报告与 lifecycle。具名 scenario 无论单独运行还是由 `all` 启动，失败都默认精确关闭本次 run 的全部 leased working Notebook，同时保留 working files、evidence 和 cache template。`all` 只有在 child 写入 durable `failure-finalization.json` 并通过内部握手证明全部 exact lease 已关闭后才继续下一个任务；close 失败、证明缺失或异常退出立即停止。最终进程仍返回首个场景失败码。`all --dry-run` 不会创建或保留 Notebook，因此仍检查全部已注册计划。

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
│  └─ Disposable-Section
└─ Disposable-Section
   └─ Disposable-Page
```

场景保存 `before.json/create-results.json/after.json`，要求两次 COM allocated/read-back ID 完全一致、互异、均为 fresh Page 且属于 `Duplicate-Title-Target`，两份不同正文可独立回读。默认按两个精确 Page ID 非永久删除并以 `restored.json` 证明恢复；`--keep-worksite` 跳过该清理、保持 Notebook 打开并记录精确 IDs。

`onenote-convergence` 是默认不进入 `all` 的 fresh-only 生产可靠性场景，拒绝 `--use-cache`。它按当前 53 Tool wire contract 执行 `request_notebook_sync`，创建并关闭第二个 disposable Notebook，执行 `export_object_to_pdf → navigate_to → get_hyperlink`，再对唯一 probe Page 执行 `create_page → rename_page → replace_page_body → append_page_content → delete_page_content_object → reorder_page → delete_page → close_notebook`。Sync 只报告 accepted-not-completed，Export 创建精确 run-scoped PDF，Navigate 只报告 UI action accepted；每个 effect 使用对应 Notebook Lifecycle、Local File IO 或 UI Control gate。内容对象删除只选择本次 Append 产生且可精确识别的 fresh object；默认删除均为非永久且公开 schema 没有 `permanently`。场景继续检查 convergence、单次 principal attempt、无 replay、Runtime kind/backend 和 content-free execution；handoff 不完整时 fail closed。

029 收尾的两项真实验证必须由用户在前台显式执行；Agent、pytest 与后台任务不得运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py onenote-convergence
.venv\Scripts\python.exe tests\manual_validation\run.py rename --use-cache
```

2026-08-13 用户前台真实运行 `run-2026-08-13-15-50-42` 通过：Create、Page update、Reorder、非永久 Delete 均返回 `attempts=2/stable_observations=2`，probe cleanup 后 `restored=true`；共享 lifecycle Close 同样连续稳定两次并关闭 Notebook。随后受影响回归 `run-2026-08-13-15-54-30`（Create）、`run-2026-08-13-15-56-46`（Reorder Page）、`run-2026-08-13-15-58-04`（Delete）、`run-2026-08-13-15-58-25`（六 case Page Copy）与 `run-2026-08-13-16-05-59`（两 case Page Move）全部通过。Copy 六 case 均 verified/lossless、最终 topology 连续稳定两次且 cleanup 后双侧恢复；Move 两 case 均只在 Copy verified/lossless 后非永久删除精确 source，未出现 partial，并关闭双 Notebook。

2026-08-15 用户运行 `run-2026-08-15-13-26-00` 时，Title 与 Append 已通过，但场景在调用内容删除前错误地把公开 PageContentObject 的 `id/can_delete/delete_target_id` 当成内部 parser 字段，因此误判 fresh deletable Outline 数量并 fail closed；failure finalizer 已精确关闭 Notebook。场景现只消费 `get_page_content_objects` 的公开 schema，纯测试使用与真实响应相同的 Outline/OE 形状。同期参数化 `run-2026-08-15-13-26-25` 已证明 SectionGroup Rename 正向、恢复和关闭成功；后续 canonical `rename` 在一条场景中固定覆盖 Section 与 SectionGroup 两个 case。

Working identity 冲突扫描在短时 open lock 内于打开 working bundle 前后各捕获一次当前 Notebook ID/实际目录 snapshot；全部历史 run-local lease 只与 snapshot 做内存比较，历史 run 数量不得放大 COM 调用次数。Snapshot 获取失败按 MCP/lifecycle failure fail closed，并保留本次 working 现场。

`search-all-open-notebooks` 是支持 fresh/cache、`included_in_all=true` 的双 Notebook Search 场景。它构建 Source 中的 Probe Group/两个 Section/三个 Page，以及第二个 `search-b` Notebook 中的两个 Page。由于该场景验证的是 `include_unindexed=false` 的 OneNote index，只有 fresh 模式会在全部 Page 写入后执行一次 `CloseNotebook(force=false)` checkpoint：精确关闭两个 role、从同一 working path reopen、按 typed relative address 重绑全部 live ID，并连续两次确认 hierarchy 稳定。cache working copy 使用普通 batch-open 和层级收敛，不增加 close/reopen。随后唯一一次完整 snapshot 同时完成内容真实性复核；cache hit 会从这次既有 Page XML 读取中把模板 probe 重建到进程内存，不额外读取 Page，也不把 raw probe 写入 fixture JSON evidence。预算门从公开失败 envelope 的 `error.code/error.message` 读取分类；只有无错误但尚未命中预算条件的结果会继续等待索引，任何非预期失败都会在第一次出现时立即停止，不再重复 20 次。只有实际查询出现稳定的额外命中时才输出 probe collision warning 并 fail closed。用户已确认此前 fresh 与 validated cache hit 真实运行通过；本次 envelope 修复仍等待用户定向复验。

Fixture bundle validation 由框架显式传入 role，逐 role 验证完整 manifest key set、typed container parent、Page Section/root level 和每 Page 单次内容 snapshot。每次 fresh run 在内存生成严格 32 字符的 `<15 位字母数字>-<16 位字母数字>` 探针，查询使用左右两段的 `AND`；root、Notebook、SectionGroup、Section 起点必须分别精确命中 `4 → 3 → 2 → 1` 个 Page。Readiness 必须连续两次得到相同四 ID 集合；分页以 `page_size=2` 验证两页并在前后检查 index 稳定性。静态 `max_pages=4` 的独立五 Page marker 必须在分页前失败，长正文 marker 必须触发 `max_total_chars=512`，同时 Probe Section 1 证明正常 snippet hydration。

场景的 MCP audit 会统一 hash/长度脱敏 `query/content/text/snippet`。普通 evidence 只保存探针 SHA-256、长度、字符类别、命中 ID 和无正文 metadata，不保存原始 query、正文或 snippet。Recipe 明确拒绝 `--use-cache`，以保证每次运行都生成新探针；它只有 fixture 所需 Writes，没有 Delete、Copy、Move、Permanent Delete 或 Raw XML。Agent 只能运行下面的 dry-run；真实命令必须由用户本人在前台显式启动：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py search-all-open-notebooks
.venv\Scripts\python.exe tests\manual_validation\run.py search-all-open-notebooks --use-cache
```

`query` 是支持 fresh/cache、`included_in_all=true` 的双 Notebook typed metadata Query 场景。两个 role 各自创建带共享 token 的嵌套 SectionGroup、Notebook/Group 直属 Section、根 Page 与缩进 Page；Notebook root、Outer、Inner 和 Parent Page 等受测 scope 都至少包含两个 fixture-owned 同类型对象，确保多项 Query 的 scope、过滤和分页行为均由真实集合验证。fixture 构建、fresh snapshot、cache convergence、打开 Notebook 基线枚举和正式断言都只调用四个 typed Query，不调用 List 或 Expand。Recipe v6 将场景、recipe/cache identity 和证据目录统一为简洁名称 `query`，并以 Query-only metadata snapshot 代替通用 Expand/Page XML snapshot；第二个直接缩进 child 继续明确放在第一个 child 之后，避免两个 reorder 竞争同一个 predecessor。cache cold build 在 `CloseNotebook(force=false)` 前还通过 lifecycle wrapper 对每个 exact Notebook 请求一次 `SyncHierarchy`，使已验证 live hierarchy 先写回源文件。物理 Group/Section 名称使用由完整 token 派生的 16 位紧凑 token；每个 role 在首个 fixture mutation前预算最深 `.one` 路径，超过 240 UTF-16 units 时明确 fail closed。根 scope 的 `notebook_count` 以 scenario 开始前的一次无过滤 `query_notebook` 逐页取尽结果为基线，因此用户原本打开的无关 Notebook 不会造成误报；关闭 `query-b` 后验证基线减一。cache token 只有在真实产生额外结果时才输出无查询内容的 collision warning。Query 不需要 index，因此 fresh 和 cache 都不会执行 Search 专用的 close/reopen checkpoint。先前场景名的真实证据仍保留；v6 使用新 fingerprint，旧模板不会再次命中。

Fixture bundle validation 由框架显式传入 role，逐 role 验证完整 manifest key set、对象类型、同父级双 Group/双 Section、Page Section/root level/双 indentation child 和完整 Query metadata snapshot，并在 bundle 层证明两个 role 共用同一个非空 run token。场景保存两份独立 fixture metadata evidence、逐 fixture item 的 expected 投影、每个请求/响应及对应 bridge operation；验证四个 root Query、Notebook/SectionGroup/Section 原生起点、Page `section_id/parent_page_id`、RFC 3339 严格时间、`page_size=2` 的全部页/末页/越界页，以及每次调用恰好产生规定的一个或两个 `GetHierarchy` 且不读取 Page 正文。随后由 lifecycle wrapper 精确关闭 `query-b` role，并证明 `include_recycle_bin=true` 也不能把已关闭 Notebook 或后代重新引入。场景只授予 fixture Writes、health 与四个 Query；List、Expand、Delete、Copy、Move、Permanent Delete 与 Raw XML 均关闭。真实运行只能由用户本人前台启动：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py query
.venv\Scripts\python.exe tests\manual_validation\run.py query --use-cache
```

`hierarchy-navigation` 是支持 fresh/cache、默认 `included_in_all=false` 的只读结构浏览场景；fixture 构建仍使用受限 Write。它在两个同时打开的 disposable Notebook role 上只验证 `list_notebooks`、四个 typed Expand 与 `expand_hierarchy`：包括四类 root、Page 缩进树、depth boundary 和零 Page 正文读取。fixture build、snapshot、cache convergence 和正式断言均不调用 Query。设计与完成条件由 [`TODO 033`](../../docs/todo/033_notebook_structure_list_and_expand_tools.md) 跟踪。真实运行只能由用户本人前台启动：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py hierarchy-navigation
.venv\Scripts\python.exe tests\manual_validation\run.py hierarchy-navigation --use-cache
```

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

<!-- dry-run-case: reparent-section.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section --dry-run --json
```

<!-- dry-run-case: reparent-page.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
```

<!-- dry-run-case: reparent-page-with-level.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page-with-level --dry-run --json
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

<!-- dry-run-case: onenote-convergence.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py onenote-convergence --dry-run --json
```

<!-- dry-run-case: search-all-open-notebooks.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py search-all-open-notebooks --dry-run --json
```

<!-- dry-run-case: query.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py query --dry-run --json
```

<!-- dry-run-case: hierarchy-navigation.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py hierarchy-navigation --dry-run --json
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

- 默认 `quiet`：输出每个场景的开始、PASS/FAIL、总进度；成功子场景的输出隐藏，失败诊断始终可见但受行数/字节上限约束。
- `normal`：额外展示每个成功子场景捕获到的紧凑进度和结果；检查全部 dry-run 计划时建议使用 `all --dry-run --verbosity normal`。
- `verbose`：在 `normal` 基础上输出子进程命令、逐次 mutation 细节、read 批汇总及有界 stderr。
- `--dry-run`、`--json`、所选 verbosity 和显式 `--timeout` 原样传给每个 scenario；`--json` 时聚合进度使用 JSON Lines，并允许完整 JSON payload。
- 真实执行首个 FAIL 后显示 `N not started` 并停止；关闭该失败 run 的精确 working Notebook 后可重跑，已验证的 cache template 仍可复用。Dry-run 始终遍历完整注册集合。
- 未指定 `--timeout` 时保留各 scenario 自己的默认值（普通场景 180 秒，Copy/Move 1800 秒）。
- `all` 没有自己的 `run-dir`，因此不支持 `--run-dir`；它也不会把多个场景放进同一目录。

### Cache working 单次打开与证据复用

以下 parent-aware 规则细化并取代上文对所有节点统一“absolute 优先”的概括。

Notebook 直属 child 仍先用 absolute working path 与空 relative ID；SectionGroup 下的嵌套 child 只用文件名与已经回读证明的精确 parent ID。最新失败证据显示，对嵌套 `.one` 先做 parentless absolute open 可能返回无关 parent，随后再次按 parent-relative 打开时 working group 路径会被 OneNote 改写；单一 parent-bound 路径避免这个双重 attachment，同时保留精确 parent 回读。

所有 materialized working copy 都只打开一次 exact working path。Lifecycle 以 parent-aware batch 激活受 manifest 约束的物理容器，证据保存在 `materialized-hierarchy-open[-<role>].json`；Fixture observer 随后执行 typed ID 重绑、连续两次 hierarchy 稳定观察和唯一一次 `scenario before` 内容读取。该 snapshot 既是 live Recipe 真实性复核，也是 scenario 的 before baseline，单次消费状态写入 `scenario-before-snapshot-handoff.json`。流程不会 close/reopen working Notebook、复制第二份 working bundle、修改或重建 cache template，也不会重放 mutation；任一步失败继续 fail closed，并按默认 failure lifecycle 精确关闭当前 Notebook。

## 参数与生命周期

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> `
  [--notebook-label <lowercase-kebab-label>] `
  [--run-dir <path>] `
  [--keep-notebook] `
  [--keep-worksite] `
  [--timeout <seconds>] `
  [--dry-run] `
  [--json] `
  [--verbosity quiet|normal|verbose]
```

具名 Scenario 默认 `normal`，三级输出均为立即 flush 的普通文本行，不使用 spinner、ANSI 覆盖或动态终端控制：

| 级别 | 实时进度 | 最终输出 |
| --- | --- | --- |
| `quiet` | run 开始，以及 Notebook、fixture、scenario、report、close/keep 等主要阶段和所有失败 | 一行 PASS/FAIL、总耗时、run/report 路径 |
| `normal`（默认） | `quiet`，加 cache 决策、fixture role、case/hop、mutation、restore/cleanup 的开始/完成与阶段耗时 | 紧凑状态、case 数、restored/worksite、lifecycle、MCP 进程和调用计数 |
| `verbose` | `normal`，加每次 mutation tool 名、attempt、耗时、convergence/reconciliation 标量；read 每 25 次汇总 | 加各阶段耗时、policy/allowlist 摘要和 content-free 调用统计 |

所有非 JSON 模式都只显示紧凑投影，完整事实以 `run-result.json`、`report.md` 和场景 artifact 为准。未知或复杂字段不会自动展开。任何 verbosity 都不得输出 tool arguments、OneNote ID、正文、XML、binary、query、完整响应或嵌套 JSON；失败会显示 failed phase、错误、artifact/evidence 路径，有界诊断之外的完整内容留在 artifact。

`--json` 优先于 verbosity：具名 Scenario 的 stdout 恰好是一个完整 JSON document，不混入进度文本；`all --json` 保持 JSON Lines。Dry-run 非 JSON 只显示步骤数量、cache/lifecycle、启用权限和 allowlist 数量；需要完整计划时显式使用 `--dry-run --json`。

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
- `rename` 另支持 `--new-name`；未提供时，固定 Section 与 SectionGroup case 分别使用基于原名称的临时名称。场景不暴露 fixture target selector。
- `reorder-page` 另支持 `--page-level <n>`。

## Isolated、单进程与最小权限边界

`scenarios/` 根目录中的每个可执行模块只提供一个具名 `Scenario` 子类；四个 Copy 入口分别位于 `copy_page.py`、`copy_section.py`、`copy_section_group.py` 和 `copy_notebook.py`，并共享基础设施 `copy_scenario_base.py`。根目录的 `base.py` 和 `__init__.py` 明确属于基础设施。类统一声明名称、help、默认 timeout、scenario 专属参数、fixture recipe、dry-run variants、manifest 参数准备、执行器和 `included_in_all`，并通过 registry wrapper 注册。`scenarios/__init__.py` 是公开场景导入顺序的唯一清单，`SCENARIO_REGISTRY` 则是 parser、dispatch、fixture metadata、dry-run catalog 和 `all` 的共同权威对象。

不代表单个 scenario 的依赖统一放在 `scenarios/common/`，包括 registry、闭环 orchestrator、静态 spec、fixture builders、fixture 编排、报告、Copy runtime 与 invariants。根目录因此不会混入名称看似 scenario、实际却只是共享函数的模块。

每个 scenario 都在本次独占的 disposable working Notebook 中运行，并最多启动一个 MCP 子进程。默认 fresh 路径创建新 Notebook；cache 路径只打开刚 materialize 的 working directory。Notebook 的 create/open/get/close 由窄 lifecycle wrapper 完成；wrapper 不提供 Section、Page 或内容写入能力。它立即写入 `lifecycle-lease.json`，绑定本次 run 的精确 Notebook ID、名称、本地 working path、template paths 和 `opened_template=false`。

唯一 MCP 子进程同时完成该 scenario 的最小 fixture、所选 mutation、before/after/restored 回读和契约内 restore/cleanup。它启动时使用 `scenarios/common/specs.py` 中固定的完整闭包 policy 和 tool allowlist，并在 fixture 创建前用 `health_check` 精确核对 policy、timeout 和 Copy budget；启动后不得扩权。Runner 不使用所有 scenario 的权限并集。Registry 会在导入注册时把每个 programmatic fixture 的 `creation_tools` 映射到必需 policy gate；例如 `create_section` 要求 Create，`create_page` 要求 Create + Writes，`add_page_image_from_file` 要求 Writes + Local File I/O，缺任一 gate 都会在创建 Notebook 或启动 MCP 前直接拒绝。Cache-only consumer 不会被误判为 fresh builder，也不会因此扩权。

| Scenario | Fixture 与权限限制 |
| --- | --- |
| `create` | 完整预设 fixture 加空 `Duplicate-Title-Target`；连续两次单项 `create_page` 验证同标题 fresh allocated/read-back IDs；默认先验证规范化重名 Section batch 在 mutation 前拒绝且 snapshot 不变，再以原 `create_section_group/create_section/create_page` 各提交两个 `items`，验证 batch allocated/read-back IDs，并由静态最小 allowlist 中对应的 `delete_page/delete_section/delete_section_group` 逐一非永久清理；不暴露 `create_notebook`，永久删除关闭；`--keep-worksite` 保留两个单项目标 Page |
| `onenote-convergence` | fresh-only 的两 anchor 最小 fixture；验证 `request_notebook_sync` accepted-not-completed、公开 Notebook Create+Close、精确 run-scoped `export_object_to_pdf`、typed `navigate_to` action accepted，再对生产 `create_page`、`rename_page`、`replace_page_body`、`append_page_content`、`delete_page_content_object`、`reorder_page`、可恢复 `delete_page` 和 source `close_notebook` 逐项取证；source Close 是最后一个 scenario MCP mutation，其完整双稳定单次执行证据由 lifecycle wrapper 精确封存为 pre-closed lease且不二次 Close；不进入 `all`，永久删除/Raw XML/Copy/Move 均关闭 |
| `rename` | canonical fixture 包含 Page、Section 与 SectionGroup target；单次运行以原 `rename_page/rename_section/rename_section_group` 的 `items` 模式执行 Rename/read-back，再逆序恢复，三种生产 Tool 均有独立 evidence。Page 正向证据以剔除 Title 后的 canonical body hash 严格保护正文，并保持拓扑和 content-object identity；恢复要求标题及完整 canonical Page 语义回到 before，避免把 OneNote 对同一标题 XML 的重序列化误报为内容损坏；没有 `--target`；`--keep-worksite` 保留新名称并记录精确恢复说明 |
| `reorder-page` | `Description/00-Reorder-Description` 明示操作前 `01,02,03`、正向操作后 `01,03,02`、恢复后 `01,02,03`；既有 `reorder_page` 取证后，默认再由 `sort_children` 对 leveled Page parent 的直属 Page blocks 按名称排序并验证块内缩进/内容不变；随后以冲突 `child_type=section` 验证 typed preflight 拒绝、零 mutation bridge call 与 unchanged snapshot；`--keep-worksite` 保留单项 Reorder 现场并跳过 Sort/拒绝探针 |
| `reorder-section` | **已注册到 `all`**。`00-Description/00-Reorder-Section-Description` 分别说明 Notebook 父级和 SectionGroup 父级的 before/after/restore；两组 Section 及其 Page 均使用 `01/02/03` 编号；既有 `reorder_section` 取证后默认分别调用 `sort_children`，验证两个父类型均推断 Section、保持 SectionGroup 槽位与 Page 内容；公开授权只开启 Writes。 |
| `reparent-section` | `00-Description/00-Reparent-Section-Description` 说明三种 before/after/restore：`01-Notebook-To-Group-Section` 从 Notebook 根换父级到 `01-Destination-Group`，`02-Group-To-Notebook-Section` 从 `02-Source-Group` 换父级到 Notebook 根，`03-Group-To-Group-Section` 从 `03-Source-Group` 换父级到 `03-Destination-Group`。每个 destination 预置两个可区分的直属 Section anchors；每次 Reparent 后刷新快照，验证 ID、父级、Page 拓扑、内容和独立位置证据，默认逆序恢复，`--keep-worksite` 保留三项目标父级。只允许同一 Notebook。 |
| `reparent-page` | **当前 typed 工具 / v3 fresh 与 cache 真实验证通过 / 已注册到 `all`**。通过 `reparent_page` 提交精确 ID、confirmation 与布尔 `include_subpages`，不要求 Raw XML。唯一的跨 Section case 将 `01-Source-Section/01-Reparent-Page` 改属预置两个根 Page anchors 的 `02-Destination-Section`；目标 Page 同页包含 Rich Text、Table、List、Tag、Image。Recipe v3 保留已验证的 cache identity；manual runner 在 OneNote GUI preflight 通过后直接使用首次 live identity，cache 路径完成 typed ID/evidence 重绑、双稳定和单次完整内容验证。生产工具冻结完整基线后只执行一次 Reparent mutation。默认使用新 ID 逻辑移回，或由 `--keep-worksite` 保留。 |
| `reparent-page-with-level` | **当前 typed 工具 / fresh 与 cache 真实验证通过 / 已注册到 `all`**。Recipe v2 只使用 OneNote Desktop 的合法 page level 1-3：同一个 disposable Notebook 准备两棵相互独立、选中根均为 level 2 的缩进树；`root-only-default` 使用 `include_subpages=false` 并验证一个 level-3 排除后代留源且提升为 level 2，`full-subtree` 使用 `include_subpages=true` 并验证两个 level-3 分支后代随完整子树迁移、保持相对层级并形成单射 `id_map`。两个 case 均从 after snapshot 独立计算并深比较仅属于 fresh 目标根的 `destination_position`；成功不使用 Reorder 权限恢复原缩进。用户前台运行 `run-2026-08-14-12-44-14`（fresh）、`run-2026-08-14-13-51-48`（validated cache hit）和 `run-2026-08-14-13-53-49`（fresh）均为 `passed`、`closed_preserved`，因此经用户批准纳入 `all`。 |
| `reparent-section-group` | **当前 typed 工具 / 用户确认迁移后真实验证通过 / 已注册到 `all`**。通过 `reparent_section_group` 提交精确 ID 与 confirmation，不要求 Raw XML。三组编号 Group/Section/Page 覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup；每个 destination 预置两个同类型 Group anchors，两个恢复目标 source 也各自保留两个固定 anchors，确保正向搬出目标后源容器仍非空；按 read-back 固定名称顺序核对独立位置证据，并要求目标及后代 ID、关系、Page 内容保持。默认按 `03→02→01` 逆序恢复，`--keep-worksite` 保留三组父级。 |
| `delete` | Delete-Sandbox 下准备两个独立叶子 Page、两个带子页的 Page 根、Section 与 SectionGroup target；以测试 Batch effective Page 上限 5 证明 Notebook 总 Page 数超限时，两叶子 Page 的小目标 batch 与混合 `include_subpages=false/true` batch 均可非永久 Delete：后者分别保护并提升排除子页、删除完整子树；同时证明含六 Page 的 Section scope 在 mutation 前拒绝且 snapshot 不变；各成功批次均核对 `final_hierarchy` 与回收站状态；永久删除关闭；`--keep-worksite` 保持 Notebook 打开并记录精确目标 |
| Page Copy | 双 Notebook `source`/`destination` bundle；同一 source Page 对同 Section、跨 Section、跨 Notebook 三种目标分别执行 `include_subpages=false` 与 `include_subpages=true`，合计六个 case。同 Section由既有源 Page 形成多项序列，跨 Section/Notebook 目标各预置同标题碰撞 anchor 与额外 position anchor；每个 case 只调用一次公开 `copy_page`，保存内部 planning 摘要并完成双侧回读，断言 fresh/disjoint target IDs、源端、anchors 和独立位置证据不变；默认清理六个根目标并验证两侧恢复，`--keep-worksite` 保留六个目标和两个 working Notebook |
| Section/Group Copy | 对应最小源和目标；每个 destination 预置两个同类型直属 anchors，源容器含严格富内容父页与三个混合 List/Tag 项的语义子页，并继续递归复制完整子树；每个 case 保存并深比较独立位置证据，默认执行可恢复清理，显式 `--keep-worksite` 在 after/mapping 验证后保留精确目标 ID |
| Notebook Copy | 最小 Notebook 同样包含严格父页和 List/Tag 语义子页；Copy 开启、Delete 关闭，默认关闭副本；显式 `--keep-worksite` 保持副本打开并记录路径 |
| Page Move | 固定双 Notebook `source`/`destination` bundle，destination 预置两个根 Page anchors，只覆盖跨 Notebook 两个 case：`include_subpages=false` 与 `include_subpages=true`。前者只复制/删除根 Page，并要求被排除子页在源 Section 中提升一级、ID 与内容不变；后者复制两页并按叶到根非永久删除两页。两者都从删除源后的最终 snapshot 深比较独立位置证据；`--keep-worksite` 保留目标 Page 和双 Notebook 供 UI 检查 |
| Section/SectionGroup Move | 两个独立、已进入 `all` 的双 Notebook 场景；destination Notebook 根预置两个同类型 anchors。容器完整递归复制到 destination，要求完整单射 `id_map` 与 verified/lossless Copy，然后只允许一次对应 typed 根删除且固定非永久。after snapshot 必须证明全部原源子树 ID inactive、全部目标 ID 仅位于 destination role，并深比较删除后的独立位置证据；不重复未验证 content type comparator |
| Report | 只读取本地 artifacts，不启动 MCP |
| Source lifecycle | wrapper 仅支持 fresh create、working-copy open、受控 SectionGroup/`.one` 加载、精确 get/close 与只读 open-state probe；不启动额外 MCP，也不打开 template |

永久 OneNote Delete 始终关闭。四个具名 Reparent 场景只启用公开 Writes 与 Organize；Raw XML 在全部 Reparent 场景中关闭，runner 不构造、不接收也不传递 hierarchy XML。

### 同 Notebook Reparent 能力

先查看无副作用计划：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page-with-level --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-section-group --dry-run --json
```

真实运行只能由用户本人分别显式启动。`reparent-page` 的 Description 明示编号 Page 从 `01-Source-Section` 到 `02-Destination-Section` 再恢复的状态和富内容门限；`reparent-page-with-level` 明示默认 root-only 与显式 full-subtree 的两条非恢复路线；`reparent-section-group` 明示三条容器路线。所有场景都把 COM 返回成功仅视为“请求已返回”，不视为能力成立。

十个 Reparent/Copy/Move 执行场景会在 mutation response 与 after snapshot 之外保存 `destination-position-evidence*.json`。expected evidence 由 manual-validation 自己的只读 projector 从 fresh target ID 和 after hierarchy 计算，不导入生产 position builder。Page 使用目标 Section 完整扁平 Page 序列，只核对目标根一份位置且拒绝 level/后代位置字段；Section/SectionGroup 使用同父级同类型直属 children；Notebook Copy 精确核对 `not_applicable`。字段不一致会非零退出并按场景规则保留现场。

Page typed 场景接受两种原生结果：目标 ID 保持，或全树中恰好发生 `旧 Page ID 消失 + 目标 Section 新增一个 Page ID` 的一对一替换。后者必须记录 `old→new`，且新 Page 的 Notebook、标题、page level、父子缩进、富内容语义摘要和内容对象语义必须与原 Page 一致；Rich Text、Table、List、Tag、Image 的 fixture 能力在 mutation 前已经过门限。富内容摘要忽略 Page/内容对象 ID，并把 TagDef/Tag index 解析为类型和符号后比较，其余格式、结构、文本和 Image Data 保持严格。所有无关对象仍要求 ID、关系、稳定内容 hash 和内容对象身份不变。默认恢复使用正向回读得到的新 ID；OneNote 再次重映射时记录第三个 ID，并按逻辑位置和相同富内容摘要验证恢复，不虚构原 ID 已恢复。场景没有 Copy/Delete 权限，不调用 `copy_page` 或 `DeleteHierarchy`，也不把回收站可见性作为验收条件。

SectionGroup typed 场景仍要求同一目标 ID、全树 ID 集合、全部后代和 Page 内容身份保持不变；每步回读，默认按第三、第二、第一条路线逆序恢复。`--keep-worksite` 只在全部正向验证通过时保留现场。请求被忽略、Page ID 转换不是精确一对一、富内容变化、无关对象变化或恢复失败都会非零退出并保留 Notebook 与证据。一次通过只证明当前 OneNote/Office 组合，不构成跨版本保证。

`reorder-section` 与 `reorder-page` 一样，不要求也不收集 OneNote 版本或 Office channel 参数。跨版本兼容性取证作为独立低优先级工作跟踪，见 [`docs/todo/007_cross_version_compatibility_evidence.md`](../../docs/todo/007_cross_version_compatibility_evidence.md)，不作为当前场景的运行前置条件。`reorder-section-group` 已从生产 Tool、Registry 和 `run.py` 公共场景目录移除；其 Service、fixture 与纯测试只保留后端固定名称升序的负能力证据。任何环境变量都不能恢复 MCP Tool 或公共 scenario；直接 Service 诊断仍同时要求 Writes 与默认关闭的非产品内部门，也不要求重复真实运行。

## Section 与 SectionGroup Move

`move-section` 与 `move-section-group` 只覆盖跨 Notebook 重建式 Move，不覆盖同 Notebook 父级变化；后者已经由 `reparent-section` / `reparent-section-group` 负责。两个场景都使用 source/destination 两个全新 disposable Notebook，目标父级固定为 destination Notebook 根，源端分别是“一 Section + 一 Page”和“一 SectionGroup + 一 Section + 一 Page”的最小树。

生产计划必须返回 `operation=move_section|move_section_group`、精确源子树 snapshot，以及不同的 source/destination Notebook IDs。执行只消费生产 Copy 的 `verified/lossless` 结论和完整单射 `id_map`，不承担附件、墨迹、形状或媒体 comparator。Copy 和源 digest 重校验通过后，Section/SectionGroup 路径都只能调用一次对应 typed 根删除，公共 Move tool 不接受 `permanently`，service 固定传入 `false`。after snapshot 要求计划中的根及全部后代 ID 从 source role 消失，全部新 ID 只出现在 destination role；目标复核或任何删除证据不完整都会非零退出并保留双 Notebook 现场。

2026-08-11 用户真实运行结果：`run-2026-08-11-20-31-28` 的 `move-section --use-cache` 与 `run-2026-08-11-20-33-29` 的 `move-section-group --use-cache` 均为 `status=passed/outcome=moved`。两个 Copy report 都是 `verified=true/lossless=true`、映射完整且无 skipped content；各自只尝试删除一个源根，全部计划源 ID 均 inactive，`remaining_source_ids=[]`，source/destination lease 最终均关闭。Section 运行取得 `source_deleted_to_recycle_bin=true`；SectionGroup 运行的 COM 不暴露回收站元数据，因此只记录 `not_required_com_unavailable`，不影响活动态缺席门。该证据只覆盖最小 Outline/RichText fixture 与当前环境。

## Page Move

`move-page` 的语义是对显式范围执行重建。`include_subpages=false` 只选择根 Page；`include_subpages=true` 选择完整缩进子树。场景固定使用两个 Notebook，并只覆盖 `cross-notebook-root-only` 与 `cross-notebook-subtree`：不再重复同 Notebook 跨 Section，因为该位置变化已经由 typed `reparent-page` 验证。

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
   ├─ internal-planning.json / copy-result.json
   ├─ copy-page/copy-section/copy-section-group: before/copy-result/after-<case>.json
   ├─ after.json / restored.json / worksite.json
   ├─ copy-notebook: close-confirmation.json（默认关闭副本时）
   └─ result.json 或 failure.json
```

唯一 MCP 的 content-free bridge audit 位于 `scenario-mcp/bridge-calls.jsonl`；只记录 operation、成功状态、时间和耗时，不记录参数、OneNote 内容或返回值。`fixture-result.json` 的 `validation` 段记录 profile topology/content invariants 的实际通过证据。

每个 Copy/Move case 只调用一次公开 mutation Tool；allowlist 不包含 Plan 或 Preview。生产调用在同一个 Runtime operation 内建立 live 内部计划，Runner 从 `copy_report.planning` 保存 `internal-planning.json`，只记录 operation、公开 `include_subpages` 对应的内部 `include_descendants` 布尔值、估算、内容能力和 lossless candidate，不保存 digest、token、正文或 raw XML。`copy-page` 的三个 root-only case 提交 `include_subpages=false` 并要求内部 planning 回显 `false`，三个 subtree case 提交 `include_subpages=true` 并要求内部 planning 回显 `true`；`copy-section` 与 `copy-section-group` 分别保存 same-notebook/cross-notebook 两组单次调用证据。Runner 在每次 mutation 前复核目标父级、目标 role 和范围，并从 source/destination 两侧最新 snapshot 合并下一 case 的 before evidence，从而把多次 mutation 的增量逐项隔离；它不重试任何 mutation。

每次 mutation 使用 case 前最新 snapshot 的容器 `modified` 作为 confirmation，而不是 fixture 刚写完时可能仍在被 COM 延迟更新的旧值。Runner 的 `before/after.page_hashes` 使用稳定内容 hash：忽略 Page 根级 hierarchy 字段、OneNote 在任意内容节点上延迟补写的时钟/作者/选择/视图元数据，以及空、无子节点且只携带 `selected/isSelected` 的 T 视图占位；普通空 T、非空文本、内容对象 ID、格式和二进制仍保留。`page_canonical_hashes` 忽略 Page/内容对象 ID，作为诊断摘要；Page reparent 的成功门限使用更精确的 `page_reparent_hashes`，额外把 Tag index 解析成类型/符号语义，同时保留 Rich Text、Table、List、Tag 状态、Image Data 和其他结构。原始 XML SHA-256 另记在 `page_xml_hashes`，只用于诊断 COM 重序列化，不作为内容变化成功门限；`page_objects` 仍独立记录内容对象投影。Page Copy 的逐 case 不变性门只绑定 manifest 中的 source Parent/Child 与跨 Section/Notebook anchors，并同时比较 topology、稳定内容 hash 和内容对象身份；Description 等非验收页的后台规范化不参与业务成功判断。默认 cleanup 恢复仍比较一致的 Runner 证据；任何受保护 Page 的真实稳定内容变化都会 fail closed。

每次通用 snapshot 在完成逐 Page 取证后都会再读取一次完整 hierarchy：最终 `items/modified` 来自这次末尾回读，并要求前后 ID 集合一致。这避免把 fixture 刚创建时的旧 `modified` 用作随后 mutation 的 confirmation，同时不会重试 mutation。

任一步业务流程失败后不再执行后续 mutation、read-back、restore 或成功报告，但会进入独立的 failure finalization。默认对本次 run 的每个 exact lifecycle lease 执行 `CloseNotebook(force=false)` 并确认稳定关闭；`copy-notebook` 若已创建额外目标，也会在原 scenario MCP 退出前按 result/plan 的 exact ID/path binding 关闭并写 `copy-target-failure-finalization.json`，绑定不足或 close 失败即判定隔离失败。根级 `failure-finalization.json` 与 `run-failure.json` 汇总逐 role 结果。只有显式 `--keep-notebook` 或 `--keep-worksite` 才保持打开。默认 close 不删除 working Notebook 目录、普通 artifact 或 evidence；close/证明失败仍非零并要求停止批处理。

成功的可恢复 action 默认仍完成 restore/cleanup，并用 `restored.json` 证明恢复。显式 `--keep-worksite` 才写入 `worksite.json`、保留动作后的精确状态和源 Notebook；该模式只在 scenario 自身的 read-back invariant 通过后报告成功。对 Copy，此成功还表示每页按其内容类型选择的 read-back tier 与 mapping invariant 均通过；UI 人工检查仍用于记录具体 OneNote 环境的真实证据。

`run-metrics.json` 记录 lifecycle create、唯一 scenario process、report、finalize 和总耗时，以及实际 MCP 启动数、MCP tool call 数和 scenario/lifecycle bridge call 数。真实性能对比只能由用户本人运行后据此确认；合同测试只验证结构和计数，不把 mock 耗时作为性能收益。

## 仅限纯合同测试

以下命令不访问 OneNote，可以由 Agent 或自动化运行：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
```

真实后端验收必须由用户本人先运行目标 scenario 的 `--dry-run`，再运行同一个扁平 scenario 命令。
