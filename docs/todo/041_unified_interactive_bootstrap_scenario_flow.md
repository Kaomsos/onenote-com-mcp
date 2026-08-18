# 041：统一 Interactive Bootstrap 与 Scenario 验证流程

> ID：041
> 状态：待办
> 优先级：P1
> 类型：Manual Validation / Interactive Fixture / CLI UX / Lifecycle
> 更新日期：2026-08-18

## 当前问题

当前 UserAuthored / Interactive 验证把 `bootstrap-<operation>-fixture` 与
`interactive-<operation>` 暴露为两个用户入口。用户必须先运行 bootstrap、手工注入内容并发布
cache template，再复制 instance 信息运行第二条 interactive 命令。双命令流程会中断一次完整验收的
上下文，增加选错 fingerprint / instance、遗漏参数和误判阶段的概率。

当前通用阶段展示也沿用普通 scenario 的
`notebook -> fixture -> scenario -> report -> lifecycle`，没有准确表达 Interactive 场景中“先由用户
创作并冻结模板，再重新打开独立 working copy执行测试案例”的真实生命周期。

本 TODO 只优化 human-gated manual-validation 的入口与编排体验。它不改变生产 Tool、Copy/Move
lossless gate、mutation policy、cache immutability，以及真实运行只能由用户本人前台启动等安全边界。

## 目标体验

公开入口统一为 `interactive-<operation>`；`bootstrap-<operation>-fixture` 不再作为需要用户单独调用的
公开命令。一次 interactive 运行根据 cache 参数选择以下路径：

```text
不带 --use-cache
notebook
-> bootstrap（创建 authoring zone、等待用户注入内容并确认、冻结并发布 template）
-> fixture（从刚发布的 immutable template 重新打开独立 working copy并重绑 live ID）
-> scenario（执行一次目标测试案例）
-> report
-> lifecycle

带 --use-cache
notebook / cache selection
-> fixture（直接 materialize 已有 ready template）
-> scenario
-> report
-> lifecycle
```

具体要求：

1. `interactive-<operation>` 成为用户唯一入口；bootstrap 保留为内部可复用阶段，而不是第二个公开
   scenario/命令。
2. 不带 `--use-cache` 时自动进入 interactive bootstrap；用户在精确 authoring zone 注入内容并完成
   run-bound 确认后，同一前台进程自动发布模板、materialize working copy，并继续执行 scenario，
   不要求用户复制 instance ID 或再输入另一条命令。
3. 用户确认属于 `bootstrap` 阶段；`fixture` 阶段表示模板发布后重新打开物理独立 working copy、完成
   typed hierarchy 重绑与 live validation；测试 Tool 调用只发生在 `scenario` 阶段。
4. 带 `--use-cache` 时跳过 bootstrap，只允许选择并消费已经验证为 ready、mutation-eligible 且与当前
   recipe fingerprint 匹配的 immutable template；cache miss、歧义、evidence-only 或 invalid entry 继续
   fail closed，不隐式退回 fresh authoring。

## 设计约束

- 保持扁平公开 CLI：每个 `interactive-<operation>` 仍是一个完整隔离 suite；不得重新引入公开
  `bootstrap`、`validate`、`fixture` 等 helper action。
- 一次运行最多启动一个 scenario MCP child process。Bootstrap fixture 创建、cache 发布、working copy
  materialization 与 scenario handoff 必须继续遵守既有最小权限和单 MCP 边界。
- Fresh 路径必须先精确关闭 authored Notebook bundle，证明 template inventory 与 frozen identity，再
  opaque 发布 immutable template；scenario 绝不能直接在用户刚编辑的 authored bundle 上执行。
- 重新打开的 working copy必须拥有新的 run-scoped 路径和 live OneNote ID，完成完整 role set 的 typed
  rebind、连续稳定观察、内容真实性复核与一次性 handoff 后才能 mutation。
- `--use-cache` 只控制是否复用 ready template，不得放宽 instance、fingerprint、capability、policy、
  lifecycle 或 lossless 校验。
- EOF、用户拒绝、确认超时、发布失败、materialization/rebind 失败或 scenario 失败均须保留 content-free
  evidence，默认精确关闭本次 lease；不得自动删除 working files、失败现场或 cache template。
- `--dry-run` 必须零 stdin、零 cache 读写、零目录创建、零 OneNote/MCP side effect，并能展示 fresh 与
  `--use-cache` 两条确定性阶段计划。
- `all` 不得自动纳入 Interactive 场景；合并入口不扩大真实批处理资格。

## 实施范围

- 重构 Interactive scenario / UserAuthored recipe 编排，使 fresh authoring、template publication、
  materialization、scenario execution 在同一 run checkpoint 中串联。
- 从 registry、parser、help、dry-run catalog 和 README 中移除独立 bootstrap 用户入口；保留必要的内部
  bootstrap 组件与可测试边界。
- 统一阶段状态、实时 progress、checkpoint、failure phase、report 和 lifecycle evidence 命名，确保输出
  顺序为 `notebook -> bootstrap -> fixture -> scenario -> report -> lifecycle`。
- 定义 fresh 流程自动选择本次刚发布 instance 的精确 handoff；定义 `--use-cache` 下显式或唯一 ready
  instance 的选择规则，避免“最近一次”或名称猜测。
- 迁移现有 Interactive Copy 与 Interactive Move 场景，避免只为某一个 operation 建立特例。

## 自动化合同

- Parser / registry 证明每个 operation 只有 `interactive-<operation>` 公开入口，旧 bootstrap 名称被明确
  拒绝或按一次性兼容决策处理，不形成长期双入口。
- Fresh dry-run 固定六阶段顺序和 bootstrap 用户确认位置；cache dry-run 固定跳过 bootstrap，并保持其他
  阶段顺序不变。
- Fresh 纯合同覆盖 authoring confirmation、ready/evidence-only 分类、全部 role close、immutable publish、
  自动 instance handoff、materialize/rebind、一次 scenario mutation 和最终 lifecycle。
- Cache 纯合同覆盖 ready hit、explicit/unique selection、miss、歧义、invalid、fingerprint mismatch，且证明
  任一失败都不会进入 bootstrap 或 mutation。
- 覆盖 bootstrap、fixture、scenario 各阶段失败的 checkpoint/finalization，证明无第二 MCP、无重复
  mutation、无 template 回写、无越界清理。
- README 中的真实命令、help、registered dry-run cases 和 parser catalog 保持一致；完整 pytest 与所有受
  影响命令的 `--dry-run --json` 通过。

## 真实验收

真实运行只能由用户本人在交互式前台终端执行：

1. 对至少一个 Interactive Copy 和一个 Interactive Move 场景运行不带 `--use-cache` 的单命令流程，
   确认用户只在 bootstrap 阶段输入内容/确认，随后自动进入独立 working copy 的 scenario。
2. 对同一 recipe 再运行带 `--use-cache` 的命令，确认完全跳过 authoring/bootstrap，并从 ready template
   materialize 新 working copy。
3. 核对两条路径的 progress、report、artifacts 与 lifecycle 阶段归属；确认 fresh authored bundle、cache
   template、scenario working bundle三者路径和 live identity 不混用。
4. 验证 cache miss/invalid/evidence-only 路径 fail closed，且不会悄悄要求用户创作或执行 mutation。

## 非目标

- 不改变生产 Copy/Move 实现、verification tier、lossless comparator 或 verified-Copy-before-delete 门限；
- 不允许 Agent、pytest、CI、hook、timer、watcher 或后台任务启动真实 Interactive/Bootstrap mutation；
- 不允许 `--use-cache` 自动选择不明确、过期或 evidence-only 的 template；
- 不在 authored Notebook bundle 上直接执行测试案例，也不省略 publish 后重新打开 working copy；
- 不把 Interactive 场景加入 `all`，不引入任意路径、用户业务 Notebook 或 raw XML 输入。

## 完成定义

- [ ] 所有 UserAuthored / Interactive operation 只暴露统一的 `interactive-<operation>` 用户入口；
- [ ] 不带 `--use-cache` 的一次命令完整串联 bootstrap、重新 materialize fixture 与 scenario；
- [ ] 带 `--use-cache` 时确定性跳过 bootstrap，只消费匹配的 ready immutable template；
- [ ] Progress、checkpoint、failure phase、report 与 lifecycle 统一呈现六阶段顺序，并正确归属用户确认；
- [ ] 自动化覆盖 fresh/cache、成功/拒绝/超时/发布失败/rebind 失败/scenario 失败及 fail-closed 边界；
- [ ] Manual Validation README、开发流程、help、dry-run catalog 与 AGENTS 安全约束同步；
- [ ] 用户完成至少一项 Interactive Copy 和一项 Interactive Move 的 fresh/cache 真实验收并确认体验；
- [ ] 聚焦测试、manual-validation 纯测试、完整 pytest 和相关 `--dry-run --json` 全部通过。

## 关联

- [TODO 004](004_interactive_copy_move_content_fidelity_validation.md)：既有 Interactive Copy 内容类型验收。
- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：immutable template cache 与隔离 working copy。
- [TODO 020](020_user_authored_fixture_development_scaffold.md)：UserAuthored fixture 通用脚手架。
- [TODO 026](026_manual_validation_progress_verbosity.md)：实时阶段进度与 verbosity 输出。
- [TODO 039](039_interactive_real_page_move_lossless_validation.md)：当前双入口暴露实际体验问题的 Interactive Move 验证。
- [Manual Validation Runner](../../tests/manual_validation/README.md)
- [OneNote mutation 隔离验证流程](../dev/isolated_mutation_validation.md)
