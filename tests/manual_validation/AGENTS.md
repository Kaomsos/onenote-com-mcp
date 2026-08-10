# 人工验证指令 — HUMAN-GATED

本目录包含能够执行真实 OneNote mutation 的代码。这些规则比父级测试规则更严格，不能由 scenario 代码、文档或便利性需求放宽。

## 智能体执行边界

- 智能体绝不能执行真实的 `run.py <scenario>` 或 `run.py all`。此禁令适用于前台工作、后台智能体、CI、hook、install/package 脚本、import、timer、watcher 和间接 helper 命令。
- 用户此前执行过真实运行，或授予了编辑仓库的一般权限，都不代表智能体获得再次启动真实运行的授权。需要真实证据时，将精确命令交给用户。
- 智能体可以修改代码和文档、以只读方式检查现有 artifact、运行纯合同测试、编译或 import 模块，以及运行明确包含 `--dry-run` 的命令。
- 除非用户亲自运行 scenario 并提供或确认证据，否则绝不能报告真实后端 scenario 已通过。

允许的智能体验证包括：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> --dry-run
.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run
```

## 场景与注册表架构

- 公开 CLI 是扁平的：每个 `run.py <scenario>` 都是完整隔离 suite。不得重新引入 `validate`、`suite`、`inspect`、`read`、`report` 或其他公开 helper action。`all` 是唯一批处理例外。
- `scenarios/` 下的每个可执行模块恰好定义一个具名 `Scenario` 子类。共享依赖放在 `scenarios/common/`；明显属于基础设施的 base 和 `__init__.py` 可以保留在 scenario 根目录。
- 使用 `@SCENARIO_REGISTRY.register` 装饰公开 scenario 类，然后在 `scenarios/__init__.py` 的显式有序清单中 import 它们。`scenarios/common/registry.py` 持有唯一 registry 对象，并且不得 import 具体 scenario。
- 新的探索性或仅用于验证的 scenario 默认设置 `included_in_all = False`。只有经过显式稳定性和权限审查后，才能将其纳入 `all`；filesystem discovery 绝不能自动纳入它。该资格只控制真实 `all` 批处理，与 dry-run pytest 收集资格无关。
- 每个公开 Scenario 必须显式拥有一个 fixture recipe，并自动提供至少一个稳定 ID 的注册 dry-run case。Recipe 由 Scenario 模块显式 import；不得新增 fixture registry、dry-run scenario 列表或 filesystem discovery。
- `all` 将已注册 scenario 作为相互独立的子命令串行启动。Scenario 之间不得共享 run directory、Notebook、MCP process、policy、fixture、evidence 或 lifecycle。

## 隔离、权限和生命周期

- 每个真实 scenario 都创建全新的 disposable source Notebook 和全新或空的 evidence directory。Notebook 名称冲突或非空 run directory 必须被拒绝。
- 一个 scenario 最多启动一个 MCP child process。其静态 spec 只能包含该 scenario 的 fixture、mutation、evidence read 和 restore/cleanup 所必需的完整最小权限闭包。
- Fixture 创建前，通过一次 `health_check` 核对精确的 policy、tool allowlist、timeout 和适用的 Copy budget。绝不能合并不同 scenario 的权限，也不能在启动后扩权。
- Source Notebook 的 create/get/close 只属于窄 lifecycle wrapper，并受精确 ID/name/path lease 约束。Fixture 创建必须留在 scenario MCP process 内。
- 使用绑定到 manifest 的精确 ID 和最新 confirmation field。可恢复操作默认必须 restore 并验证状态。每个具名 scenario 都可在用户显式传入 `--keep-worksite` 时，于 after/read-back 验证后跳过其契约内 restore/cleanup 并保留动作现场；本来就不可恢复的 scenario 则保留其既定最终状态。该模式必须保持源 Notebook 打开，在 evidence 中记录全部精确目标 ID 和人工清理要求，且不得由 `all` 透传。不可恢复操作只能触及 manifest allowlist 中的 disposable target。
- Delete scenario 必须保持非永久删除。绝不能删除本地 Notebook 或 Copy directory。出现 mutation 失败、`copy_only`、restore 失败、fidelity 失败或状态不确定时，应以非零状态退出、保持 source Notebook 打开并保留全部 evidence。
- Move 必须保持严格：`copy_only`、source 未删除或 fidelity gate 失败都不算成功，不得跳过或降级处理。

## 变更要求

- 任何新增或修改的非只读生产 tool，都必须具备具名 scenario、静态 policy/allowlist、隔离 fixture、before/after evidence、失败 handoff，以及 `README.md` 中记录的精确用户命令。
- CLI、lifecycle、permission、registry 或 evidence 行为变化时，同步维护本文件、manual-validation README、相关开发文档和合同测试。
- Dry-run 不得创建目录、启动 MCP 或访问 OneNote。它必须展示最终名称和路径、有序阶段、权限、allowlist、budget 及 lifecycle plan。
- 注册 dry-run case 只能包含冻结的声明式参数。测试 harness 独占 `--dry-run --json --run-dir`，使用正式 parser 和纯 plan builder，并以 sentinel 拒绝 MCP、lifecycle、bridge、subprocess 和目录副作用；README 的带标记代码块只能与 catalog 比较，绝不能执行。
- `--keep-worksite` 是所有具名 scenario 的公共、默认关闭选项，但不得属于或由特殊批处理入口 `all` 透传，也不得扩展任何 scenario 的 policy/tool allowlist 或改变失败保留语义。合同测试必须证明默认 restore/cleanup 仍执行、显式保留时跳过适用的 restore/cleanup、写入精确 `worksite.json` 且 source lifecycle 保持 open。
- 合同测试必须覆盖 parser/registry 行为、最小权限、redaction/content-free audit、lifecycle lease、失败现场保留、严格安全门限，以及 `all` 只运行显式纳入 scenario 的保证。
