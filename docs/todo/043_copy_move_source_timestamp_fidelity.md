# 043：Copy/Move 目标创建与修改时间保真

> ID：043
> 状态：待办
> 优先级：P1
> 类型：开放方向 / Copy / Move / 元数据保真 / OneNote COM
> 更新日期：2026-08-19

## 决策摘要

本 TODO 保留为日后可能开放的研究方向，不是当前产品承诺、已排期路线图或兼容性保证。当前产品明确把 Copy/Move 定义为内容与拓扑级重建：不继承 source revision marker、原始创建时间或原始修改时间；现有 `verified`、`lossless` 与 `copy_contract_satisfied` 也不覆盖这些字段。当前边界见[产品能力边界](../product/README.md)和[工具契约](../design/tool_contracts.md)。

若未来重新作出产品决策，目标可以是：Copy 与重建式 Move 在完成目标资源的创建、内容写入和拓扑调整后，把目标资源的创建时间与修改时间恢复为 source 对应资源的值，并对 exact target ID 做回读验证。Move 只有在该时间元数据与现有内容、标题和拓扑合同全部通过后，才允许删除 source；时间回写不支持、失败或回读不一致时必须保留 source，并返回可诊断的 `copy_only`/未验证结果。

本 TODO 只描述未来探索时可采用的目标合同，不把 OneNote COM 对各资源类型时间 attribute 的可写性当作既成事实。启动实现前必须先有新的产品级决策，并建立 Page、Section、SectionGroup、Notebook 的能力矩阵；无法可靠回写的类型应显式报告能力限制，不得继续声称完整保真。

## 当前缺口

- 公共层级对象已将 COM XML 的 `dateTime/createdTime` 映射为 `created`，将 `lastModifiedTime` 映射为 `modified`；
- 当前 Page Copy 的 canonical/lossless 比较把 `creationTime`、`dateTime` 和 `lastModifiedTime` 视为 OneNote-owned volatile attributes，因此新目标可以使用当前时间，仍被报告为 `lossless=true`；
- Copy 后得到的新资源丢失 source 的时间轴语义；Move 又建立在 Copy-before-delete 之上，若随后删除 source，用户最终只剩带新建时间和新修改时间的目标；
- 内容写入、子树创建、重排或标题修正都可能再次推进目标的修改时间，因此不能只在创建时复制一次时间字段。

## 工作范围

### A. 能力探测与公开合同

- 覆盖全部公开 Copy：`copy_page`、`copy_section`、`copy_section_group`、`copy_notebook`，以及对应的重建式 `move_page`、`move_section`、`move_section_group`；
- 按资源类型验证 OneNote COM 是否允许写入并稳定保留 `dateTime/createdTime` 与 `lastModifiedTime`，记录所需 XML/API、写入顺序、时间精度和版本差异；
- 明确本项只处理层级资源本身的 `created/modified`，不顺带改写 Page 内部 OE、Tag 等 content object 的 `creationTime`；后者如有保真需求应单独建项；
- 时间值按 ISO/RFC 3339 表示的同一时刻比较，保留 source 时区表达；只有真实后端证据证明 OneNote 会做固定精度归一化时，才允许加入最窄的语义等价规则，禁止无界时间容差。

### B. 执行顺序与子树映射

1. 内部 planning 以 exact source ID 冻结每个待复制资源的 `created/modified`，并将其绑定到生成的 exact target ID；不得按标题、名称或 legacy `path` 反查目标；
2. 先完成目标创建、Page content 写入、标题修正、层级创建和最终 reorder，再进行时间回写；
3. 子树 Copy/Move 在所有结构 mutation 完成后按叶到根恢复时间，避免创建或调整子对象再次推进父容器的 `modified`；
4. 对每个 source→target 映射逐项回读，验证 target 的创建时间和修改时间均与冻结值语义相等，同时重校验 source 未发生漂移；
5. 批量或递归操作必须按整批映射汇总结果，任何一项不支持、缺失、漂移或不一致都不能被其他成功项掩盖。

若 source 本身未返回某个必需时间字段，应明确标记该字段为不可验证；不得用当前时间、父对象时间或相邻对象时间补值。

### C. Copy/Move 结果与失败语义

- 在 `copy_report` 中增加逐资源、content-free 的时间保真结果，至少能区分 `verified`、`source_missing`、`write_unsupported`、`write_failed`、`readback_mismatch` 和 `source_drifted`；不得记录 Page 正文或 raw XML；
- `copy_contract_satisfied` 与完整 fidelity 结论必须纳入时间回读；不再仅因内容/标题/拓扑一致就声称完整保真；
- Copy 的时间回写失败时保留已创建 target，返回清晰的 partial/unverified 结果和 exact target ID，禁止自动重试出第二份副本；
- Move 的删源门必须依赖整批时间保真通过。任一 target 失败时保留所有尚未删除的 source，并按现有安全合同报告 `copy_only`；不得为了得到“移动成功”而降低时间检查；
- 如果平台确认某资源类型无法可靠写入时间，需要产品层明确决定该类型是否允许“内容保真但时间不保真”的 Copy。该降级不得自动扩展到 Move，Move 默认继续 fail closed。

## 自动化与真实验证

### 自动化合同

- 覆盖四种 Copy 与三种 Move 的 source 时间捕获、exact ID 映射、最终写入顺序和回读汇总；
- 覆盖 root-only、带子树、跨 Notebook、重名目标以及 batch/递归部分失败；
- 覆盖等价时区表示、固定精度归一化（若能力证据支持）、缺失字段、不可写、写入后被后续 mutation 改写、回读 mismatch 和 source 并发修改；
- 断言 Move 在任一时间检查失败时零次删源，并且不会通过补做 Copy 隐式产生重复 target；
- 保留现有 content-object comparator 的职责边界，防止把层级时间字段重新混入正文对象差异或 typed equivalence 错误。

### Human-gated 真实验证

- 在 `tests/manual_validation/` 为受影响的具名 Copy/Move scenario 增加时间 before/after 证据；真实运行仍只由用户本人显式启动；
- 至少验证 Page、Section、SectionGroup、Notebook 各自的创建/修改时间可写性与稳定回读，并覆盖一个带子树的叶到根恢复顺序；
- 至少构造一次时间回写或回读不一致的负向路径，证明 Move 保留 source、保存 exact target 诊断且不执行删除；
- 证据只保存 resource type、ID、标准化时间和比较结论，不保存 Page content、raw XML 或用户标题。

## 非目标与安全边界

- 不通过直接修改 `.one` 文件实现时间回写；只能使用受控的 OneNote COM/XML 路径；
- 不扩大现有 Create、Writes、Deletes、Notebook Lifecycle、Local File IO 或其他 policy 权限；
- 不以无界全 Notebook 扫描寻找目标或做回读，预算按实际 source→target scope 计费；
- 不承诺复制 OneNote 未公开或无法稳定回写的审计字段（例如最后修改者）；能力不足时必须显式暴露限制；
- 不由 pytest、CI、agent 或后台任务启动真实 OneNote mutation scenario。

## 完成定义

- [ ] 四类资源的时间字段写入/回读能力矩阵已由用户真实验证并记录；
- [ ] 全部公开 Copy 与重建式 Move 使用 exact ID 映射，在最后一次结构/content mutation 后恢复 source `created/modified`；
- [ ] 子树按叶到根恢复，batch/递归结果按整批聚合，局部失败不会被误报为完整成功；
- [ ] `copy_report` 提供 typed、content-free 时间保真诊断，完整 fidelity 与 Move 删源门均纳入该结果；
- [ ] 时间不支持、写入失败、回读 mismatch 或 source drift 时，Copy/Move 均 fail closed，Move 证明 source 未删除；
- [ ] 自动化合同、受影响的 design/README 文档以及具名 manual scenario 同步完成；
- [ ] 用户确认各受支持资源类型的真实 Copy/Move 正向证据和至少一条 Move 负向删源保护证据。

## 关联

- [对象模型](../design/object_model.md)：当前 `created/modified` 的公开字段映射；实现完成后应在此固化当前契约。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：Copy/Move 内部 planning 与服务端证明职责。
- [TODO 040](040_move_readback_validation_followups.md)：Page Move 内容、标题与 typed readback 校验的历史闭环；本项新增时间元数据维度。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
