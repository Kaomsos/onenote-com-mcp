# 038：Batch Mutation 目标范围预检预算与大型 Notebook 误拒绝修复

> ID：038
> 状态：已完成
> 优先级：P1
> 类型：Bug / Batch Mutation / Budget / User Testing
> 更新日期：2026-08-18

## 当前结论

2026-08-18 的真实使用发现：对同一 Section 中 10 个无父子重叠的叶子 Page 调用 `delete_page(items=[...])`，请求在 mutation 前以 `Batch preflight exceeds the configured Page budget.` 拒绝。响应明确为 `mutation_attempted=false`、`attempts=0`、`backend_calls=1`，因此只发生一次 content-free hierarchy 快照读取，没有 Page 被删除。同批中的一个目标随后通过单项 `delete_page` 成功，进一步把问题定位到 batch 预检而非目标 confirmation、Delete policy 或 OneNote 删除能力。

本文不保存用户提供的真实 Page ID、标题、Section ID 或 Notebook 名称。用户已确认目标数为 10、均为叶子 Page、没有父子范围重叠；所在 Notebook 的 Page 总数超过默认 Copy Page 预算 200。

这是生产 bug，不是把 batch 上限从 20 调小即可解决的问题。修复前共享 `_preflight_batch_targets` 在确认 batch 目标属于同一 Notebook 后，计算整本 Notebook 的 active resource/Page 总数，并与 `CopyBudget.current().max_resources/max_pages` 比较。结果是只操作少量精确目标的 Batch Rename、Reparent 或 Delete，会因为同一本 Notebook 中大量无关 Page 而被拒绝；使用的环境变量还是 Copy 专用的 `LOCAL_ONENOTE_MAX_COPY_RESOURCES` / `LOCAL_ONENOTE_MAX_COPY_PAGES`。

`batch_create` 修复前也直接以“整本 Notebook 现有数量 + 新建数量”对比同一 CopyBudget，因此已与其余 9 个 Rename/Reparent/Delete 路径一起审计，未只删除观察到的 Delete 错误分支而保留同源误拒绝。

2026-08-18，用户在 fresh disposable Notebook 中完成 `run-2026-08-18-11-36-29`：Notebook 总 Page 数高于测试用 effective Page 上限 5 时，两个无重叠叶子 Page 的 Batch Delete 全部成功；含 6 Page 的真实 Section scope 则在 `mutation_attempted=false`、`attempts=0`、`backend_calls=1`、`replayed=false` 且 bridge audit 证明零 mutation call 的条件下拒绝。相同 run 的 mixed `include_subpages=false/true` Page batch 也验证了排除子页提升保护与完整子树删除。实现、自动化、dry-run、UT-010 集成和用户真实证据均已闭合，本 TODO 转为已完成。

## 根因位置与影响面

- `MutationService._batch_snapshot()` 通过一次 `GetHierarchy(root, pages)` 取得全部已打开 Notebook 的 content-free hierarchy；该一次读取解释了原失败响应中的 `backend_calls=1`，修复后 catalog 读取与 effective-scope 计费已分离。
- 修复前 `_preflight_batch_targets()` 由 Page/Section/SectionGroup 的 Batch Reparent、Delete 和 Rename 共用；它在精确目标、confirmation 和同 Notebook 检查后，把整本 Notebook 规模错误地当作当前 mutation 的有效工作范围。
- 修复前 `batch_create()` 使用独立代码，但也把整本 Notebook 的既有资源规模计入 CopyBudget，因此具有相同的预算职责混淆。
- 单项 Rename/Reparent/Delete 不经过这条 batch 整本预算检查，所以相同目标可以在单项路径成功。
- [TODO 037 / UT-010](037_user_testing_experience_feedback_and_optimization.md) 将为 Page Reparent/Delete 引入 `include_subpages` 和 batch-wide 后代提升规划；038 的预算修复必须按 UT-010 计算出的 effective scope 计费，不能简单改成只数 `items`。

### 当前完整 batch-capable inventory

038 必须覆盖当前所有公开 `items[1..20]` 模式，不能只修复用户实际命中的 `delete_page`：

| Family | Tool |
| --- | --- |
| Create | `create_section_group`、`create_section`、`create_page` |
| Rename | `rename_section_group`、`rename_section`、`rename_page` |
| Reparent | `reparent_section_group`、`reparent_section`、`reparent_page` |
| Delete | `delete_section_group`、`delete_section`、`delete_page` |

`sort_children` 虽然一次处理完整直接子序列，但不接受 `items`，不属于上述 batch 模式；它已有独立的完整子序列、Page block 和预算合同。Copy、Move 与 Reorder 当前也没有 batch 模式。若实施期间 Registry/schema 出现新的 `items`-capable 公开工具，它必须自动进入同一预算审计，不得依赖手工维护这张表后继续遗漏。

## 修复目标

### 1. 预算职责解耦

- Batch Mutation 不再读取或解释 `LOCAL_ONENOTE_MAX_COPY_*`，CopyBudget 只约束 Copy/Move 的规划、正文、对象和执行成本。
- 为 batch 预检建立明确的 mutation-specific 有界合同。可以采用独立的 `BatchMutationBudget`，或由已有的固定 `items[1..20]`、层级范围与操作特定上限组合实现；最终形态必须进入公开配置/health 文档和自动化合同，不能留下隐式常量或继续借用 Copy 名称。
- 区分“为了 exact-ID 定位而读取的 content-free catalog”与“当前 mutation 实际需要验证、保护或改变的 effective scope”。读取到无关 Page 不等于这些 Page 应消耗 mutation target budget。
- 即使取消整本 Notebook 的 Copy Page 门，也必须保持有界内存、层级解析、祖先链、子树、兄弟集合、destination 和收敛验证；不得以完全移除预算换取通过。

### 2. 按操作计算 effective scope

- Batch Rename：预算只覆盖精确目标、confirmation、同父级名称冲突所需的有界直接兄弟证据，以及必要的 ancestor/Notebook ownership 证明；同 Notebook 的无关 Page 不计入目标 Page 上限。
- Batch Delete：预算覆盖精确目标、回收站/ownership/overlap 证明，以及 `include_subpages=true` 选择的完整缩进子树或容器完整后代；`include_subpages=false` 还覆盖 UT-010 要求保护和提升的排除后代。无关 Section 的 Page 不得导致拒绝。
- Batch Reparent：除目标 effective scope 外，还覆盖 destination、循环/overlap 证明、UT-010 的 batch-wide excluded-descendant 提升计划和受影响 Section 的有界拓扑；不把目标 Notebook 中完全无关的 Page 作为 Copy 页数计费。
- Batch Create：预算覆盖请求新建项、输入正文字符、confirmed parent、名称冲突所需直接兄弟和创建后的对账，不因 Notebook 已经超过 Copy Page/Resource 默认值而永久禁止再进行至多 20 项的合法创建。是否需要独立的总容量门必须使用明确的 Create/Batch 名称和契约，不能复用 CopyBudget。
- Page、Section、SectionGroup 三种 Batch Rename/Reparent/Delete 都必须回归；修复不能只对 `delete_page` 写特例。
- Create、Rename、Reparent、Delete 四个 family 共 12 个当前 batch-capable 工具必须全部证明不再以无关 Notebook 总规模消耗 CopyBudget；同一 family 的共享实现可以复用合同，但每个公开工具名都必须有 Registry/schema 到 handler 的覆盖证据。

### 3. 失败与响应语义

- 输入项超过 20、目标 effective scope 超出新的明确预算、confirmation 改变、跨 Notebook、范围重叠或 scope 无法证明时，仍在 principal mutation 前 fail closed。
- Create、Rename、Reparent、Delete 在全部逐项调用返回成功后，必须各执行一次整批 content-free hierarchy 回读；分别核对新 identity、最终名称、最终父级或 inactive/recycle 状态。整批回读失败按 `batch_final_hierarchy` partial failure 处理，不允许自动 replay。
- 预检拒绝继续报告 `mutation_stage=preflight`、`mutation_attempted=false`、`attempts=0`；只读 hierarchy 调用必须如实计入 `backend_calls`，不得把一次读取伪装成零调用。
- 预算错误应指出超限的是 `items`、effective resources、effective Pages、protected descendants、direct siblings 或其他具名维度，并返回配置上限与 content-free observed count；不得泄露 Page 标题、ID、正文或原始 XML。
- 修复后不得改变 Batch 首个失败即停止、`applied/failed/not_attempted`、零自动 rollback、零 mutation replay 和人工恢复指引合同。

## 自动化验证要求

- 在一个含超过 200 个无关 Page 的确定性 hierarchy fixture 中，10 个同 Section、无重叠叶子目标的 `batch_delete("page", ...)` 必须通过预检并进入受控执行；测试不得通过提高 `LOCAL_ONENOTE_MAX_COPY_PAGES` 实现。
- 同一大 Notebook fixture 下，Page/Section/SectionGroup Batch Rename、Reparent 和 Delete 均证明无关 Page 不消耗 effective target budget。
- Batch Create 在 Notebook 已超过 Copy 默认资源/Page 数时，不因 CopyBudget 拒绝；仍验证 items、内容字符、名称冲突、parent confirmation 和其自身明确预算。
- 目标 effective scope 本身超限时仍必须在零 principal mutation 下拒绝。Page case同时覆盖 `include_subpages=false|true`、多层子树、受保护后代和 batch-wide union；容器 case覆盖完整后代范围。
- 冻结失败 envelope 的 `mutation_attempted=false`、`attempts=0`、准确 `backend_calls`、具名预算维度、无内容泄露与零 replay。
- 增加回归证明单项路径和 Copy/Move 的原 CopyBudget 行为没有被放宽或改名污染。
- 从实际 Tool Registry/schema 枚举所有公开 `items`-capable 工具，并断言集合精确等于当前 12 项 inventory；对枚举结果逐项证明其 handler/preflight 不读取 `CopyBudget.current()` 或 `LOCAL_ONENOTE_MAX_COPY_*`。未来新增 batch 工具而未声明 mutation-specific budget contract 时，该测试必须失败。

## 真实后端验证要求

- 在 `tests/manual_validation/` 中新增或扩展一个具名、human-gated scenario，使用 fresh disposable Notebook 和静态最小权限。
- 场景用有界 fixture 制造“Notebook 总 Page 数高于测试用 batch effective-scope 上限，但目标 scope 低于上限”的条件；允许通过独立的测试期 BatchMutationBudget override 降低阈值，避免仅为跨过默认 200 而创建大量 Page。
- 至少验证一组无重叠叶子 Page 的 Batch Delete 能越过预检、全部非永久删除并完成 after evidence；同时验证一个真实 effective-scope 超限请求在零 mutation 下 fail closed。
- 若 UT-010 已实施，场景还必须覆盖混合 `include_subpages` 的 batch-wide scope/提升证据；若 038 先实施，则完成状态必须等待与 UT-010 的接口集成回归，不能以旧 `page_scope` 证据关闭。
- Agent、pytest、CI、hook、timer、watcher 或后台任务不得运行真实 scenario；只能由用户本人前台显式启动并确认。

## 非目标

- 不通过提高用户的 `LOCAL_ONENOTE_MAX_COPY_PAGES` 或建议拆成单项删除来掩盖 bug。
- 不降低 exact ID、confirmation、同 Notebook、回收站、祖先/后代 overlap、权限、收敛或 partial-failure 门限。
- 不把 hierarchy snapshot 扩展为 Page 正文读取、raw XML 公共能力、后台索引或无界扫描。
- 不在测试或人工场景中使用用户此次报告的真实 Notebook、Page ID、标题或已删除现场。

## 完成定义

- [x] Batch Mutation 与 CopyBudget 完全解耦，新的有界预算职责、名称、默认值和 Agent 可见投影明确；
- [x] 当前 12 个 `items[1..20]` 工具全部完成 Registry/schema/handler 预算审计；新增 batch-capable 工具具有 fail-closed 的自动纳管合同；
- [x] 大型 Notebook 中少量精确 Batch Rename/Reparent/Delete 不再因无关 Page 数量误拒绝；
- [x] Batch Create 的同源整本 CopyBudget 耦合已修复或以明确证据证明具有不同且合理的容量合同；
- [x] UT-010 的 `include_subpages`、完整子树和 batch-wide 后代提升都按 effective scope 正确计费；
- [x] 聚焦自动化覆盖成功、真实 scope 超限、confirmation/overlap、partial envelope、backend-call accounting、零 replay 和内容脱敏；
- [x] 共享行为变更后的完整 pytest 与所有相关 `--dry-run` 通过；
- [x] 用户在 fresh disposable 场景中确认“大 Notebook/小目标成功 + 真实 scope 超限零 mutation”两条真实路径；
- [x] 生产实现、根 README、公开 Tool contract、Operation Runtime、配置/health 和 manual-validation 文档同步。

## 完成证据记录

| 证据 | 结果/位置 |
| --- | --- |
| 用户原始复现的 content-free 摘要 | 2026-08-18：10 个无重叠叶子 Page；validation_error；mutation_attempted=false；attempts=0；backend_calls=1；单项目标随后成功 |
| 根因与受影响 batch inventory | `_preflight_batch_targets`、`batch_create` 及 Reparent 内层 `_capture_reparent_hierarchy` 均已解除 CopyBudget 耦合；Registry/schema 自动枚举固定当前 12 项 |
| 新预算合同与公开配置/health 投影 | `BatchMutationBudget` 五维 content-free 合同；`health_check.batch_mutation_budget`、README、Tool contract、Operation Runtime 已同步 |
| 聚焦自动化与完整 pytest | Batch/Policy/Server/Config 与完整 manual-validation 纯合同已覆盖；大型 Notebook 的 12 个公开 batch 工具均直接证明一次预检快照加一次整批最终回读，Page 两种范围/多层树/batch union、容器完整后代、失败 envelope 均有回归；完整 `.venv\\Scripts\\python.exe -m pytest -q`：1340 passed in 67.88s |
| Manual-validation dry-run | `delete`、`reorder-page`、`copy-page`、`move-page`、`reparent-page-with-level` 五个 `--dry-run --json` 均通过；Delete fixture v5 将两叶子 Page batch 与 mixed `include_subpages=false/true` 树范围 batch 独立执行，并投影 `batch_mutation_budget.max_effective_pages=5`、human-only、server_started=false |
| UT-010 集成 | 五个 Page Tool 已统一 `include_subpages=false|true`；Page Reparent/Delete batch 冻结整批 scope 并先完成按 Section 的一次性提升；用户 5 个 fresh disposable run 全部 passed/closed，UT-010 已完成 |
| 用户确认的真实 disposable 证据 | `run-2026-08-18-11-36-29`：两叶子 Page batch applied；Notebook Page 数超过有效上限；mixed `false/true` batch applied 且受保护子页正文不变；6-Page Section 以 `effective_pages` observed=6/configured=5 在 preflight 拒绝，`mutation_attempted=false`、`attempts=0`、`backend_calls=1`、`replayed=false`、bridge mutation calls=0；顶层/scenario passed，lifecycle=`closed_preserved` |

## 关联

- [TODO 037 / UT-010](037_user_testing_experience_feedback_and_optimization.md)：统一 `include_subpages`、Delete/Reorder 后代保护和 Page batch-wide scope 规划。
- [公开 Tool 契约](../design/tool_contracts.md)：Batch schema、预算、错误和 partial outcome 的 canonical 归属。
- [Operation Runtime](../design/operation_runtime.md)：backend-call accounting、attempt、replay 和 outcome 语义。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：真实 mutation 只能由用户前台显式启动。
