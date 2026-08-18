# 手动验证框架

[English](../../en/dev-guide/manual-validation.md) | [文档首页](../../README.zh-CN.md)

自动化测试在 COM 边界处 mock，因此只能证明代码合同，不能证明真实 OneNote 行为。所以每个 mutation 能力都有第二层由人把关的验证：`tests/manual_validation/` 下的**具名真实后端 scenario**，在完全隔离的 disposable Notebook 中对真实 OneNote Desktop 执行。

本章向贡献者解释这个框架的设计。操作层面的权威是 [`tests/manual_validation/README.md`](../../../tests/manual_validation/README.md)，约束性规则是它的 [`AGENTS.md`](../../../tests/manual_validation/AGENTS.md)；内部架构见 [scenario/fixture 架构](../../../docs/design/manual_validation_scenario_fixture_architecture.md)。

## 为什么真实运行由人把关

真实 scenario 会 mutation 真实的 OneNote 后端。尽管每个 scenario 都隔离在 disposable 数据中，框架仍把"谁可以扣扳机"当作硬安全边界：

- **只有用户能启动真实运行。** Agent、pytest、CI、hook、package/install 脚本、import、timer、watcher 和后台任务绝不能执行真实的 `run.py <scenario>` 或 `run.py all`。
- 自动化可以修改验证代码、运行纯合同测试、只读检查已保存的证据，以及运行任何显式带 `--dry-run` 的命令。
- 只有用户本人运行并提供或确认证据后，结果才能报告为"真实后端通过"。Mock 和 dry-run 永远不充分。

这恰好防御了让智能体开发变得危险的失败模式：一个急切的自动化循环悄悄对用户真实应用状态行使写能力。

## Scenario 模型

公开 CLI 是扁平的：`run.py <scenario>` 就是一个完整的隔离 suite。没有辅助子命令；`all` 是唯一批处理入口，`clear` 是唯一 maintenance 分组。

```powershell
# 先检查计划——dry-run 不创建目录、不启动 MCP、绝不接触 OneNote
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json

# 真实运行：仅限用户
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

每次真实 scenario 运行都是一个完整隔离闭环：

```text
创建全新隔离 Notebook（窄 lifecycle wrapper）
→ 恰好启动一个 scenario 级 MCP 子进程
→ 只构建本 scenario 的 fixture，恰好运行所选 scenario
→ 写入本地证据报告
→ 关闭精确 lease 的 Notebook（默认）或按请求保持打开
```

关键性质：

- **Registry 驱动。** 每个公开 scenario 是一个具名 `Scenario` 类，注册到唯一的 `SCENARIO_REGISTRY`；`scenarios/__init__.py` 是显式有序清单。没有 filesystem discovery。纳入 `all` 批处理（`included_in_all`）是逐 scenario 显式审查后的决定。
- **静态最小权限。** 每个 scenario 最多启动一个 MCP 子进程，policy 和工具 allowlist 冻结——只包含其 fixture、mutation、证据回读和 restore/cleanup 所需的最小闭包。权限在创建 fixture 前通过 `health_check` 核对，启动后绝不扩权。不同 scenario 的权限绝不合并。
- **Disposable fixture、精确 ID。** 每次运行创建全新的 run-scoped Notebook bundle（绝不用用户数据），每个 mutation 都按精确对象 ID 加 confirmation 字段定位，只做有界工作。
- **Before/after 证据。** Scenario 在 `.local-validation/run-<timestamp>/` 下捕获 before snapshot、mutation 响应、after snapshot、restore 证明和 content-free 审计。失败时 fail closed：不再执行后续 mutation，证据保留，默认精确关闭 lease 的 Notebook。
- **默认恢复。** 可恢复操作默认 restore 并验证原始状态。删除保持非永久。`--keep-worksite`（显式、默认关闭）在验证通过后保留操作现场供人工 UI 检查，并记录精确 ID 和人工清理说明。

## Fixture Recipe 与模板缓存

每个 scenario 恰好拥有一个 **fixture recipe**——对所需 Notebook 结构和内容的声明式、带 fingerprint 的描述。Fresh 运行实时构建 fixture 并在 mutation 前完成验证。

由于复杂 fixture 重建昂贵，存在一个显式 opt-in 的缓存：`--use-cache` 从已关闭、不可变、先前已验证的 template（不解析内容的 opaque byte-for-byte copy——template 本身永不被打开）materialize 一份全新 working copy。Materialized copy 在任何 mutation 前重新做 live 验证。不传 `--use-cache` 时，scenario 执行零 cache 操作。

交互式 scenario（`interactive-<operation>`）为必须人工创作的内容（墨迹、形状、媒体录制）扩展了这一机制：fresh 运行串联一个 HUMAN-GATED 的 bootstrap 阶段，由用户创作合成内容并给出 run-bound verdict，随后发布已验证的 template 供后续缓存复用。

## `all` 与 `clear`

- `run.py all` 把显式纳入的 scenario 作为完全独立的子命令串行启动——不共享 run 目录、Notebook、MCP 进程、policy、fixture 或证据。子任务失败后，只有该子任务证明其全部 Notebook lease 已精确关闭，批处理才会继续。
- `run.py clear runs|cache|all` 是删除历史验证 artifact 和 cache entry 的唯一 maintenance 入口。真实执行只能交互式进行：必须由用户在前台终端启动，并在提示中现场输入动作绑定确认值（没有 `--confirm` 参数；非交互 stdin 被拒绝）。删除仅限固定 `.local-validation/` 根下有精确 ownership 的 payload，由 ownership metadata、containment 检查、reparse point 拒绝、当前 OneNote 已打开路径快照和逐目标 receipt 把关。自动化只能运行其 `--dry-run`。

## 贡献者必须做什么

任何新增或修改的非只读生产工具，都要求在同一变更中提供：

1. 自动化合同覆盖（mock/隔离），以及
2. 此处的具名 scenario：静态 policy/allowlist、隔离 fixture、before/after 证据、失败 handoff，并在 manual-validation README 中记录精确的用户命令。

然后把真实命令交给用户。绝不要自己运行，也绝不要在缺少用户确认的真实证据时把工作标记为完成。
