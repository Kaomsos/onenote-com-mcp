# 032：Hierarchy Navigation 人工验证

> ID：032
> 状态：已完成
> 优先级：P2
> 类型：Manual Validation / Read Contract
> 更新日期：2026-08-15

## 背景

生产对象模型同时保留两种 Page 关系：`parent_id/section_id` 表示 COM 容器父级，`parent_page_id/page_level` 表示由同一 Section 有序 Page 序列派生的缩进关系。`get_parent`、`get_path` 与 `get_tree` 已分别公开这两种视图，但此前没有一个具名真实场景把三者放在同一 fixture 中对照验证。

## 当前范围

- 新增 cache-capable、默认不进入 `all` 的 `hierarchy-navigation` 场景。
- Fixture 创建 Notebook → SectionGroup → 两个 Section，并在目标 Section 中建立有分支的 Page level 1/2/3 结构。
- `get_parent` 验证 Notebook/SectionGroup/Section 的容器父级，并确认缩进 Page 的 COM 容器父级仍是 Section。
- `get_path` 验证缩进 Page 的稳定祖先仅为 Notebook/SectionGroup/Section，不把派生 Page parent 混入容器路径。
- `get_tree` 验证 level 1/2/3、两个同级 child、grandchild 与 root sibling 被投影为精确缩进树，并验证 `max_depth=1` 边界。
- Fresh 与 `--use-cache` 共用同一个 Recipe validator；cache working copy 仍经过 typed ID rebind、双稳定和每 Page 单次内容验证。

Manual-validation 的设计原理见 [Scenario 与 Fixture 架构](../design/manual_validation_scenario_fixture_architecture.md)，新增场景的实现流程见 [缓存 Fixture 驱动的真实操作验证推荐实践](../dev/cached_fixture_operation_validation.md)。生产对象语义仍以 [对象模型](../design/object_model.md) 和 [工具合同](../design/tool_contracts.md) 为准。

## 自动化验证

- Recipe manifest、静态最小权限、cache identity 和 dry-run 注册有纯合同覆盖；
- Fixture validator 拒绝 Page level/parent、Section、container parent 或 Page 内容证据不一致；
- 模拟 runtime 覆盖三个 `get_parent` case、Page `get_path`、完整 `get_tree` 与 depth boundary；
- 错误的 Page child order 必须 fail closed；
- 聚焦测试、注册目录测试、完整 `pytest -q`、相关 `--dry-run --json` 与 `git diff --check` 通过。

## 真实验证（已完成）

真实运行由用户在可见 OneNote Desktop GUI 已启动时显式执行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py hierarchy-navigation
.venv\Scripts\python.exe tests\manual_validation\run.py hierarchy-navigation --use-cache
```

用户确认 fresh 与 `--use-cache` 两种运行均已完成，仓库保存的精确证据如下：

- fresh：`.local-validation/run-2026-08-14-22-28-34/`。`run-result.json` 记录 `cache.cache_mode=fresh`、run/scenario 均为 `passed`；scenario 记录三个 `get_parent` case、Page container-only `get_path`、完整缩进树和 `max_depth=1` 边界全部通过。Fixture validator 同时证明 Notebook/SectionGroup/两个 Section 的容器关系、Page level 1/2/3 分支树和每 Page 单次内容验证；最终 lifecycle 为 `closed_preserved`，`filesystem_deleted=false`。
- cache：`.local-validation/run-2026-08-14-22-30-49/`。`run-result.json` 记录 `cache.cache_mode=use_cache`、`decision=cold_build`、run/scenario 均为 `passed`；`cache-materialization.json`、`cache-structure-remap.json`、`cache-hierarchy-convergence.json` 与 `cache-template-immutability.json` 齐全。Fixture evidence 记录 `live_materialized_revalidation=true` 和 `scenario_before_snapshot_reused=true`，working copy 的 Notebook/结构 ID 重绑后，三个 parent case、container path、缩进树与 depth boundary 再次全部通过；template 未打开且保持不变，最终 working Notebook 同样为 `closed_preserved`、未删除文件。

两次成功 run 均使用独立 disposable working Notebook，且没有以 mock、dry-run 或 cache template 自身代替真实 working-copy 验证。

本次只完成独立场景的真实验收，没有收到将其加入 `all` 的显式批准；因此 `hierarchy-navigation.included_in_all` 继续保持 `False`。如后续需要批处理资格，应单独完成稳定性与权限审查。

## 完成定义

- [x] 实现、Recipe、静态权限、注册、证据和纯测试全部完成；
- [x] 聚焦与完整自动化测试、dry-run 和 diff 检查通过；
- [x] 用户确认 fresh 与 cache 两种真实运行均通过；
- [x] 已记录两份 run 证据；当前未批准加入 `all`，保持 `included_in_all=false`。

## 完成结论

`get_parent`、`get_path` 与 `get_tree` 在 fresh 和 cache working copy 上都得到一致的真实 OneNote 后端结果：Page 的 COM 容器父级/路径稳定停在 Section，而 `parent_page_id/page_level` 独立投影出精确的多层缩进树。实现、自动化与 HUMAN-GATED 证据均满足本 TODO 的完成定义，状态更新为“已完成”。
