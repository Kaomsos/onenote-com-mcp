# 003：Scenario 独立 Fixture 与单 MCP 进程闭环

> ID：003
> 状态：已完成
> 优先级：P2
> 类型：验证基础设施 / 性能与权限架构
> 更新日期：2026-08-06

## 背景

改造前，每个 `tests/manual_validation/run.py <scenario>` 都会创建相同的完整预设 fixture，并按 create、mutation、close 阶段分别启动 MCP 子进程。该设计具有清晰的阶段级最小权限边界，但存在两类明显开销：

1. 简单场景也会创建与自身无关的 Section、Page、富文本、表格、图片和 disposable targets；
2. 每次 MCP 子进程启动都需要重新执行 Python import、MCP initialize、tool discovery、`health_check` 和 OneNote COM 初始化。

本 TODO 记录评审后的折中架构。代码迁移、纯合同测试、文档同步、低风险/性能实测以及修复后的严格 Move 安全门验证均已完成。

## 真实验收进展

用户于 2026-08-06 完成了下列真实运行：

1. `run-20260806T060013Z`：`rename` 通过，fixture validation 通过，restore 通过，只启动 1 个 MCP，源 Notebook 按精确 lease 关闭且本地目录保留；
2. `run-20260806T060140Z`：`create` 通过，完整 fixture validation 通过，只启动 1 个 MCP，总耗时 37.697279 秒；
3. `run-20260806T060239Z` 与 `run-20260806T060301Z`：严格 Move 均在 Copy 写入阶段安全失败，源 Notebook 保持打开，`created_ids/id_map` 和人工清理状态被保留。

严格场景的首轮真实证据定位出 `_created_item` 错误优先选择父 Section 的问题：后续 `UpdatePageContent` 因此错误命中 Section ID 并返回 HRESULT `0x80042005`。实现已改为优先选择新 Page，同时保证部分创建统一返回 `copy_unverified`，由 Move 归一化为 `copy_only`。

用户随后完成修复后复跑 `run-20260806T061225Z`：fixture validation 通过；只启动 1 个 MCP；Copy 创建了真实新 Page ID 并生成 old→new `id_map`；因 `Outline` 尚未通过真实保真 allowlist，结果按设计返回 `outcome=copy_only`、`source_deleted=false` 和非零退出；源 Notebook lease 保持 `active`，finalization 未启动，本地 `.one` 文件与全部证据保留。该结果证明严格门禁和失败保留语义，而不把未验证内容错误视为成功 Move。

## 目标架构

每个公开 scenario 继续保持一次命令完成完整隔离闭环，但改为：

```text
wrapper 直接创建全新源 Notebook
→ 启动该 scenario 唯一的 MCP 子进程
→ 在该进程内创建 scenario 最小 fixture
→ mutation / 回读 / restore 或 cleanup
→ 退出 MCP 子进程并生成报告
→ wrapper 精确关闭源 Notebook，或按 --keep-notebook 保持打开
```

其中必须区分：

- **Notebook lifecycle create/close**：由窄接口 wrapper 直接执行，只负责源 Notebook 生命周期；
- **Scenario fixture create**：仍通过该 scenario 的 MCP 进程执行，受静态 policy、tool allowlist 和 `health_check` 约束。

不得让 wrapper 直接创建任意 Section、Page 或内容 fixture，以免这些 mutation 绕过 MCP 权限与证据边界。

## Scenario 独立 Fixture

当前为每个 scenario 定义固定、可审查的最小 fixture profile：

| Scenario | 最小 fixture 范围 |
| --- | --- |
| `create` | 当前完整预设结构；不执行额外 mutation |
| `rename` | 一个可重命名 Group 或 Section |
| `reorder-page` | Description 说明分区，以及带 `01/02/03` 固定编号的 Parent/Child/Sibling Page 树 |
| `reparent-section` | Description 说明分区；三个带编号 Page 的目标 Section，分别覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup，并支持逆序恢复或保留现场 |
| `reparent-page` | Description 说明页；编号源/目标 Section、目标 Page 和无关锚点；typed `reparent_page`，默认恢复或保留现场 |
| `reparent-section-group` | Description 说明页；三组编号 Group/Section/Page，覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup；typed `reparent_section_group`，默认逆序恢复或保留现场 |
| `delete` | Delete-Sandbox 和一个 manifest-allowlisted disposable target |
| `copy-page` | 富内容源 Page 和目标 Section |
| `copy-section` | 源 Section 和目标 Group |
| `copy-section-group` | 一个源 Group 和 Notebook 根目标 |
| `copy-notebook` | 最小可复制 Notebook 和 allowlisted 本地 Copy root |
| `move-page` | disposable 源 Page 和目标 Section |

Fixture profile 必须声明预期结构、内容能力、manifest keys、创建工具和验证条件，禁止运行时自由扩权或猜测缺失目标。

## Scenario 级静态 Policy

每个 scenario 维护一份覆盖完整闭环的静态 policy 和 tool allowlist，包括：

- 最小 fixture 创建；
- 当前 scenario mutation；
- 必要的 before/after/restored 读取；
- 该 scenario 契约允许的 restore 或 cleanup。

MCP 进程启动后 policy 不得动态扩大；`health_check` 必须精确返回预期 policy、timeout 和 Copy budget。该模型是“单 scenario 闭环最小权限”，不是所有 scenario 的全局权限并集。

该权限模型已经取代 fixture、mutation、close 分阶段 MCP 进程模型，并已同步更新根目录 `AGENTS.md`、`tests/manual_validation/AGENTS.md`、README 和合同测试。真实运行前仍必须由用户审查 dry-run；不得用 mock 耗时制造性能优化结果。

## Notebook Lifecycle Wrapper

新增的 wrapper 能力必须是窄接口，不得成为通用 COM mutation 入口。建议只暴露：

```text
create_fresh_notebook
get_exact_notebook
close_exact_notebook
```

创建后写入 lifecycle lease，至少记录：

- run ID；
- 精确 Notebook ID；
- 预期名称；
- 预期本地路径；
- 创建时间和创建结果；
- 当前 lifecycle 状态。

最终 close 必须同时满足：

1. scenario 和报告全部成功；
2. 未指定 `--keep-notebook`；
3. 当前 Notebook ID 与 lease 完全一致；
4. 名称和本地路径仍与本次 run 一致；
5. 使用最新确认字段执行关闭；
6. 关闭对象是源 Notebook，而不是 Copy 副本。

任一条件不满足时不得关闭，必须非零退出并保留 Notebook、lease 和全部本地证据。任何路径都不得删除本地 Notebook 文件或目录。

## 实施步骤

1. 为现有 create、mutation、snapshot 和 close 阶段增加纯本地耗时统计，获得改造前基线；
2. 定义 `ScenarioSpec`、fixture profile、静态 policy 和 tool allowlist 数据模型；
3. 将 fixture helper 改为接收现有 MCP client，不自行启动子进程；
4. 实现窄接口 Notebook lifecycle wrapper 和 lifecycle lease；
5. 逐个迁移 scenario，优先从 `rename`、现名为 `reorder-page` 的 Page Reorder 等低风险场景开始；
6. 为精确 ID/path/name 绑定、失败不关闭、Copy 副本隔离和权限不扩张增加合同测试；
7. 比较迁移前后的 MCP 启动次数、COM 调用数和总耗时；
8. 用户本人完成真实 OneNote 隔离验证后，才可声明新架构已验证。

## 当前实现与待验收证据

- `scenarios/common/specs.py` 固定声明十个公开 scenario 的 fixture profile、完整 policy 和 tool allowlist；
- `scenarios/common/fixtures.py` 使用外部传入的唯一 MCP client 创建场景最小 fixture；
- fixture 完成后会对 active IDs、父子关系、Page tree、源/目标隔离和富内容能力执行 profile invariant，并把通过项写入 `fixture-result.json`；
- `lifecycle.py` 只公开 `create_fresh_notebook`、`get_exact_notebook`、`close_exact_notebook`，并写入精确 lease；
- `run-metrics.json` 记录 phase elapsed seconds、旧架构预期启动数、本次实际 MCP 启动数、MCP tool calls 和 content-free bridge call counts；失败时也会保留截至唯一 scenario process 退出时的计数与耗时；
- 合同测试只证明编排、权限和证据合同，不证明真实 OneNote 性能。仓库本地保留了一次用户在旧架构下完成的 `create` + default close 运行，可作为同机历史基线；新架构 after 数据仍必须由用户本人运行生成。

| 样本 | 架构 | MCP starts | Bridge calls | 总耗时 | 证据 |
| --- | --- | ---: | ---: | ---: | --- |
| before：`create` + default close，2026-08-06 | fixture MCP + lifecycle close MCP | 2 | 旧版本未做 bridge 审计 | 38.102935 s | `.local-validation/run-20260806T031731Z/run-state.json` 与两个 `calls.jsonl` |
| after：`create` + default close，2026-08-06 | scenario-scoped single MCP + lifecycle wrapper | 1 | 108（scenario 102 + lifecycle 6） | 37.697279 s | `.local-validation/run-20260806T060140Z/run-metrics.json` 与两个 content-free bridge audit |

同机单样本结果：MCP starts 从 2 降到 1（减少 50%），总耗时从 38.102935 秒降到 37.697279 秒（减少 0.405656 秒，约 1.06%）。样本证明进程启动数目标和本次运行的正向收益，但不把单次测量外推为普遍性能保证。

用户验收命令（必须先审查 dry-run，且只能由用户本人执行）：

```powershell
# 低风险场景
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py rename --json

# 与历史 create 基线进行同机 after 对比
.venv\Scripts\python.exe tests\manual_validation\run.py create --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py create --json

# 严格 Copy/Move 场景；允许安全门导致非零退出并保留现场
.venv\Scripts\python.exe tests\manual_validation\run.py move-page --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py move-page --json
```

全部验收证据已经保存：`rename`、`create` 与修复后的 `move-page` 分别证明低风险闭环、性能/进程收益和严格 `copy_only` 安全门。

## 风险与决策门

- Wrapper 直接操作 Notebook lifecycle 会扩大可信代码边界，必须避免提供通用 COM 操作能力；
- Scenario policy 会同时包含 fixture 与 mutation 权限，比当前 mutation-only 子进程更宽；
- OneNote 外部状态可能在 create、MCP 执行和 close 之间变化，必须依靠 lease 与最新回读拒绝不确定关闭；
- 合并 MCP 进程只能减少启动开销，不会自动减少 snapshot 和 OneNote COM 调用；若基线表明主要耗时仍来自快照，应优先优化读取范围；
- 任何为了性能跳过 before/after/restored 证据、严格保真门或失败保留现场的方案均不可接受。

## 非目标

- 不创建常驻 MCP daemon；
- 不在多个 scenario 之间复用 MCP 进程或 Notebook；
- 不引入所有 mutation 权限的全局 policy；
- 不允许 Agent、CI、hook、timer、watcher 或后台任务运行真实 scenario；
- 不实现自动删除本地 Notebook 文件；
- 不把 `inspect`、`read`、`report` 或 lifecycle wrapper 暴露为新的公开 CLI action。

## 完成定义

- [x] 每个 scenario 使用独立最小 fixture profile；
- [x] 每次 scenario 最多启动一个 MCP 子进程；
- [x] 每个 scenario 拥有固定、可测试的静态 policy 和 tool allowlist；
- [x] Wrapper 只提供受约束的 Notebook create/get/close；
- [x] Lifecycle lease 和精确 ID/name/path 关闭合同测试通过；
- [x] 失败、`copy_only`、restore 失败或 close 绑定失败时源 Notebook 保持打开；
- [x] 默认 close 与 `--keep-notebook` 均不删除本地文件；
- [x] dry-run 展示 fixture profile、完整 policy、allowlist 和 lifecycle；
- [x] 全部纯合同测试、完整 pytest 和 `git diff --check` 通过（2026-08-06：158 passed）；
- [x] 文档和 Agent 禁令同步更新；
- [x] 用户本人完成至少一个低风险 scenario 和一个严格 Copy/Move scenario 的真实验证；
- [x] 记录改造前后耗时和 MCP 启动次数，证明本次同机运行的优化收益。
