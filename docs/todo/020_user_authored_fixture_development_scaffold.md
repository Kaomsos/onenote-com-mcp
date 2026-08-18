# 020：UserAuthored Fixture 开发脚手架完整化

> ID：020
> 状态：待办
> 优先级：P3
> 类型：开发脚手架 / Manual Validation Fixture 复用
> 更新日期：2026-08-18

## 决策摘要

`UserAuthoredRecipe` 是帮助开发者在 disposable Notebook 的受控区域内自由制作复杂 fixture、冻结为显式实例并从 cache 复用的临时开发脚手架。它不是 Local OneNote MCP 的生产功能，也不直接验证 Copy、Move、Delete 或某种 Page 内容能力。

当前骨架已经足以服务现阶段的验证开发：统一 `interactive-user-authored-fixture` 入口、bounded authoring zone、reserved marker、内容能力分类、`authored-<digest>` 实例 ID、`ready/evidence_only` 状态、显式或唯一 ready 实例选择以及 immutable cache materialization 均已有实现和纯合同覆盖。继续补齐完整真实矩阵的收益较低，因此从 [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md) 的完成条件中剥离，单独作为低优先级事项维护。

本 TODO 不阻塞 TODO 014、生产 Copy/Move、具体 Interactive fixture comparator、静态内容 allowlist 或发布计划。现有 UserAuthored 命令保留为开发工具，但在本 TODO 完成前不得把当前骨架描述为已经获得完整 authoring-zone 与多实例真实后端验证。

## 当前可用范围

- `interactive-user-authored-fixture` 在 fresh 路径创建 disposable Notebook、系统 instructions/marker 和固定 authoring zone；
- 用户确认后可根据当前 snapshot 生成 content-free 能力分类与 `authored-<24 hex>` 实例 ID；
- 已知且投影完整的能力可分类为 `ready`，未知、缺投影或错误 schema 分类为 `evidence_only`；
- `interactive-user-authored-fixture --use-cache --template-instance-id ...` 只接受显式、格式正确的实例 ID，或在恰好存在一个 ready instance 时自动选择；不枚举、不猜测“最新”实例；
- `--use-cache` 路径拒绝 `evidence_only`，cache miss/invalid 返回 `interactive_cache_miss` 并提示同一命令移除 `--use-cache`；fresh 路径可发布 `evidence_only` 取证，但结果必须保持 mutation-ineligible；
- cache template 继续遵守关闭后 opaque copy、从不由 OneNote 打开、每次 materialize 新 working copy 和 live ID rebind 的共享安全合同；
- 相关 Scenario 不进入 `all`，Agent、pytest、CI、hook 或后台任务不得启动真实 fresh/cache Scenario。

这些能力足以让开发者制作和复用临时复杂 fixture，但不等于下述完整矩阵已经取得真实证据。

## 延期补齐的开发矩阵

### Authoring-zone 边界

- 证明 zone 内新增、删除、重命名和重排 Section/Page 能被完整捕获；
- 证明修改 reserved marker、role 身份或 zone 外对象时 fail closed；
- 失败时模板不发布、Notebook 保持打开，并保存精确越界 evidence；
- 不接受用户业务 Notebook、任意外部 Notebook ID/path 或运行后的 working copy作为模板来源。

### 冻结实例和多实例身份

- 同一 contract fingerprint 下冻结两个内容不同的实例并证明可并存；
- cache 路径必须按显式 `template_instance_id` 或唯一 ready、mutation-eligible instance 选择；格式错误、未知或歧义选择在 materialization/mutation 前拒绝；
- 冻结后的 projection digest、manifest 和 byte inventory 不随后续 working copy 修改而漂移；
- 不按名称、mtime、目录顺序或“最近实例”推断目标。

### `ready` 与 `evidence_only`

- 用真实用户创作分别取得一个全部能力可稳定验证的 `ready` 实例，以及一个含未知/未验证能力的 `evidence_only` 实例；
- `evidence_only` 必须保持 `mutation_eligible=false`、`move_source_deletion_allowed=false`，不能被普通 mutation consumer 使用；
- 人工 ACCEPT 不能覆盖未知能力、缺失 projection 或错误公开对象 schema；
- 如未来需要允许 evidence-only 取证，必须使用独立、只读取证的具名 Scenario，不得动态扩权。

### Cache、失效和 operation 隔离

- ready instance validated hit 必须重新打开完整 working hierarchy、完成 old→live ID rebind 和 live validation；
- working mutation 不改变冻结实例或 cache master inventory；
- UserAuthored entry 失效并完成精确受控清理后，cache 路径只能返回 `interactive_cache_miss`，由用户显式移除 `--use-cache` 进入 fresh authoring；
- 不得从旧 working copy自动修复、重新发布或覆盖 frozen template；
- active working lease、source 仍打开、ownership/containment 不完整或 cleanup failure 均 fail closed。

## 面向代码

主要涉及以下 manual-validation 开发基础设施，而不是生产 `src/` 服务：

- `tests/manual_validation/scenarios/fixture_recipes/interactive.py` 中的 `UserAuthoredRecipe` 分类、冻结和状态模型；
- `tests/manual_validation/scenarios/fixture_recipes/user_authored.py` 的 unified recipe；
- `tests/manual_validation/scenarios/interactive_user_authored_fixture.py`；
- `scenarios/common/interactive_bootstrap.py` 的 checkpoint、freeze、verdict 和发布交接；
- `scenarios/common/orchestrator.py` 与 `fixture_cache.py` 的显式 instance selection、state gate、materialization 和 invalidation；
- RecipeContractCase、dry-run catalog、纯合同和临时文件系统 cache 测试。

除非未来明确设计新的生产能力，本 TODO 不应修改生产 Copy comparator、Move `copy_contract_satisfied` 门、公开 MCP tool schema 或生产 mutation allowlist。

## 推荐实施顺序

1. 先补齐 authoring-zone 的 before/after 边界模型和负向纯测试；
2. 再实现两个 frozen instances 共存与精确选择的完整 cache 合同；
3. 固定 `ready/evidence_only` evidence schema 和只读/可 mutation cache-consumption 边界；
4. 覆盖失效、精确清理、interactive-cache-miss、active lease 和 template immutability；
5. 运行 manual-validation 纯测试、完整 pytest、所有相关 `--dry-run --json` 和 `git diff --check`；
6. 只有重新评估该脚手架价值后，才由用户本人运行真实矩阵。Agent 不运行真实 Scenario。

## 非目标

- 不把 UserAuthoredRecipe 公开为生产 MCP tool 或用户 Notebook 模板导入功能；
- 不缓存、复制或接管业务 Notebook；
- 不让自由创作绕过 synthetic-content、authoring-zone、最小权限或 unknown capability fail-closed；
- 不为任意 UserAuthored 内容自动授予 Copy lossless、Move 源删除或 Delete 权限；
- 不要求 TODO 014、TODO 004 或生产 Copy/Move 等待本脚手架完整化；
- 不直接解析、编辑或重写 `.one` 文件。

## 完成定义

- authoring-zone 内允许变更与 zone 外/reserved marker 负向分支均有纯合同和用户真实证据；
- 同一 fingerprint 下两个不同实例可共存，显式选择、缺失/错误/未知选择和冻结后不可漂移均通过；
- `ready` 与 `evidence_only` 各取得一次真实证据，状态、mutation eligibility 和 Move deletion eligibility 均 fail closed；
- ready cache validated hit、template immutability、active lease、失效后 interactive-cache-miss 和禁止 working-copy 修复均有真实证据；
- manual-validation README、开发指南和本 TODO 记录最终操作边界；
- 用户确认全部真实 evidence 后，本 TODO 才可标记为已完成。

## 关联

- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：共享的 immutable fixture cache、working-copy isolation 和具体 Interactive Recipe 基础；本 TODO 不属于其完成条件。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：当前已注册命令和 HUMAN-GATED 执行边界。
- [缓存 Fixture 驱动的操作验证推荐实践](../dev/cached_fixture_operation_validation.md)：复杂 fixture 的开发使用方式。
