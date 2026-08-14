# Fixture cache consumer 必须重新建立 live identity 并执行实时验收

> 状态：当前有效的工程经验<br>
> 观察日期：2026-08-11、2026-08-13、2026-08-14<br>
> 范围：Windows OneNote Desktop、本地 COM、隔离的 InsertedFile fixture cache consumer、双 Notebook Copy consumer 与一次完整 `all --use-cache` 失败/成功矩阵<br>
> 当前架构：[`../design/architecture.md`](../design/architecture.md)<br>
> 验证流程：[`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md)、[`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md)<br>
> 相关对象表示经验：[`onenote_page_object_kind_and_file_attachment_representation.md`](onenote_page_object_kind_and_file_attachment_representation.md)

## 结论

关闭并验证过的 OneNote fixture cache 只能证明 template bytes 和发布时证据可信，不能证明新 materialized working copy 已经具备可直接使用的 Notebook hierarchy、对象 ID 或实时内容状态。Materialized working copy 因而仍须在当前 run 中打开、按精确 parent 批量激活、按稳定的类型化结构地址重绑 live ID，并以连续两次 hierarchy 稳定和唯一一次完整内容 snapshot 运行 Recipe validator。2026-08-14 后续对照确认，先前大面积 fixture failure 与 scenario 启动前 OneNote Desktop 未运行稳定相关；close/reopen 的 2/15→12/15 改善是受进程状态混杂的中间结果，不能支持“两套 live identity 是必要条件”的结论。当前实现已改为 GUI preflight 后只打开一次 working copy。

这次排障还证明，consumer 的失败必须按归因分层。working copy 的临时打开或激活失败不能反向证明 immutable template 损坏；结构重绑定缺失、歧义或实时 validator 失败才提供 template 不应继续命中的证据。working lease 同样必须尽早绑定实际 Notebook ID 和路径，否则失败发生在 hierarchy 激活中途时，后续运行无法可靠区分“Notebook 仍打开”和“遗留 lease 已过期”。同时，传给 COM 的物理 working 路径本身也是兼容性输入；完整显示 identity 应保留在 evidence，不应无界地堆入 Notebook 目录名。

## 真实观察与证据边界

本经验来自同一隔离 InsertedFile fixture 从 bootstrap 发布到 cache-only consumer 成功期间保存的多次真实运行证据。排障过程依次观察到：

1. 人工 authored Canvas 已通过 detector 和人工 verdict，但首次 post-publish working copy 只出现已打开的 Notebook shell，没有活动 Section；template manifest 中的 Page ID 也不能直接解析到 working copy。
2. 使用文件名和 parent ID 调用 `OpenHierarchy` 时，COM 可以返回 object ID，但目标 Section 仍未进入 exact working Notebook hierarchy。返回 ID 本身不能证明激活成功。
3. 一次修复错误地把绝对 `.one` 路径和非空 parent ID 组合传入 `OpenHierarchy`，当前环境返回 `0x80042006/hrFileDoesNotExist`。这说明两个分别有意义的参数形式不能任意混用。
4. working activation failure 曾被错误归因为 template invalid，导致下一次 consumer 表现为 cache miss；调整失败分类后，template 保持可重试，结构或内容验收失败仍保持 fail closed。
5. hierarchy 激活失败发生在实际 working Notebook ID 写回 cache lease 之前，遗留 lease 只持有 template 内部 ID，后续运行因此被 active-lease 门限阻止。将实际 ID/path 在 Notebook folder 打开并完成 exact-path 证明后立即持久化，才使 stale reconciliation 可判定。
6. 最终由用户执行的 cache-only consumer 命中既有 ready entry，只打开新 working copy，完成 live hierarchy 加载、ID 重绑定和实时 detector；机器投影精确观察到一个 `InsertedFile`，template 未打开，场景通过。用户显式选择保留现场，因此 working Notebook 和 active lease 按设计继续存在。
7. 后续命名改动把固定长前缀、毫秒和 UTC offset 一并写入物理 Notebook 目录；两次新 working path 均在 Notebook folder 的首次 `OpenHierarchy` 返回 `0x80042006`，当时 working 目录路径长度为 184。将名称改为由双下划线包裹的 scenario、可选 `CACHED` 和本地秒级时间戳后，working 目录路径缩短为 143；用户连续执行的两次 consumer 均命中同一 ready entry、完成 hierarchy/live validation 并精确观察到 `InsertedFile=1`。第一次正常关闭 working Notebook，第二次由用户显式选择 `--keep-worksite` 并按契约保持打开。
8. 后续双 Notebook Copy 复验推翻了“同 fingerprint/instance 的 active lease 必然阻止下一次 consumer”这一过度推断。`run-2026-08-11-19-07-17` 保留第一组 source/destination working bundle 后，`run-2026-08-11-19-10-38` 从相同 entry 再次命中并 materialize 第二组路径；OneNote 为第二组生成了与第一组全部互异的 live Notebook ID。第二组独立完成业务验证、恢复和关闭，第一组 lease 仍保持 active。另一次 activation failure 则保留了尚未完成独立 live identity 建立的 ID，后续 claim 因真实 ID 集相交而拒绝，并在用户关闭旧 Notebook 后恢复成功。这两个分支共同表明 lease 冲突的真相是实际 identity/path 冲突，而不是 fingerprint/instance 相同。
9. 2026-08-13 两个用户运行的 cache consumer 在首个 materialized Section 上都得到 `OpenHierarchy` ID 和正确 parent，但初次全局 snapshot 尚不可见，之后 exact-self 连续返回未分类的 `0x80131501`；同批第三个独立 consumer 则完整激活并通过三项业务 mutation、取证和恢复。这组对照不证明该 HRESULT 普遍可重试，但证明“初次全局读取一次失败后只轮询 exact-self”会遗漏另一条可能随后收敛的严格证明路径。当前设计因而在同一有界窗口内重查全局与 exact-self，仍要求其中一条完整证明类型、身份、非回收站状态和精确 parent，不能降级接受裸返回 ID。
10. 随后的 `run-2026-08-13-21-09-17` 与 `run-2026-08-13-21-12-37` 又推翻了“先请求 `SyncHierarchy` 足以消除落盘/激活窗口”的判断。fresh run 中同步请求成功后，完整 COM baseline 仍可见，但首次 Page `UpdateHierarchy` 失败，磁盘缺少 destination Section 文件；cache run 中同步请求同样成功，但首个 copied Section 仍无法通过 global/exact-self 激活证明。请求接受不能作为 source file 已提交或 working hierarchy 已就绪的证据。
11. 2026-08-14 在修正 batch 立即误判后，用户运行的完整 `all --use-cache` 中只有 2/15 场景通过；其余 13 个场景在第一次 batch 请求均成功后，后续 16 次独立 hierarchy observation 保持同一缺失集合，并全部停在 mutation 前。失败包括刚 cold-build、关闭发布并立即 materialize 的模板，排除了旧 cache bytes 污染与短暂 sleep 不足。全部失败 working lease 都精确关闭，批处理仍继续到底；同批 `reparent-section-group` 完成三次 mutation、三次恢复和最终关闭，证明问题不是 cache 全面不可用，而是第一次导入身份没有稳定成为后续 COM session 的 mutation identity。
12. 统一 import/close/reopen 后，用户运行的新一轮完整批次通过 12/15，全部失败仍被精确关闭且没有跨场景扩散。Create 只在旧 full preset 中持续缺少 `Disposable-Page`；Copy SectionGroup 的发布 inventory 已经没有原本为空的 `99-Group-Anchor-B` 物理目录；Move SectionGroup 则完成 Copy、内容/拓扑验证和一次源根非永久删除后，仅因目标 `modified` 继续推进而被完整摘要误判。这组证据当时缩小了失败面，但尚未控制 OneNote Desktop 初始进程状态。
13. 用户随后做了稳定对照：OneNote GUI 已启动时当前 manual validation 全绿；GUI 未启动时多个 use-cache fixture 在 mutation 前稳定失败。加入 check-only GUI preflight 后，最新完整 `all` 的 15 个场景全部通过。该变量比 close/reopen、Recipe 类型或 cache hit/cold build 更能解释先前矩阵，因此当前实现撤销统一 checkpoint，并保留批量激活、typed remap、双稳定和单次内容取证。

`reparent-page` close/reopen 的历史成功仍是有效观察，但不再被解释为持久化窗口的因果证明。当前保留其 v3 cache identity 和 typed structure/evidence remap；Create v5 移除了只为该假设加入的 sentinel Page。Copy SectionGroup v5 与 Delete v2 的 typed sentinel Section继续承担“空 Group 需要物理子树”的独立 shape 约束。证据仍将 materialize 动作与 `validated_hit|cold_build|interactive_bootstrap` origin 分开，并分别记录字节复制、hierarchy 收敛和内容读取耗时。

上述成功只证明当前 OneNote/Office/Windows 环境中的这一个 InsertedFile Recipe consumer 链路。连续的失败/成功对照支持“较短物理名称解决了当前环境的打开回归”，但同时改变了路径长度和时间戳字符集，因而不能推导出 OneNote 的通用长度上限，也不能把 184 解释为所有版本的固定阈值。环境版本和附件表示范围记录在相关 [`kind`/附件表示 Lesson](onenote_page_object_kind_and_file_attachment_representation.md#观察环境) 中。pytest 和 `--dry-run` 只证明编排与状态机合同，不被当作真实 OneNote 行为证据。本文不记录 Page 正文、附件名称、Notebook 名称、对象 ID、用户路径或二进制内容。

## Materialization 复制的是 bytes，不是可复用的 live identity

opaque byte copy 可以证明 working tree 在打开前与 template inventory 一致，但 OneNote 打开副本时可以重新生成 Notebook、Section 和 Page ID。template manifest 中的旧 ID 因而只能作为发布时证据，不能直接成为 consumer mutation 或 validation 的目标。

可复用的是类型化结构地址，例如 Notebook 内的 SectionGroup/Section/Page 层级、对象类型和受约束的 Page order/level；consumer 必须在当前 working snapshot 中为每个声明对象找到唯一对应项，并记录 old-to-live remap。缺失或歧义都意味着 consumer 无法证明自己操作的是预期 fixture，应停止而不是按名称猜测。

## Notebook shell、COM 返回 ID 与可用 hierarchy 是三种不同状态

排障中出现了三个容易混淆的阶段：

1. Notebook folder 已被 OneNote 打开，并报告 exact working path；
2. `OpenHierarchy` 已返回 Section 或 SectionGroup object ID；
3. 完整 hierarchy 回读中出现类型正确、parent 正确的活动对象及其 Page。

只有第三阶段能够支持后续 ID remap 和 live validation。仅凭第一阶段会接受空 Notebook shell，仅凭第二阶段则可能接受位于其他父级、最近打开分区或尚未进入目标 hierarchy 的对象。consumer 的激活证明必须同时检查活动对象、resource type 和 actual parent relationship。

当前环境的排障支持两种有序的打开形式：先尝试绝对 working path 与空 relative ID，必要时再尝试 child filename 与精确 parent ID。该经验解释为什么要保留兼容回退和 parent 回读；当前实现顺序与错误处理仍以 canonical 验证文档为准，而不是由本文定义。

Notebook folder 的打开还表明，Windows 文件系统能看到 `.one`/`.onetoc2` 并不足以证明 OneNote COM 能打开该物理路径。当前决策是让 Notebook 名称只承载 scenario、role、可选 `CACHED` 和本地秒级时间戳，并把完整本地 ISO 时间、UTC offset、时区名称和其他运行 identity 留在 JSON evidence。这是当前环境已验证的工程取舍，不是对所有 OneNote 版本的路径上限声明。

## 失败归因决定 cache 状态

早期实现把所有 materialized-open、remap 和 validator 失败统一 quarantine。这会把 OneNote 当前进程状态、激活延迟或一次 working copy 故障错误升级为 template 内容损坏，使一次可重试失败永久表现为 cache miss。

更可靠的归因边界是：

- working Notebook 打开、Section 激活或本次 COM 环境失败，只能证明当前 working run 未通过；保留 run、Notebook、lease 和 content-free 诊断，但不能据此修改 immutable template 的可信结论；
- template identity、byte inventory 或 Recipe compatibility 不成立，说明 cache entry 本身不兼容；
- live typed-address remap 缺失/歧义，或 live Recipe validator 失败，说明本次 working run 无法证明可安全使用，应继续 fail closed并保留证据；除非错误能确定回溯到 template identity、inventory 或缓存证据完整性，否则不自动改变 entry matchability；
- consumer 自身后续业务动作失败，不自动证明输入 template 损坏，必须单独记录 mutation/restore 结果。

这里的关键不是放宽失败条件，而是让失败影响正确的状态域：run-local failure 终止本次运行，template failure 才改变 cache matchability。

## Lease 必须在最早可证明的时刻绑定实际身份

consumer 在打开 Notebook folder 并证明 `actual_path == working_path` 后，就已经拥有可持久化的实际 Notebook ID/path。若等到全部 child hierarchy 激活成功才写 lease，中途失败会只留下 materialization 前的 template ID，后续进程无法精确判断哪个 Notebook 仍然打开。

因此应尽早保存实际 identity，并让失败 evidence、lifecycle lease 和 cache working lease 能互相补足。若遗留 lease 的实际 ID/path 与新 claim 相交，下一次 claim 只能在 exact ID/path probe 证明旧 Notebook 已关闭后把它标记为 stale；不能通过删除 lease 文件、忽略冲突或复用同一 working directory 绕过安全门。若新 materialization 使用唯一 working paths，且打开后得到的全部 live Notebook ID 与现有 active leases 不相交，则相同 fingerprint/instance 的 lease 可以并存。

`--keep-worksite` 是这一状态机的显式分支：成功后 Notebook 和 active lease 都应保留，便于 UI 检查；它们不是泄漏，也不是 cache entry 的排他锁。后续 consumer 可以从同一 immutable entry 创建身份互异的隔离 working bundle，但不得关闭、接管或修改已保留 worksite。只有实际 ID/path 冲突的重试，或对该 entry 的 invalidation/cleanup，才必须先等待用户关闭相关 exact working Notebook 并完成 stale reconciliation。

## Consumer 应是独立的可执行回归边界

交互式 bootstrap 负责创建和发布人工 authored fixture；consumer 不应在 miss 时隐式进入人工等待、创建替代 fixture 或自动放宽 policy。独立 cache-only consumer 的价值在于把下面这条链路变成可重复验证的功能边界：

```text
validated cache lookup
→ new working materialization
→ exact working-path proof
→ first live identity bounded hierarchy activation
→ stable hierarchy and live ID remap
→ live Recipe validation
→ consumer result
```

真正 miss、版本不兼容或已确认的 invalid entry 应在打开 Notebook 和启动业务 mutation 前停止，并指向具名 bootstrap。这样 bootstrap 证据与 consumer 回归证据不会混在同一个隐式流程中。

## 对证据设计的启示

- authored snapshot、hierarchy-open evidence、old-to-live remap 和 live detection 应分开保存；后一步失败不能覆盖前一步证据。
- hierarchy-open evidence 只需要路径角色、请求形式、返回 ID、actual parent、对象计数和状态，不需要 Page 正文或二进制。
- cache failure evidence 应明确记录失败属于 open、remap、validator 还是 consumer action，以及 template 是否仍可命中。
- lease 冲突应报告精确旧 run 和 working role，使用户能关闭正确现场；模糊的“内部 ID 已占用”会迫使用户猜测。
- 成功的 active lease 只保护其 working identity，并阻止对应 entry 的 invalidation/cleanup；它不能被误用为 fingerprint/instance 级 consumer 互斥锁。
- 成功报告应同时证明 cache decision、live revalidation、template 未打开、working path 和 lifecycle 结果，不能只返回业务 detector 的最终布尔值。

## 不可靠的实现捷径

- 直接复用 template manifest 中的 Notebook、Section 或 Page ID；
- 只比较 byte inventory，就跳过 materialized live validation；
- 把 Notebook shell 已打开当成 Section/Page 已加载；
- 把 `OpenHierarchy` 返回 object ID 当成 actual-parent 证明；
- 将 absolute path 与非空 parent ID 任意组合；
- 把完整时区、毫秒和长固定前缀全部编入物理 Notebook 目录，却不经真实 COM open 验证；
- 任一 working activation failure 都把 ready template 标成 invalid；
- hierarchy 全部成功后才记录实际 Notebook ID；
- 删除 active lease 文件来解除冲突；
- consumer miss 时自动调用 human bootstrap。

## 适用边界

本文解释的是 fixture cache consumer 在 OneNote COM 环境中建立 live identity、分类失败和管理 lease 的工程经验，不定义 cache schema、CLI 或当前状态机的完整契约。当前实现以 [`../design/architecture.md`](../design/architecture.md) 为准，人工验证授权、参数和操作流程以 [`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) 与 [`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md) 为准，未完成的扩展仍以 [`../todo/014_recipe_fixture_validation_and_local_notebook_cache.md`](../todo/014_recipe_fixture_validation_and_local_notebook_cache.md) 为准。
