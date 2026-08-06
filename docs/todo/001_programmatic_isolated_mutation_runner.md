# 001：本地程序化 OneNote 隔离验证 Runner

> ID：001
> 状态：进行中
> 优先级：P1
> 类型：开发基础设施
> 更新日期：2026-08-06
> 触发边界：只能由用户在终端显式运行，不进入 CI、hook、自动化或默认测试

## 目标

通过唯一入口 `tests/manual_validation/run.py` 自动完成真实 OneNote mutation 的隔离准备、最小权限 MCP 调用、精确 ID/确认字段、before/after/restore 证据和报告。

每个扁平的 `run.py <scenario>` 自身就是一次完整 suite：创建全新 disposable Notebook、准备 fixture、只运行所选 scenario、生成报告，然后默认关闭源 Notebook或按 `--keep-notebook` 保持打开。`create` 是正式的 fixture-only scenario；`validate` 和诊断辅助 action 均不公开。特殊 `run.py all` 只负责串行启动显式注册的稳定测试 scenario，不拥有共享 run-dir、Notebook、MCP、权限或 lifecycle；未来新增的探索性/验证性 scenario 默认不注册，用户仍可单独启动。

Runner 不使用 Codex、LLM 或远程服务，但会执行真实 OneNote COM mutation，因此 Agent、pytest、CI、hook、timer、watcher 和后台任务不得运行真实命令。Agent 只能修改实现、运行纯合同测试并把命令交给用户。

## 文件边界

```text
tests/manual_validation/
├─ run.py                 唯一用户入口
├─ runner.py              仅 CLI 启动、分发和顶层错误处理
├─ runtime.py             共享 exit code、异常和 runtime options
├─ test_utils.py          快照、manifest、证据和不变量工具
├─ all_scenarios.py       特殊 all 串行子进程编排
├─ mcp_stdio_client.py    MCP 生命周期与 call_tool adapter
├─ lifecycle.py           仅源 Notebook create/get/close 与精确 lease
├─ scenarios/
│  ├─ base.py             Scenario 类合同与统一 CLI/lifecycle 参数
│  ├─ <scenario>.py       每个可执行模块恰好一个具名 Scenario 子类
│  └─ common/             registry、orchestrator、spec、fixture、报告与 Copy 等共享依赖
├─ tests/                 不访问 OneNote 的合同测试
└─ README.md              权威使用说明
```

不要为 mutation 创建独立脚本；`all` 是唯一允许的一次运行多个 mutation scenario 的入口，并且只能串行调用完整的独立场景命令。

## 当前 CLI 契约

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> `
  [--notebook-name <name>] `
  [--run-dir <path>] `
  [--keep-notebook] `
  [--timeout <seconds>] `
  [--dry-run] `
  [--json]
```

当前 scenario：`create`、`rename`、`reorder`、`move`、`delete`、`copy-page`、`copy-section`、`copy-section-group`、`copy-notebook`、`reconstructive-move-page`。

特殊批量入口：`run.py all [--timeout <seconds>] [--dry-run] [--json] [--verbosity quiet|normal|verbose]`。它只读取 `SCENARIO_REGISTRY` 中 `registered_for_all=True` 的类实例，不支持 `--run-dir`，默认 quiet，仅输出进度、错误和失败。

- 默认 Notebook：`__LOCAL_MCP_TEST_ISOLATED__<UTC_TIMESTAMP>`。
- 默认目录：`.local-validation\run-<同一 UTC_TIMESTAMP>`。
- `run-dir` 必须不存在或为空；同名 Notebook 已存在时拒绝复用。
- 命令顺序是 `lifecycle create → 唯一 MCP 内的场景 fixture + 当前 scenario → report → lifecycle close/keep`；`create` scenario 在唯一 MCP 内只执行一次完整预设 fixture，不执行额外 mutation。
- `delete` 自动选择本次 manifest 中的 `disposable_group`，不接受外部目标 ID。
- 默认 close 只关闭 Notebook；源与 Copy 文件夹始终保留，不实现自动文件删除。
- 任一步失败立即停止，源 Notebook 保持打开；close 失败作为恢复失败处理。
- `reconstructive-move-page` 始终严格运行。空保真 allowlist 导致的 `copy_only`、源未删除或保真门失败必须非零退出，不得跳过或降级。

## 权限与证据要求

每个 scenario 最多启动一个 MCP 子进程。它使用固定的场景级完整闭包 policy 和 tool allowlist，在 fixture 前通过 `health_check` 核对，并在同一进程完成最小 fixture、当前 mutation、证据读取和 restore/cleanup；不得跨 scenario 使用权限并集或运行时扩权。源 Notebook create/get/close 由窄 lifecycle wrapper 依照精确 ID/name/path lease 完成，不启动额外 MCP，也不得创建 Section、Page 或内容 fixture。

真实 mutation 使用 manifest 中的精确 ID 和最新 name/title、parent、modified 确认字段。可恢复操作必须恢复并回读；Delete 仅作用于 manifest allowlist 中的 disposable target，并保持非永久删除。Copy 和重建式 Move 的失败证据必须保留 `outcome`、`created_ids` 与 `id_map`。

典型输出包括 `run-state.json`、`run-result.json`/`run-failure.json`、`manifest.json`、`prepared.json`、`fixture-result.json`、`lifecycle-lease.json`、`lifecycle.json`、`run-metrics.json`、`report.md` 和 `scenarios/<scenario>/` 下的 before/plan/copy-result/after/restored/result/failure artifacts。

## 已完成

- [x] 统一入口、stdio client、manifest、快照、报告和日志脱敏；
- [x] 全部 P1/P2 具名 scenario 的静态最小权限与 tool allowlist；
- [x] 每个扁平 `run.py <scenario>` 自动创建全新 Notebook 和 fixture；
- [x] dry-run 无目录、无 MCP、无 OneNote，并展示完整闭环、权限和 Copy 预算；
- [x] 默认精确 ID close、`--keep-notebook` 和本地文件永久保留；
- [x] Delete 自动绑定 disposable group；
- [x] 重建式 Move 严格失败门禁与失败交接；
- [x] Agent/CI/hook/timer/watcher 禁令；
- [x] 不访问 OneNote 的合同测试覆盖默认值、run-dir、同名冲突、顺序、失败停止、close/keep 与严格 `copy_only`。
- [x] `runner.py` 启动职责与 runtime/test utils 分离，`all` 串行入口覆盖 quiet、verbosity、失败继续和参数透传合同。
- [x] 可执行 scenario 类化；`scenarios/__init__.py` 导入公开类并触发 wrapper 注册，parser、dispatch、静态 spec 与 `all` 资格由单一 `SCENARIO_REGISTRY` 对象管理；未注册的验证性 scenario 不会进入 `all`。

## 待用户验收

代码与合同测试完成后，真实后端状态仍保持“未确认”，直到用户本人完成：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

其他 scenario 分别重复同样流程。验收时确认 `report.md`、成功 close/keep 状态，或严格场景的预期非零失败与保留现场。用户确认真实 OneNote 运行后，再更新本 TODO 状态和索引。
