# 006：Typed Section 与 SectionGroup Reorder

> ID：006
> 状态：已完成
> 优先级：P1
> 类型：公开 mutation 契约 / 真实后端验证
> 更新日期：2026-08-10

## 背景与范围

本 TODO 最初评估 Section 与 SectionGroup 的 typed 同父级 Reorder。Reorder 只允许对象在原父级内保持 ID 地改变兄弟顺序，不包含跨父级 Move，也不得用 Rename、Copy/Delete、raw XML 或直接编辑 `.one` 文件模拟。

自动化合同和 human-gated 场景曾同时覆盖两类容器；最终产品结论必须以用户触发的真实 OneNote 隔离证据为准，不能从 mock、dry-run 或 `UpdateHierarchy` 返回成功推导后端实际支持。

## 最终能力决策

### Section：保留 typed Reorder

`reorder_section` 只接受精确 ID、名称、父级和可选 `modified` confirmation；目标与 predecessor 必须是 Notebook 或 SectionGroup 同一父级下的 Section。实现使用完整直属 sibling XML，并在写后验证：

- Section ID、父级和 sibling ID 集合不变；
- 请求的 predecessor/顺序已生效；
- 所有 Page ID、顺序和稳定内容摘要不变；
- 任何父级变化、内容变化或回读不一致都 fail closed。

用户已在真实 OneNote UI 中确认 Section 的同父级排序成功。Runner 随后发现并修复了两类验证问题：非正文作者/时钟/选择/视图 XML 元数据的延迟补写，以及逐 Page 取证期间容器 `modified` 更新造成的 confirmation 过期。这些问题属于验证证据稳定性，不否定 UI 中已观察到的 Section 排序。

### SectionGroup：明确不支持并拒绝

产品契约不提供 SectionGroup Reorder。无论父级是 Notebook 还是 SectionGroup，请求都必须拒绝，且不得通过以下方式规避：

- 调整完整 hierarchy XML 中 SectionGroup 元素的先后顺序；
- Rename 后再改回名称；
- Copy/Delete 或重建对象；
- raw XML 或直接编辑 `.one` 文件。

原因是当前 OneNote 后端只暴露按名称固定升序的 SectionGroup 集合，没有可验证的可变 sibling order。`UpdateHierarchy(xs2013)` 对此类 XML 可以返回成功，但成功只表示调用被接受，不表示请求顺序被应用。

## 真实证据边界

2026-08-10，用户显式运行隔离场景 `reorder-section-group --keep-worksite`。fixture、精确目标 ID、父级 confirmation 和 mutation 前快照均成功；对 Notebook 直属 Group 发出的顺序请求为：

```text
请求前：01-Root-Group-A, 02-Root-Group-B, 03-Root-Group-C
请求后：01-Root-Group-A, 03-Root-Group-C, 02-Root-Group-B
实际回读：01-Root-Group-A, 02-Root-Group-B, 03-Root-Group-C
```

bridge 记录显示 `update_hierarchy` 返回成功，生产工具紧接着重新读取 hierarchy，并因未观察到请求顺序而返回：

```text
Reorder returned success, but the requested sibling order was not observed.
```

因此可以确认：

- 失败不是 Runner 后置校验误报，而是生产工具自身的写后验证拒绝；
- 失败不是旧 `modified`、错误父级、错误 predecessor 或 fixture 顺序造成；
- Notebook 直属 SectionGroup 的顺序请求被后端忽略，并保持固定名称升序；
- 三个 Group 共享一个 SectionGroup 父级的嵌套操作因根级操作先失败而没有执行，不能写成“嵌套场景已实测失败”。

尽管嵌套父级未被独立执行，产品层仍对两种父级统一拒绝：已观察到的根级失败和固定名称升序说明后端没有可交付的 SectionGroup reorder 原语；继续发起更多真实 mutation 不能建立安全、稳定的产品契约。

失败 worksite 与证据由 Runner 按 `--keep-worksite` 保留，不要求重复运行相同正向验收。

## 自动化与人工验证处置

- 保留 Section Reorder 的 schema、policy、同父级拒绝、bridge 失败和写后不变量自动化合同；
- 保留 `reorder-section` 的编号 fixture、Description、before/after/restored 和 `--keep-worksite` 流程；
- `reorder-section-group` 保留完整实现和单独 CLI 注册，明确标记为功能受限、真实验证失败，并显式设置 `included_in_all=False`；它只作为诊断探针和回归证据，不列为用户应执行的正向验收；
- `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP` 必须保持 `false`，不得解释为授权使用；
- Agent、pytest、CI、hook、timer 或 watcher 仍不得启动任何真实 OneNote mutation 场景。

## 完成定义

- 当前设计、README、验证指南和 TODO 索引明确区分 Section 支持与 SectionGroup 拒绝；
- 公开能力目录不再把 `reorder_section_group` 描述为待验证或可启用的实验能力；
- SectionGroup reorder 保持默认 fail closed，遗留实验开关不得解释为受支持能力；后续移除原型入口属于实现清理，不再阻塞本 TODO 的能力结论；
- Section Reorder 保持严格同父级、ID-preserving 和写后内容不变量验证；
- 不再要求用户重复运行已证明后端忽略请求的 SectionGroup 正向场景。

## 完成状态

本 TODO 已完成：Section Reorder 保留 typed、同父级、ID-preserving 契约，并已有用户确认的真实 UI 排序证据；SectionGroup Reorder 则依据真实后端负能力证据，以“不支持并拒绝”结束评估。公开能力目录和验证指南不再要求用户运行 SectionGroup 正向验收。

仓库中保留的早期 SectionGroup 实验 tool、policy 开关和 manual scenario 资产只属于遗留原型、独立诊断与历史证据；manual scenario 不进入 `all`，并以机器可读状态报告 `limited/failed`。这些资产默认 fail closed，不改变本 TODO 的最终能力结论，也不能重新把 SectionGroup reorder 描述为待验证或可启用能力。

[TODO 013](013_reparent_default_placement_contract.md) 后续允许 Reparent/Copy/Move 返回 SectionGroup 在后端固定名称排序中的观察索引。该索引不恢复本 TODO 已否定的 Reorder 能力，也不是可控落点保证。
