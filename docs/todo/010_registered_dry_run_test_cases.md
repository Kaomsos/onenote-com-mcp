# 010：Manual Validation Dry-run 自动测试用例注册

> ID：010
> 状态：待办
> 优先级：P1
> 类型：验证架构 / 自动化合同与安全边界
> 更新日期：2026-08-10

## 背景与现状

人工验证文档目前使用 PowerShell 代码块展示具名场景的 dry-run 命令，例如：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
```

仓库已经具备一部分自动覆盖：`SCENARIO_REGISTRY` 是公开场景的权威注册表，`test_isolated_scenario_suite.py` 会对 `PUBLIC_SCENARIOS` 参数化并调用每个场景的默认 `--dry-run --json`。但是当前仍存在以下分散状态：

- 文档命令块不是结构化数据，无法证明代码块对应的参数组合确实被 pytest 收集；
- `--keep-worksite`、Rename target、Page level、能力受限场景和 `all --dry-run` 等变体由不同测试中的手写列表维护；
- `registered_for_all` 与 `get_registered_test_scenarios()` 实际表示“允许进入真实 `all` 批处理”，命名容易被误解为 dry-run 自动测试资格；
- 新增场景虽然会被默认全场景参数化覆盖，但没有统一机制声明额外 dry-run case、稳定 pytest ID、预期 policy/allowlist/lifecycle 或对应文档示例；
- dry-run 测试目前通过 CLI `main()` 进入共享 dispatch。现有早返回是安全的，但缺少一个从类型和测试 harness 层强制注入 `--dry-run`、拒绝危险参数并监视所有副作用入口的独立边界。

本 TODO 要把“文档中的 dry-run 命令背后的参数组合”注册为结构化 test case，使 pytest 自动发现和运行；Markdown 只作为人类可读视图，不作为可执行代码来源。

## 目标

- 每个公开 scenario 注册至少一个默认 dry-run test case；新增 scenario 后无需再编辑中央 pytest 参数列表即可自动进入默认测试集；
- scenario 可声明有限、具名的额外 case，例如 `keep-worksite`、合法的 scenario 专属参数或高风险 policy 变体；
- pytest 使用稳定 case ID 自动收集这些 case，并验证 plan、policy、tool allowlist、budget、lifecycle 和零副作用不变量；
- manual-validation README 中选定的 dry-run 代码块与已注册 case 建立可检查映射，避免命令、场景名和参数漂移；
- `all` 的真实批处理资格与 dry-run 测试资格彻底分离，探索性且 `registered_for_all=False` 的场景仍必须自动测试自己的 dry-run；
- 保持 HUMAN-GATED 边界：pytest、CI、hook、import、collection 和 test helper 在任何情况下都不能启动真实 OneNote scenario。

## 可行性评估

结论：**可行性高，实施风险中等，建议实施。**

有利条件：

- 每个公开场景已经由显式 import 和 `@SCENARIO_REGISTRY.register` 统一管理，没有依赖 filesystem discovery；
- `Scenario.runtime_spec()`、`ScenarioSpec` 和 `isolated_dry_run()` 已能提供静态 fixture、policy、allowlist、budget 与 lifecycle plan；
- 默认全场景 dry-run 参数化测试已经证明 in-process CLI 路径可在不创建 run directory、不启动 MCP 的情况下确定性运行；
- pytest 默认测试集已经加载 manual-validation 合同测试，因此注册后的 case 会自然进入现有自动化流程。

主要重构不是重新实现 dry-run，而是将“case 声明、plan 生成、CLI 渲染、pytest 参数和文档映射”收敛到同一数据源。预计不需要真实 OneNote 验证；所有实施验收都应由纯测试和显式 `--dry-run` 完成。

## 方案比较

| 方案 | 可行性 | 风险 | 结论 |
| --- | --- | --- | --- |
| 从 Markdown fenced code block 提取字符串并交给 shell/子进程执行 | 中 | 高：文档即代码、shell quoting、参数注入、漏写 `--dry-run`、平台差异 | 拒绝 |
| Registry 提供结构化 case，pytest 强制追加安全参数后通过 CLI `main()` 执行 | 高 | 中：仍需证明 shared dispatch 不会越过 dry-run 分支 | 作为 CLI 合同层 |
| 将 plan 生成提取为无 I/O 的纯函数，pytest 直接测试 | 高 | 低，但单独使用会漏掉 parser/dispatch 漂移 | 作为核心层 |
| 结构化 case + 纯 plan builder + 受守卫的 CLI round-trip | 高 | 低至中 | 推荐 |

## 建议契约

新增不可变的结构化声明，字段名称可在实施时微调，但安全属性必须保持：

```python
@dataclass(frozen=True)
class DryRunCase:
    case_id: str
    scenario_name: str
    scenario_args: tuple[str, ...] = ()
    expected: DryRunExpectations = DryRunExpectations()
    documentation_key: str | None = None
```

约束如下：

- `case_id` 在全 catalog 中唯一，并作为稳定 pytest ID；
- `scenario_args` 只允许 scenario 专属、安全且可枚举的参数，不允许包含 executable、scenario 名、`--dry-run`、`--json`、`--run-dir` 或能改变真实执行授权的参数；
- test harness 自己构造 argv，强制加入 `--dry-run --json` 和 pytest 临时 `--run-dir`，解析后再次断言 `args.dry_run is True`；调用方不能覆盖这些字段；
- `expected` 只保存声明式预期，不接受任意回调或 shell command，避免 test registration 本身成为副作用入口；
- `Scenario` 基类为每个公开场景提供默认 case，场景只在确有参数分支时追加有限变体；
- `all --dry-run` 是 runner 级特殊 case，可由同一 catalog 显式追加，但不得借此改变任何场景的 `registered_for_all` 状态。

## 建议代码重构

### 1. 分离三个注册概念

- `SCENARIO_REGISTRY.public_names`：公开具名 CLI 场景；
- `included_in_all`（迁移现有 `registered_for_all`）：仅控制用户显式执行 `run.py all` 时的真实批处理资格；
- `dry_run_cases`：纯自动化测试 catalog，覆盖全部公开场景，与 `all` 资格无关。

同时将含混的 `get_registered_test_scenarios()` 重命名为 `get_all_scenario_names()` 或等价名称，避免后续开发者误把真实批处理 allowlist 当成 pytest collection 列表。是否保留内部兼容别名应由引用扫描决定；不得为兼容而维持两个可漂移的权威列表。

### 2. 提取纯 dry-run plan builder

将 `isolated_dry_run()` 中的静态 plan 构造提取到不创建目录、不启动 MCP、不调用 lifecycle/bridge 的纯模块，例如 `scenarios/common/dry_run.py`：

- 输入为已解析的 scenario、`ScenarioSpec`、运行参数和逻辑路径；
- 输出为版本化、可序列化的 dry-run payload；
- CLI 的 `--dry-run` 分支和 pytest case runner 调用同一个 builder；
- 纯模块不得 import 能启动 MCP、COM 或 Notebook lifecycle 的执行器；若 policy 类型当前迫使其依赖执行模块，应先抽出只含数据的 policy/spec 定义。

真实执行 orchestrator 继续独立存在。不得把 `dry_run=False` 作为 pure builder 的可选开关，也不得提供会根据布尔值切换真实执行的统一 helper。

### 3. 建立受守卫的 pytest case runner

新增统一 helper，例如 `run_registered_dry_run_case(case, tmp_path)`：

1. 从 case 生成 argv，并无条件追加 `--dry-run --json`；
2. 使用正式 parser 解析，断言 command、case ID 映射和 `dry_run=True`；
3. 调用共享 pure builder，并对需要 CLI 覆盖的 case 执行一次 in-process `main(argv)` round-trip；
4. 将 MCP child start、OneNote lifecycle create/open/close、bridge invoke、`subprocess.run/Popen` 和 filesystem mutation 入口替换为一旦调用即失败的 sentinel；
5. 断言临时 `run-dir` 不存在，stdout 只有稳定 JSON，payload 明确包含 `server_started=false` 与 human-only 边界；
6. 使用共享 validators 检查 policy、allowlist、budget、ordered steps、restore/keep lifecycle 和 capability assessment。

禁止 test case 携带 Python callable、环境变量覆盖或任意命令行前缀。需要检查拒绝行为时，使用普通负向单元测试，不把危险参数注册为可执行 catalog case。

### 4. 自动 pytest 参数化

使用 registry 导出的不可变 tuple 直接参数化：

```python
DRY_RUN_CASES = SCENARIO_REGISTRY.dry_run_cases

@pytest.mark.parametrize("case", DRY_RUN_CASES, ids=lambda case: case.case_id)
def test_registered_manual_validation_dry_run(case, tmp_path):
    run_registered_dry_run_case(case, tmp_path)
```

Registry 注册时必须 fail closed：重复 ID、未知 scenario、空 case 集、禁止参数、spec/name 不一致或无法通过 parser 的 case 都使 import/collection 失败。继续使用 `scenarios/__init__.py` 的显式 import 清单；不得改成扫描 `scenarios/*.py` 自动发现，以免未审查模块意外进入 CLI 或测试。

### 5. 绑定文档代码块

README 中需要长期保留的 dry-run 示例使用稳定标记关联 `documentation_key`，例如：

````markdown
<!-- dry-run-case: reparent-page.default -->
```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page --dry-run --json
```
````

文档合同测试只解析带明确标记的 fenced block，将其规范化后与结构化 case 的渲染结果比较；它不执行 Markdown、不调用 shell，也不解析其他 PowerShell 示例。若嵌套 fence 的表现影响 Markdown，可改用单行标记加普通代码块，安全语义不变。

默认 case、`all --dry-run` 和文档中承诺的高价值变体应有映射；内部穷举 case 不要求全部展示在 README。Registry 是执行与测试的权威来源，文档块只是受检查的投影。

## 风险与缓解

### P0：误触真实 mutation

风险：case 漏写 `--dry-run`、参数覆盖、helper 复用真实 dispatch，导致 pytest/CI 启动 MCP 或访问 OneNote。

缓解：harness 独占安全参数；case schema 禁止相关 token；pure builder 不提供真实执行开关；sentinel 覆盖进程、bridge、lifecycle 和写目录入口；负向测试证明任何移除/覆盖 dry-run 的构造都会在执行前失败。

### P1：把 pytest 资格与 `all` 资格混为一谈

风险：为了自动测试而把探索性 scenario 设置为 `registered_for_all=True`，从而扩大用户批量真实 mutation 范围。

缓解：分离并重命名两种注册概念；合同测试明确断言 `registered_for_all=False` 的场景仍有 dry-run case，但不进入 `all`。

### P1：纯测试路径与真实 CLI dry-run 漂移

风险：只测试 pure builder，parser、dispatch 或 JSON 输出已经损坏却未被发现。

缓解：全部 case 经过正式 parser；默认 case 执行受守卫的 CLI round-trip。若运行时间可接受，全部 case 均执行 round-trip；否则至少按场景一个，额外变体直接测试 pure builder。

### P1：文档成为第二权威来源

风险：同时维护 Python case 与 Markdown 命令，或直接执行 Markdown，造成漂移与注入面。

缓解：只从 registry 渲染期望命令；文档测试比较带标记块，不执行块；未标记示例不参与 catalog。

### P2：case 数量膨胀和脆弱快照

风险：每个参数组合都注册，导致 collection 变慢、输出变更引发大面积 snapshot 更新。

缓解：每场景一个默认 case，只有权限、lifecycle 或 parser 分支不同的组合才新增；验证结构化 invariant，不保存整段 JSON golden snapshot。

### P2：import/collection 副作用

风险：为自动发现改用 filesystem scan，或在 class registration 时构造 runtime 对象、读取环境和路径。

缓解：保持显式 module import；case 为冻结数据；注册阶段只做纯校验；测试证明 import 和 collection 不创建目录、不读取 OneNote、不启动子进程。

### P2：dry-run 通过被误记为真实证据

风险：自动 case 全绿后错误地把真实 OneNote capability 标记为 passed。

缓解：pytest 名称、payload 和报告均使用 `dry_run_contract` 术语；capability assessment 只透传当前状态，不由 dry-run 修改；文档继续声明只有用户运行并确认真实 scenario 才构成后端证据。

## 实施步骤

1. 为 `DryRunCase`、期望 schema 和禁止参数规则补充纯单元测试；
2. 在 `Scenario`/registry 中接入默认 case、额外 case 和唯一性/完整性校验；
3. 将 `isolated_dry_run()` 的 plan 生成提取为无 I/O pure builder，保持现有 JSON 字段兼容；
4. 新增受守卫的统一 case runner，并将现有 `PUBLIC_SCENARIOS` 默认参数化测试迁移到 registry catalog；
5. 把手写的 `keep-worksite` 和场景专属 dry-run 参数列表收敛为 case 声明，共享结构化 validators；
6. 分离并重命名 `registered_for_all`/`get_registered_test_scenarios()` 相关内部概念，证明 `all` allowlist 没有扩大；
7. 为 `all --dry-run` 添加 runner 级 case，验证只包含显式纳入 `all` 的场景并始终向子命令传递 `--dry-run`；
8. 给 manual-validation README 中 canonical dry-run 示例增加 case 标记和非执行式文档一致性测试；
9. 同步 `tests/manual_validation/AGENTS.md`、manual-validation README 和 `docs/dev/isolated_mutation_validation.md` 的当前流程说明；
10. 运行 manual-validation 纯测试、完整 pytest，以及每个注册 case 的显式 dry-run；不得运行任何缺少 `--dry-run` 的 scenario 命令。

## 非目标

- 不让 pytest、CI、hook、import、timer 或 watcher 运行真实 `run.py <scenario>` 或 `run.py all`；
- 不从 Markdown、目录扫描或任意 shell string 自动创建可执行测试；
- 不因加入 dry-run case 而把探索性场景注册到真实 `all`；
- 不改变 scenario 的 mutation policy、tool allowlist、fixture、restore/cleanup 或真实证据要求；
- 不把 dry-run、mock 或静态 plan 成功描述为真实 OneNote capability 通过；
- 不要求为了完成本 TODO 而执行真实 OneNote mutation。

## 完成定义

- 每个 `SCENARIO_REGISTRY.public_names` 成员至少有一个唯一、稳定 ID 的注册 dry-run case；
- 新增显式导入并注册的 scenario 会自动进入 pytest dry-run collection，缺少或非法 case 时 collection fail closed；
- `registered_for_all=False` 的场景全部被 dry-run 自动测试，同时仍不出现在 `run.py all` 的真实批处理列表；
- pure plan builder 不导入或调用 MCP、COM、Notebook lifecycle 和 filesystem mutation，CLI 与测试共用该 builder；
- case harness 强制 `--dry-run --json`、控制 run directory，并由 sentinel 证明不会启动子进程、bridge 或 lifecycle；
- 默认、`--keep-worksite`、必要的 scenario 专属变体和 `all --dry-run` 已由结构化 case 覆盖，旧手写列表已删除或仅保留明确的负向测试；
- README 中带 case 标记的 canonical dry-run 代码块与 registry 渲染一致，文档块从不被 shell 执行；
- dry-run payload 的 policy、allowlist、budget、ordered steps、lifecycle 和零副作用合同拥有共享断言；
- manual-validation 纯测试与完整 pytest 通过，所有执行日志中的 manual scenario 命令都显式包含 `--dry-run`；
- 当前开发文档、人工验证 README、AGENTS 安全边界和 TODO 索引与最终实现一致。
