# 027：Reparent 三层级人工验证矩阵与 `all` 覆盖

> ID：027
> 状态：已完成
> 优先级：P2
> 类型：人工验证 / Reparent 回归矩阵与批处理资格
> 更新日期：2026-08-14

## 目标

让 human-gated `all` 明确覆盖 Page、Section、SectionGroup 三类同 Notebook Reparent，并锁定以下操作矩阵：

| 场景 | case |
| --- | --- |
| `reparent-page` | 一个同 Notebook 跨 Section case：`01-Source-Section` → `02-Destination-Section` |
| `reparent-section` | Notebook → SectionGroup、SectionGroup → Notebook；保留现有 SectionGroup → SectionGroup 第三个 case |
| `reparent-section-group` | Notebook → SectionGroup、SectionGroup → Notebook、SectionGroup → SectionGroup |

本 TODO 不新增重复的 Scenario 名称。三个具名场景、各自的 scenario-owned recipe、静态权限、before/after/restore evidence 和注册 dry-run case 已存在；本轮补齐的是对现有矩阵的审计，以及 `reparent-page`、`reparent-section-group` 缺失的 `included_in_all` 资格。

## 现状确认

- `reparent-section` 的 `REPARENT_PLANS` 已依次包含 `notebook-to-section-group`、`section-group-to-notebook`、`section-group-to-section-group`，因此明确覆盖要求确认的前两个方向，并额外保留第三个合法方向。
- `reparent-page` 的 recipe 只声明一个源 Section、一个目标 Section 和一个富内容目标 Page；正向只调用一次 `reparent_page`，允许精确一对一 ID remap，并用 fresh ID 默认恢复。
- `reparent-section-group` 具有三个独立目标 Group、精确 source/destination parent 和编号 Section/Page 后代，按三个要求方向正向执行，再按 `03 → 02 → 01` 逆序恢复。
- 三个场景只允许同一 Notebook typed Reparent。Page/SectionGroup 不要求 Raw XML、Copy、Move、Delete 或 Reorder 权限；fixture 写入、证据读取和 Reparent mutation 仍保持场景级最小权限。
- 历史上用户已单独确认三个 typed Reparent 场景通过，证据边界见 [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md)。后续 `destination_position` 变化的当前版本仍受 [TODO 013](013_reparent_default_placement_contract.md) 的新真实回归门限约束。

## 本轮变更

- [x] 确认 `reparent-section` 覆盖 Notebook → SectionGroup 与 SectionGroup → Notebook，并保留 SectionGroup → SectionGroup。
- [x] 确认 `reparent-page` 是一个同 Notebook 跨 Section case，具备隔离 fixture、ID remap、富内容、无关对象、位置证据和恢复合同。
- [x] 确认 `reparent-section-group` 覆盖要求的三个 parent transition，具备后代 ID/拓扑/内容和恢复合同。
- [x] 完成显式稳定性与权限审查，把 `reparent-page`、`reparent-section-group` 设置为 `included_in_all=True`；`all` 仍逐场景启动独立子命令，不合并权限、Notebook、MCP、evidence 或 lifecycle。
- [x] 更新 registry/`all` 纯合同与人工验证文档。
- [x] 根据 2026-08-13 用户真实单项 run 修复 materialized hierarchy 就绪轮询：初次全局 snapshot 不可见且 exact-self 返回 `0x80131501` 时，在原有有界窗口内同时重查两条严格证明路径，不接受裸 object ID 或 parent probe。
- [x] 根据 `run-2026-08-13-21-09-17` 与 `run-2026-08-13-21-12-37` 撤回两个无效的 `SyncHierarchy` 屏障；请求虽被接受，但 fresh fixture 仍未完整落盘，旧 cache working copy 仍无法激活首个 Section。
- [x] 将 `reparent-page` recipe 提升到 v3，并在 fresh/cold-build 首次 mutation 或 template publish 前增加 `CloseNotebook(false) → exact-path reopen → typed ID/evidence rebind → full live validation` 持久化检查点；旧 v2 fingerprint 不再命中。
- [x] bridge failure audit 读取最内层异常 HRESULT，同时保留 PowerShell wrapper HRESULT、异常深度和最内层异常类型，避免后续只看到包装层 `0x80131501`。
- [x] 由用户本人运行当前版本的真实 `all`，确认新增的两个批处理成员以及既有 `reparent-section` 均通过。

## 最新真实运行证据（2026-08-14）

- 用户启动的 `all --use-cache` 依次产生 `run-2026-08-14-00-11-15`、`run-2026-08-14-00-14-21` 和 `run-2026-08-14-00-15-47`。三个 run 均为 `validated_hit`、`status=passed`、`opened_template=false`、默认 restore 完成且 lifecycle 为 `closed_preserved`。
- Section run 的 operations 明确包含 Notebook → SectionGroup、SectionGroup → Notebook、SectionGroup → SectionGroup；Page run 完成唯一跨 Section case并验证 ID history、富内容与无关对象；SectionGroup run 完成三个 parent transition、后代身份/内容/位置验证与逆序恢复。
- 批处理在稍后的无关 cache consumer 上返回非零，但不改变这三个独立 child run 已完成且精确关闭的成功证据；失败隔离允许 `all` 继续执行后续 child，未复用它们的 Notebook、MCP 或 evidence。

- `run-2026-08-13-20-05-35`：`reparent-section-group --use-cache` 的三个 parent transition、完整内容/拓扑取证、逆序 restore 和 lifecycle close 全部通过。这是用户运行的真实后端证据。
- `run-2026-08-13-20-01-15` 与 `run-2026-08-13-20-04-40`：`reparent-page`、`reparent-section` 均未进入 mutation。两者在首个 materialized Section 上观察到 `OpenHierarchy` 返回 ID、exact parent 回读成功，但初次全局 snapshot 不可见，随后 16 次 exact-self 读取均为 `0x80131501`。因此失败属于 fixture activation，不是 Reparent read-back 或 mutation 回归。
- `run-2026-08-13-20-11-12` 与 `run-2026-08-13-20-12-00` 再次复现同一失败，但二者分别在 20:12:00、20:12:49 完成，早于双路径 lifecycle 修复在 20:18:40 落盘。两份 evidence 均没有 `global_snapshot_retry_*` 字段，bridge 日志也仍只有 exact-self 重试，因此只能强化旧缺口的诊断，不能作为修复后的真实复验。
- `run-2026-08-13-20-28-56`：fresh `reparent-section` 的三个 case、read-back、逆序恢复与 close 全部通过，确认共用两阶段 Reparent read-back 在当前后端可收敛。
- `run-2026-08-13-20-36-12` 与 `run-2026-08-13-20-44-38`：双路径修复后的 Section/Page cache run 仍在首个 materialized Section 失败；两条证明路径各完成 8 次观察，不能再归因为只缺少全局重查。当时增加的 working Notebook `SyncHierarchy` 后续被最新证据证明无效。
- `run-2026-08-13-20-24-21` 与 `run-2026-08-13-20-39-26`：fresh Page fixture 的 COM/live snapshot 已完整，但首次 `UpdateHierarchy` 返回 `0x80131501`。失败现场的 Notebook 目录只落盘两个 4,744-byte 空壳 Section，目标 Section 文件尚不存在，说明 fixture 的 COM 可读状态早于本地源文件提交。
- `run-2026-08-13-21-09-17`：上一版生产 Page 前置 `SyncHierarchy` 已在完整 baseline 前成功返回，但首次 `UpdateHierarchy` 仍在约 6.7 秒后返回包装层 `0x80131501`；磁盘仍只有两个 4,744-byte Section 和 catalog，没有 destination `.one`。因此“同步请求可提交 fixture”这一判断被真实证据否定。
- `run-2026-08-13-21-12-37`：上一版 materialized-open `SyncHierarchy` 同样已成功返回，但 absolute 与 parent-relative `OpenHierarchy` 返回相同 Section ID 后，exact-self/global 两条路径各 8 次仍不能证明首个 Section 激活。失败仍属于 fixture stage，mutation 未执行。
- `run-2026-08-13-21-33-17`：用户运行当前 v3 fresh `reparent-page`。`CloseNotebook(false)`、exact-path reopen、7 项 typed structure 映射、两个 Page evidence ID 重绑和完整 live validator 均通过；正向跨 Section Reparent 发生唯一 Page/内容对象 ID remap，富内容、同 Notebook、无关内容/关系全部通过，恢复形成三段 `id_history`，最终 Notebook 精确关闭，run 状态为 `passed`。
- `run-2026-08-13-21-37-14`：用户运行当前 v3 `reparent-page --use-cache`。由于新 fingerprint，cache decision 为 `cold_build`，未命中旧 v2 template；持久化检查点通过后发布新 template，再 materialize 新 working copy。全部 7 项 structure ID 和两个 evidence Page ID 均成功重绑，template `opened_template=false` 且 byte inventory `all_templates_unchanged=true`。业务正向验证、三段 ID history、默认恢复和最终关闭均通过，run 状态为 `passed`。
- 两个真实 run 共同确认 v3 持久化检查点解决了本轮 fresh 首次 mutation 和 cache fixture activation 两条失败路径；内层 HRESULT 诊断仍保留用于未来未知 COM failure。

## Agent 纯验证记录

2026-08-13 已完成以下非真实后端验证；Agent 未启动任何真实 OneNote scenario：

- `test_all_scenarios.py`、`test_reparent_scenarios.py` 与注册 dry-run 合同均包含在 manual-validation 完整纯测试中；
- `tests/manual_validation/tests` 当前收集 `630` 项，全部包含在本轮完整基线中；
- 仓库当前完整 pytest：`1002 passed`；
- v3 持久化检查点、lease archive、ID/evidence 重绑、内层 HRESULT audit 与 Reparent read-back 的聚焦纯测试：`231 passed`；
- 初次纳入时 `run.py all --dry-run --json --verbosity quiet` 为 `14 passed, 0 failed`；TODO 028 随后把受支持的 `reorder-section` 纳入批处理，当前为 `15 passed, 0 failed`，有序 child 5–7 分别为 `reparent-section`、`reparent-page`、`reparent-section-group`。

## HUMAN-GATED 验收

Agent 只能运行纯测试和明确的 dry-run。真实命令必须由用户本人在交互式前台终端启动：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

真实验收至少要从批处理结果确认：

1. `reparent-page` 执行且通过一个跨 Section case，正向/恢复 ID history 与内容、无关对象和 `destination_position` 证据完整；
2. `reparent-section` 的三个 operations 中包含 Notebook → SectionGroup 与 SectionGroup → Notebook，且逆序恢复；
3. `reparent-section-group` 的三个 operations 与要求矩阵完全一致，目标及后代身份、拓扑、内容和位置证据通过并逆序恢复；
4. 每个场景使用独立 run directory、disposable Notebook、MCP process、policy、fixture、evidence 和 lifecycle；任一失败必须非零并保留现场。

## 完成证据

当前版本的真实 `all` 已由用户执行，三个 Reparent child 均通过并独立关闭，因此完成定义已经满足：

- 两个新增 `all` 成员和既有 `reparent-section` 的矩阵合同、最小权限、默认恢复、失败保留与批处理隔离纯测试通过；
- `all --dry-run --json` 证明两个场景均进入有序 child plan，且 dry-run 不启动 MCP、不创建 fixture、不执行 OneNote mutation；
- 用户确认当前版本真实 `all` 中三个 Reparent 场景全部通过，并记录对应 run/evidence 结论；
- TODO 索引与 manual-validation canonical 文档保持一致。
