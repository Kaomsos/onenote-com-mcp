# 054：Manual Validation fixture 创建与验证 backend 调用去重

> ID：054
> 状态：待办
> 优先级：P2
> 类型：性能 / Manual Validation / Fixture / Validation / backend call
> 更新日期：2026-08-21

## 目标

参照 [TODO 049](049_copy_move_backend_readback_call_deduplication.md)，审视 manual-validation 在 fixture 创建、materialization/live validation 与 scenario 验证阶段的 backend 调用。对同一阶段、同一已证明状态的重复 hierarchy、Page content 或其他 readback，尝试以类型化、作用域受限的内部 observation/snapshot 复用来去重，降低真实人工验证的等待时间。

优化必须先冻结 content-free 调用基线、read reason 与可省略依据；没有证据证明等价的读取仍保持独立。任何 mutation 后所需的 fresh live observation、双稳定门、fixture identity/materialization 验证、Copy/Move fidelity、失败证据、lifecycle 与 fail-closed 边界均不得削弱。

## 范围与边界

- 仅考虑单次 manual-validation run 内 fixture 创建和验证链路中可证明重复的 backend readback；不得引入跨 run、跨 template 或跨 tool call 的缓存。
- 复用对象必须类型化、具备明确阶段/作用域/epoch 或等价失效边界；状态改变后继续重新取得 live evidence。
- 保持现有 fixture recipe、immutable cache、opaque copy、cache shape fail-fast 与真实场景必须由用户前台启动的安全约束；本项不改变生产公开 Tool 或 mutation policy。
- Agent、pytest、CI、hook、timer、watcher 与后台任务不得借本项启动真实 OneNote scenario；自动化只覆盖纯合同与 `--dry-run`。

## 完成定义

- [ ] 已为受影响的 fixture 创建与验证路径建立 content-free backend-call/read-reason 基线，并明确哪些重复读取可安全合并；
- [ ] 可证明等价的读取仅在同一有效状态内通过类型化内部 observation/snapshot 复用，mutation 后必要的 fresh 验证保持不变；
- [ ] 自动化合同覆盖正常、stale、failure/partial 与 cache/fresh 边界，且不降低 fixture identity、内容保真、lifecycle 或 fail-closed 保护；
- [ ] 用户在 disposable 本地 OneNote 场景中确认调用数或等待时间按预期改善，验证结果和安全边界未退化。

## 关联

- [TODO 049](049_copy_move_backend_readback_call_deduplication.md)：生产 Copy/Move shared-service readback 去重的参考目标。
- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：immutable fixture template cache 与隔离 working copy。
- [TODO 030](030_manual_validation_cache_hierarchy_activation_batching.md)：cache hierarchy activation batching 与验证证据复用。
- [TODO 052](052_nested_section_cache_crash_investigation.md)：嵌套 Section cache materialization 的 fail-fast 与安全调查不得被本项绕过。
- [Manual Validation Scenario 与 Fixture 架构](../design/manual_validation_scenario_fixture_architecture.md)。
