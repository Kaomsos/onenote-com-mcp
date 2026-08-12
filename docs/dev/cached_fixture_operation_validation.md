# 缓存 Fixture 驱动的真实操作验证推荐实践

> 级别：推荐实践（Recommended Practice）
> 适用范围：需要在真实 OneNote COM 后端上反复验证、且 fixture 构建成本较高或只能由用户在 UI 中创建的操作
> 当前强制安全契约以 [`tests/manual_validation/AGENTS.md`](../../tests/manual_validation/AGENTS.md) 和 [Human-gated Manual Validation Runner](../../tests/manual_validation/README.md) 为准。

本文推荐把一项真实后端验证拆成以下证据链，而不是把 fixture 创建、业务操作和人工观察揉成一次不可复现的手工试验：

```text
已验证并缓存的 fixture
→ 在全新 working copy 上执行待验证操作
→ 自动比较 before / plan / after
→ 用户对精确目标给出 run-bound verdict
→ 根据机器结论与人工结论共同形成证据
```

“待验证操作”不限于 Copy。Create、Rename、Reorder、Reparent、Move、非永久 Delete、格式转换、富内容往返以及新的 typed mutation 都可以采用这套实践，但必须各自保留独立的 policy、tool allowlist、confirmation、比较器和失败语义。缓存复用 fixture，不复用已经发生 mutation 的 working copy，也不提供跨场景扩权。

面向实际操作者的步骤见[缓存 Fixture 驱动的操作验收指南](../../tests/manual_validation/cached_fixture_operation_validation.md)。

## 为什么推荐这条链路

它主要解决开发阶段的四类问题：

1. **缩短重现周期**：复杂结构、墨迹、形状、媒体等 fixture 经验证并发布后，后续调试直接 materialize 新 working bundle，不必每轮重新创作。
2. **隔离变量**：fixture 合同、待验证操作、机器 comparator 和 UI 观察分别落证，失败能定位到 materialization、operation、comparison 或 verdict，而不是只得到“看起来不对”。
3. **保留自动化门限**：人工 `ACCEPT` 只能补充 UI/播放/视觉证据，不能覆盖 ID、拓扑、内容投影、binary hash 或 source-deletion gate 的机器失败。
4. **支持多种操作**：同一个稳定 fixture 可以服务不同的固定 consumer Scenario，但每个 consumer 仍拥有独立的操作合同和最小权限，不把 Copy 的结论外推到 Move、Delete 或其他 mutation。

## 四个阶段的开发合同

### 1. 缓存 Fixture：只复用输入状态

优先为每个 Scenario 定义显式 `RecipeBase`，以 recipe version、角色集合、fixture 参数、manifest keys 和 validation conditions 形成稳定 fingerprint。

- Programmatic recipe 的 cold path 必须先在 fresh disposable bundle 上完成 live validation，精确关闭后才能 opaque-copy 并发布 immutable template。
- Interactive/UserAuthored recipe 必须由固定、human-gated bootstrap Scenario 创建。用户编辑后先运行 detector 和边界 validator，再决定发布 `ready` 或仅保留 `evidence_only`。
- Cache hit 必须 materialize 到本次 run 独有的 working paths，打开完整 hierarchy，记录 old→live ID 映射，并重新执行 live Recipe validation。
- Cache template 永远不能由 OneNote 打开；运行中的 mutation 只能触及 working copy。
- Recipe 内容、detector、结构门或 comparator 的输入合同变化时提升 recipe version，使旧 entry 不会命中新合同。

缓存命中不是“fixture 仍然正确”的替代证据。只有本次 working copy 的 live validation 通过，才能进入待验证操作。

### 2. 待验证操作：固定输入、固定权限、单次执行

为操作定义独立的具名 Scenario；不要提供动态 operation 名称、外部 Notebook path/ID 或运行时扩权参数。

推荐执行顺序：

1. 从 live manifest 取得精确 typed IDs；
2. 捕获 operation-specific before snapshot；
3. 对支持 plan 的操作取得稳定 plan，并把 confirmation 与 snapshot 绑定；
4. 在静态最小 policy/tool allowlist 下执行一次 mutation；
5. mutation 不自动重试；响应不确定或部分失败立即停止并保留现场；
6. 捕获 after snapshot、响应中的 allocated/resolved IDs 和所有部分失败字段。

不同操作必须有自己的成功条件。例如：

- Reorder/Reparent 要证明对象身份、目标父级/顺序和未涉及对象不变；
- Copy 要证明 fresh target mapping、内容等价、source/anchors 不变；
- Move 要先通过完整 Copy/placement comparator，随后才允许非永久删除源，并证明源从活动树消失；
- Delete 要证明精确目标不再活动或明确进入回收站，且 `permanently=false`；
- 内容或格式转换要定义允许的 COM 规范化，不得把未知差异降为 warning。

### 3. 自动比较：先于人工 verdict，且 fail closed

Comparator 应从操作风险反推，而不是从现有响应里挑容易通过的字段。通常至少分为：

- **身份**：source、target、before ID 集是否满足 fresh/disjoint/一对一要求；
- **拓扑**：role、parent、Section、order、level、后代范围是否正确；
- **内容**：canonical 或有真实证据支撑的 semantic projection、visible text、对象签名和 binary hash；
- **操作语义**：source 是否应保持、移动后是否应消失、被排除后代是否仍活动、cleanup/restore 是否完成；
- **不变对象**：anchors、其他 roles、先前 target 和 cache template inventory 是否未被改写。

允许 semantic comparator 时，应把每个忽略字段、容差和投影来源写成代码中的静态合同，并用真实 COM 观察和纯自动化 regression 支撑。未知节点、缺字段、非数字、额外能力或越界差异应 fail closed。

机器 evidence 应在提示人工 verdict 前原子落盘。即使用户随后拒绝、终端 EOF 或进程异常，开发者仍能区分“操作/比较失败”和“尚未取得人工判断”。

### 4. 人工 verdict：补充 UI 证据，不覆盖机器门限

只有自动比较达到该场景声明的可人工评审状态，才提示用户检查精确 working Notebook、source 和 target。确认短语必须绑定 run ID、能力或操作，例如：

```text
ACCEPT <run-id> <capability-or-operation>
REJECT <run-id> <capability-or-operation>
```

推荐把 verdict 写入独立的 `human-acceptance.json`，至少包含：

- run ID、Scenario、operation/capability；
- verdict：`accepted` 或 `rejected`；
- 用户实际检查的 source/target IDs 或 manifest keys；
- 机器 comparator 的状态与 evidence 路径；
- OneNote/Office、Windows 和时区环境；
- 对视觉、播放、交互、顺序或删除结果的固定检查项；
- 时间与后续人工清理说明。

最终通过应采用合取关系：

```text
fixture live-valid
AND operation completed as declared
AND automatic comparator passed
AND human verdict accepted（当该场景要求人工判断时）
```

任何一项失败都不能发布“已验证”结论、修改静态 allowlist 或为 Move/Delete 放权。人工拒绝是有效负向证据，应保留，不应通过重问来覆盖。

## 推荐的证据布局

文件名可以按具体场景细化，但职责应保持清晰：

```text
<run-dir>/
├─ manifest.json / prepared.json
├─ cache-materialization.json
├─ cache-structure-remap.json
├─ fixture-result.json
├─ before[-<case>].json
├─ plan-attempts[-<case>].json
├─ plan[-<case>].json
├─ operation-result[-<case>].json
├─ machine-comparison[-<case>].json
├─ human-acceptance.json
├─ worksite.json 或 restored.json
├─ run-result.json 或 run-failure.json
└─ report.md
```

这里的 `operation-result` 是职责名称，不要求已有场景立即重命名其 `copy-result`、`move-result` 等稳定 artifact。新场景应优先保持“输入、执行、机器比较、人工结论”可单独审计。

默认 evidence 应 content-free：保存 typed IDs、计数、hash、投影和布尔检查，不保存正文或二进制。只有具名场景通过显式参数授权敏感证据时，才可在本次本地 run 目录保存经过约定 redaction 的内容；不得进入 cache template 或版本库。

## 失败归因与处置

| 停止点 | 结论 | 处置 |
| --- | --- | --- |
| Cache miss，且 recipe 只能人工创建 | `interactive_bootstrap_required` | 提示固定 bootstrap Scenario；不得自动创作或猜测实例。 |
| Materialization/open/ID rebind 失败 | fixture 尚不可用于操作 | mutation 前停止，保留 working bundle 和 lease；不得写回 template。 |
| Live Recipe validation 失败 | cached fixture 不满足当前合同 | exact entry quarantine/invalid；保留失败现场，不以人工观察放行。 |
| Operation partial/uncertain failure | 操作结果未知或不完整 | 不重试 mutation；保存 created/allocated/resolved IDs 和人工接管说明。 |
| Machine comparator 失败 | 自动合同未通过 | 不把用户 `ACCEPT` 作为成功；Move 不进入源删除，allowlist 不变。 |
| 用户 `REJECT`、EOF 或超时 | 未取得正向 UI 证据 | 保留机器 evidence 和现场，记录 rejected/incomplete。 |
| Restore/cleanup/close 失败 | 验收闭环未完成 | 顶层非零失败，保持现场；不得删除 working Notebook 或普通 artifacts。 |

## 如何接入一个新操作

开发时推荐按以下顺序提交：

1. 定义 Recipe/fixture invariant，并决定 programmatic build 还是 human bootstrap；
2. 定义固定 consumer Scenario、静态 policy、tool allowlist、budget 和 `included_in_all=False` 默认值；
3. 定义 before/plan/after 与 operation response evidence；
4. 先实现自动 comparator 和负向合同，再接 mutation；
5. 若真实 UI 行为无法由 COM 完整证明，增加后置、run-bound 的 ACCEPT/REJECT verdict；
6. 覆盖 cache miss/hit、ID remap、未知内容、partial failure、人工拒绝、EOF/timeout、keep-worksite 和 lifecycle failure；
7. 运行 manual-validation 纯测试、相关完整 pytest 和带 `--dry-run --json` 的 CLI；
8. 把真实命令交给用户执行。Agent、pytest、CI 或后台任务不得启动真实 Scenario；
9. 用户确认真实 evidence 后，才评审 comparator 容差、静态 allowlist、Move/Delete 门或 `included_in_all` 资格。

## 不推荐的做法

- 从前一次 mutation 后的 working Notebook 制作或刷新 cache；
- 把 cache hit 当作本次 live fixture validation；
- 用人工 ACCEPT 覆盖机器比较失败或未知 schema；
- 为了复用 fixture，把 Copy、Move 和 Delete 合并到同一个宽权限 Scenario；
- 通过名称、路径或页面标题选择 mutation 目标；
- mutation 失败后自动重试或自动清理现场；
- 根据单次真实 evidence 在运行时动态修改生产 allowlist；
- 把 UI 看起来相似当成二进制、拓扑或源删除已经证明；
- 将 cache template、working copy、失败现场或用户 Notebook 纳入通用文件清理。

## 与当前文档的关系

- 隔离 lifecycle、权限和具名 Scenario 总流程：[`isolated_mutation_validation.md`](isolated_mutation_validation.md)
- 当前公开 tool 与安全合同：[`../design/tool_contracts.md`](../design/tool_contracts.md)
- Fixture cache 和 Interactive/UserAuthored 当前操作入口：[`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md)
- 操作者版步骤与 verdict 检查表：[`../../tests/manual_validation/cached_fixture_operation_validation.md`](../../tests/manual_validation/cached_fixture_operation_validation.md)
