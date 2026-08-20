# Copy/Move Read Reason Ledger

## 目的与边界

本文是 `CopyService` 的开发、排障与 trace 对账表。它说明 Copy/Move 单次调用中每个 `get_hierarchy`、`get_page_content` readback 的**证据用途**；不记录对象 ID、标题、路径、XML 或任何正文。

`read_reason` 只会出现在启用了本地 Debug Trace 的 backend 行中，值必须属于固定 allowlist。它不是用户输入，也不是调用结果；一次逻辑读取若命中本调用的 phase-local cache，就不会产生 backend 行，因此也不会产生新的 `read_reason` 事件。

适用范围仅限七个公开 Copy/Move 工具及其调用的共享 `MutationService`。独立 Create/Delete/Rename 等工具不因复用这些服务而新增 `read_reason` 字段。`get_special_location`、`update_page_content`、`update_hierarchy` 与删除等非 hierarchy/Page 读取不在本表范围。

## Reason 对照表

| `read_reason` | backend read | 触发阶段与证据用途 | cache / mutation 边界 |
| --- | --- | --- | --- |
| `source_confirmation` | hierarchy | 公开调用刚进入时，确认 source 的类型、父级、名称/标题与可选 modified。 | 第一个 hierarchy snapshot；同一 epoch 的 planning 可复用。 |
| `plan_capture` | hierarchy、Page | 捕获 source 范围、拓扑与 source Page XML，建立内部 Copy plan 和保真比较基线。 | 可能命中 `source_confirmation` 的 hierarchy snapshot；每个 Page XML 以 `(page_id, scope, epoch)` 缓存。 |
| `destination_precondition` | hierarchy | 确认 destination 父级、重名冲突、可创建位置，以及共享创建服务的父级/已分配 ID 前置条件。 | 无 mutation 时可复用已有 hierarchy snapshot；同一份 epoch 校验 snapshot 同时满足 parent 类型与含回收站 `before_ids`，不再拆成两次 live read。 |
| `post_create_convergence` | hierarchy | `create_notebook` / `create_section_group` / `create_section` / `create_page` 后，等候并确认新对象的 ID、父级和名称。 | 创建是 mutation，必须 live read；不得复用创建前快照。 |
| `pre_write_target_observation` | Page | 写入 transformed Page XML 前，记录 target 的精确 pre-state，供对账判断。 | 创建后 epoch 的 Page live read；写后失效。 |
| `post_write_reconciliation` | Page | `update_page_content` 返回或报错后，判定 applied / not-applied / partial 的第一份 target 观察。 | 写入后必须 live read；若已满足，作为 convergence 的首样本。 |
| `post_write_convergence` | Page | 仅在 reconciliation 首样本不足以形成稳定证据时，继续观察 target Page。 | 不复用写前或写后旧 epoch；成功路径可能没有这类 backend 行。 |
| `topology_verification` | hierarchy | 排序 XML 构造所需的当前目录、Copy target 的父级/Section/order/pageLevel 收敛，以及 partial outcome 的目标位置投影。 | 同 epoch catalog 可注入 `page_order_xml(..., catalog=...)`；每次 `update_hierarchy` 后仍重新读取 live topology。 |
| `source_drift_revalidation` | hierarchy、Page | Move 在删 source 前重新读取 source（容器 Move 还读取 target），确认 protected topology 与 Page 内容没有 drift。 | Copy 完成后 epoch 已变更，必须 live read。 |
| `delete_confirmation` | hierarchy | Move 删除前确认 source 或 root-only Page 子树、保留 descendant 的 promotion 前置，以及 promotion 的排序 XML 构造。 | 每次源拓扑 mutation 前独立 fresh snapshot，不入 cache，也不用 source-drift snapshot 授权删除/提升。fresh confirmation 只能发现读取前的外部变化，不消除随后 dispatch 的 TOCTOU。promotion 前与 delete 前各需要一次。promotion 后只从已验证 observation 重绑源 root 的 `modified`；标题/Section 不重绑，第二次 fresh confirmation 仍拦截随后的外部变化。 |
| `delete_convergence` | hierarchy、Page | 删除或 descendant promotion 后，确认 source 已不活跃、保留 descendant 拓扑与 Page 内容稳定。 | 删除 reconciliation 的成功观察（含合法 `None`）可作为 convergence 首样本，但仍要求既有稳定次数；Page scope final check 只复用最后一次匹配的私有 observation。CopyService 最终 destination-position **继续 fresh-read**，不复用删除 snapshot。 |

## 典型成功路径

Page Copy 的通常顺序为：`source_confirmation` → `plan_capture` → `destination_precondition` → `post_create_convergence` → `pre_write_target_observation` → `post_write_reconciliation` →（必要时）`post_write_convergence` → `topology_verification`。其中相邻的 hierarchy 用途可能复用同一个 read-only epoch snapshot，所以 trace 中不一定逐项各有一条 `get_hierarchy`。

Page Move 在 Copy 成功后额外出现 `source_drift_revalidation`、`delete_confirmation` 与 `delete_convergence`。安全证据顺序固定为 `source-drift live read → fresh delete_confirmation → delete → reconciliation 首样本 → 至少一次新的 live stable observation`。root-only promotion 在 drift 之后还有一次 promotion 前 fresh confirmation，以及 `update_hierarchy` 之后、delete 之前的第二次 fresh confirmation。promotion 收敛后只从该已验证 observation 重绑源 root 的 `modified`；标题和 Section 继续使用 source-drift/plan 绑定值，第二次 fresh confirmation 负责拦截随后的外部变化。Section、SectionGroup 与 Notebook Copy/Move 复用同一表；容器路径会为其包含的每个 Page 重复 Page 级 evidence，但不改变 reason 含义。

确定性 ledger 分两层，都不是跨环境性能承诺：

- 七个公开工具的轻量 budget ledger：发现 CopyService 广度回归；Move 成功路径含 1 次 `delete_confirmation` hierarchy read。
- 三条走真实 `CopyService → MutationService → Hierarchy/PageService → BaseService.call()` 的 scripted 组合路径：普通 Page Move、root-only promotion、一个容器 Move。promotion 路径断言 promotion 前和 delete 前各有一条 fresh `delete_confirmation`。

非 promotion 同形状成功样本的暂定目标为 `14P + 5`（hierarchy `6P + 4`、Page read `4P`、mutation `4P + 1`）；promotion 至少再多一次 promotion 前 fresh hierarchy。最终以 ledger 和用户优化后 trace 为准。

## 维护与验证

- 新增 Copy/Move 的 hierarchy 或 Page readback 前，必须先在此表选择已有 reason；若语义无法表达，再以最小新增项扩展 `read_reasons.py` allowlist、设计文档与 fake ledger 合同。
- 不得在 bridge 层按 operation 名推测 reason；调用点必须显式安装上下文，避免把写操作或独立 mutation 工具误标为 Copy/Move readback。
- `tests/test_copy_readback_ledger.py` 冻结所有公开 Copy/Move 形态的 `(backend_operation, read_reason)` 预算，并额外覆盖共享 `MutationService` 的创建、删除内部回读与三条组合路径。Copy/Move 归因上下文内的 hierarchy/Page 读取不得留下 `None` reason。
- 实际排障时，只比较同一操作形态和相同 OneNote 状态下的 content-free JSONL：先看 operation 分布和 reason，再解释 cache hit、epoch 失效或 convergence 重试；不要从单次墙钟耗时推导性能承诺。
