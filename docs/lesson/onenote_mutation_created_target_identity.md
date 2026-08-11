# OneNote mutation 的 created target 不能由 friendly path 代表

## 结论与边界

在 OneNote 允许同一 Section 内存在重名 Page 的前提下，标题和由标题组成的 friendly path 只能帮助发现候选对象，不能证明一次 create/copy mutation 实际分配了哪个对象。只要后续还要写正文、调整层级或删除源对象，目标身份必须从 COM 返回的 allocated ID 开始，并以类型、父级、活动状态和本次操作前的 ID 集合回读确认；path 兼容回退只有在候选唯一且可证明为本次新增对象时才安全。

本文解释这条工程经验，不定义当前工具的完整响应或错误结构。公开 Create/Copy/Move 合同以 [`tool_contracts.md`](../design/tool_contracts.md) 为准，对象身份模型以 [`object_model.md`](../design/object_model.md) 为准，人工验证流程以 [`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) 和 [`manual_validation/README.md`](../../tests/manual_validation/README.md) 为准。

## 2026-08-11 真实观察

一次由用户显式启动的隔离 Page Copy 六 case 验证中，root-only case 成功创建并回读了新 Page；随后 subtree case 为复制根分配了新 ID，却把源子页识别成了复制子页。源子页与计划创建的子页标题相同，旧实现又在 hierarchy 顺序中接受第一个相同 friendly path，因此后续正文写入与层级调整触及了源对象。严格 Copy verifier 返回 partial failure 并阻止了后续 case，working bundle 按失败语义保留，immutable template 未被回写。

修复 created-target 选择后，用户在同一 OneNote/Office/Windows 环境中连续两次完成当时的同 Section、跨 Section、跨 Notebook六 case 矩阵；每个 Copy 均通过内容与拓扑回读，subtree target 不再复用 source child ID。该对照支持“错误来自 path-first created-target 定位，allocated-ID-first 修复了当时复现”的有限结论。它不证明所有 OneNote 版本都以相同方式排序重名 Page，也不替代增强后 destination-anchor 场景尚待取得的真实证据；后者继续由 [`TODO 015`](../todo/015_mutation_target_identity_hardening_and_duplicate_page_regression.md) 跟踪。

后续 v4 destination-anchor 真实复验又给出两条独立经验。第一次运行中，前三个 Copy 已返回 fresh、verified、lossless target，两个 anchors 也通过 live validation，但 runner 因一个无关 Description Page 和源 Parent 的稳定 hash 改变而停止。保存的 before/after 证据显示内容对象与能力投影相同；只读结构诊断进一步证明 Parent 的 canonical 差异恰好是 OneNote 后补的空 `<T selected=...>`。因此当前设计只忽略这种内容为空、没有子节点且只携带 selection 属性的视图占位，并将“不变”证明绑定到 manifest 指定的 source/anchors；它不把普通空 T、可见文本、格式或对象身份降级为可忽略。

第二次运行在修复上述 comparator 后通过两个同 Section case，却在跨 Section root-only 的 strict Copy read-back 中发现目标标题拼接了原标题。保存的行为链说明，同一个空 selection T 位于真实标题 T 之前；转换旧顺序先剥掉 `selected`，再把第一个 T 改成目标标题，于是空占位变成目标标题、真实标题仍留在 payload。当前转换在剥 volatile 属性和改标题之前删除且只删除严格的空 selection T；普通空 T 和带可见内容的 selected T 仍保留。

第三次运行在该转换修复后让增强后的六个 case 全部通过 strict/semantic fidelity、fresh identity、目标拓扑和 source/anchor 不变门，也实际清理了全部复制目标。runner 最后仍因无关 Description Page 的稳定文本 hash 后台变化拒绝 restored snapshot；保存证据则显示全部原对象 identity/topology、全部 Page 对象身份与能力投影、四个保护页稳定内容都已恢复。因此 Page Copy 的最终恢复门现在沿用相同证据边界，不再把非验收页重序列化当成业务损坏。

第四次也是最终运行在该恢复门下顶层成功：六个 case 生成的 9 个 target 全部 fresh、互异并与 source/anchors 不相交，三个 subtree child 都保持 fresh root 下的 level 2，全部 Copy verified/lossless；随后 9 个 target 被精确非永久清理，bundle restored，双 Notebook closed，immutable template inventories unchanged。该单 run 与增强后的 Create/Move 成功证据共同闭合了 TODO 015，但结论仍限定于本次 OneNote/Office/Windows 环境，不外推为所有版本的排序或序列化保证。

## 错误假设为什么危险

“刚创建的对象可以按预期路径找回来”隐含了 path 唯一，但 Page title 并不具备这一约束。即使路径在 mutation 前唯一，重试、并发变化、后端 ID 重映射或已有同名对象也会使 first match 与本次 allocated object 分离。读取首个同名对象有时只是展示歧义；在 mutation 链中，它会把歧义升级为对错误对象的正文写入、拓扑修改，甚至错误的 Move 源删除判断。

另一个错误假设是“只要最终 verifier 失败，partial response 就能默认声称 source untouched”。Verifier 能证明最终结果不可信，却不能倒推之前没有执行写入。可靠 evidence 必须按实际阶段分别记录 allocated、resolved、正文写入、拓扑写入和源删除状态；未知状态应明确保留为未知并要求人工恢复。

## 可复用的设计影响

1. allocated ID 是 create/open 返回后的第一身份来源；命中该 ID 后仍必须验证资源类型、目标父级、活动/回收站状态和预期 friendly path。
2. allocated ID 不可见时，path fallback 不是任选一个候选，而是对本次操作前后集合做有界、类型化的唯一新增证明；零候选可重试，多候选应 fail closed。
3. Copy 中每个 source 必须映射到一个 fresh、互异的 target；source/target disjoint、target uniqueness、type 和 parent 检查都应发生在 Page 正文写入和拓扑重排前。
4. Move 的源删除门必须晚于完整 target identity、内容、拓扑和 source-current 验证；任何 alias、歧义或未跟踪 allocated object 都只能保留为 copy-only/partial failure。
5. 重名回归 fixture 需要用 manifest-bound ID 和独立正文 hash 证明 anchor 未被覆盖；不能再用同一个 path 去定位要保护的 anchor。

pytest 和 `--dry-run` 已用于证明上述 fail-closed 分支、响应 evidence 和场景编排合同，但它们不构成真实 OneNote 行为证据。增强后的真实复验状态仍以 [`TODO 015`](../todo/015_mutation_target_identity_hardening_and_duplicate_page_regression.md) 为准。
