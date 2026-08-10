# 011：Scenario 自管理 Fixture Recipe 与拆分集中式 Fixtures

> ID：011
> 状态：待办
> 优先级：P1
> 类型：验证架构 / Scenario 所有权与 Fixture 可维护性
> 更新日期：2026-08-10

## 背景与当前问题

[`tests/manual_validation/scenarios/common/fixtures.py`](../../tests/manual_validation/scenarios/common/fixtures.py) 当前约 1439 行。它已不再只是共享 fixture helper，而是同时承担：

- 14 个公开 Scenario 的具体 fixture 构建，并在 `prepare_scenario_fixture()` 中通过 `if/elif args.scenario` 分派；
- Reorder/Reparent 的长篇 Description 文本和场景专属标题；
- `_validate_fixture_snapshot()` 中另一套按 scenario 名称分派的结构、拓扑、内容和编号验证；
- 公共 manifest、`fixture-result.json`、snapshot 和失败证据的编排与落盘；
- Copy/Move 的分层富内容 fixture，以及 `copy_fixture`、`reparent_page_fixture` 等特殊 manifest 字段；
- ScenarioSpec/profile 声明与实际构建结果之间的完整性检查。

这导致 Scenario 对自身 fixture 只有间接所有权：Scenario class 管理 parser、spec、mutation、restore/cleanup，但新增或修改 fixture 必须进入一个中央模块增加构建分支、验证分支、常量和测试。随着场景增加，中央模块会持续增长，且容易出现下列问题：

- 构建逻辑与执行/恢复逻辑相距较远，审查一个 Scenario 时无法在局部确认完整闭环；
- `args.scenario` 字符串分派、`SCENARIO_SPECS`、Scenario registry 和测试列表之间可能漂移；
- 修改一个共享文件会扩大回归面，并增加 merge conflict；
- 测试直接导入中央私有 validator，使场景专属 invariant 无法按所有权拆分；
- Copy/Reparent 的特殊证据字段促使 common runtime 逐渐了解越来越多业务语义；
- 仅把大文件机械拆成多个全局函数模块，仍会保留中央 switch 和第二套隐式 registry，不能解决根因。

本 TODO 的目标是让每个 `Scenario` 对象显式拥有自己的 fixture recipe；共享层只保留无场景分支的构建原语、运行时编排、通用验证和证据持久化。

## 目标

- 每个公开 Scenario 显式提供且只提供一个 fixture recipe，负责自身 fixture 的构建与场景专属验证；
- orchestrator 从已经选定的 Scenario 对象取得 recipe，不再把 `scenario_name` 传给中央 fixture switch；
- `common/` 只保留真正跨场景复用的 typed primitive、recipe 模型、通用 runtime 和通用 invariant；
- fixture profile、创建工具、manifest keys、实际 recipe 和 ScenarioSpec 在注册或纯测试阶段可相互核对并 fail closed；
- 保持单 Scenario、单 MCP 进程、静态最小 policy/allowlist、fresh Notebook、before/after evidence 和失败保留边界；
- 第一阶段严格保持现有 manifest、fixture-result、report 和 dry-run payload 契约，避免“代码拆分”与“证据 schema 迁移”同时发生；
- 新增 Scenario 时，只需在其 Scenario 声明与 recipe 模块中完成局部实现，不再修改中央 `fixtures.py` 分支。

## 与相关 TODO 的关系和实施顺序

- [TODO 003：Scenario 独立 Fixture 与单 MCP 进程闭环](003_scenario_scoped_mcp_and_fixtures.md) 已完成并继续定义运行时安全边界。本 TODO 只收敛内部代码所有权，不重新打开其真实性能或 OneNote 验收结论；
- [TODO 010：Manual Validation Dry-run 自动测试用例注册](010_registered_dry_run_test_cases.md) 也会扩展 Scenario metadata、registry 和静态 profile 读取。建议先完成本 TODO 的 FixtureRecipe/Scenario 接口骨架，再实现 TODO 010 的 case catalog；
- 如果两项并行实施，应先合并一份 Scenario metadata 协议：`fixture_recipe/profile` 服务 fixture runtime 与 dry-run plan，`dry_run_cases` 服务 pytest collection。两者共用现有 Scenario registry，禁止各自创建第二 registry。

## 可行性评估

结论：**可行性高，实施风险中等，建议在继续增加 manual-validation 场景前完成。**

有利条件：

- 所有 Scenario 已由 `SCENARIO_REGISTRY` 返回对象实例，orchestrator 在 fixture 阶段之前已经取得精确 Scenario；
- 每个场景已经具有 `ScenarioSpec.fixture` 静态 profile、policy 和 tool allowlist，可作为 recipe 注册校验基础；
- [`fixture_builders.py`](../../tests/manual_validation/scenarios/common/fixture_builders.py) 已包含 `ensure_group`、`ensure_section`、`ensure_page`、富内容和 List/Tag 等可复用 typed 原语；
- MCP client 已由 orchestrator 创建并传入当前集中式 fixture builder，拆分无需新增进程或扩张权限；
- 现有大量纯测试能够作为行为保持回归网，迁移可按场景分批完成。

主要风险来自证据兼容、失败时部分构建状态的保留和共享 Copy fixture 的抽象边界，而不是 Python 分派本身。

## 推荐架构

### 1. Scenario 持有 FixtureRecipe

推荐使用组合而不是让 Scenario class 继续膨胀。每个 Scenario 通过只读属性持有一个 recipe：

```python
class Scenario:
    fixture_recipe: FixtureRecipe

    @property
    def fixture_profile(self) -> FixtureProfile:
        return self.fixture_recipe.profile
```

`FixtureRecipe` 使用明确协议或抽象基类：

```python
class FixtureRecipe(Protocol):
    profile: FixtureProfile

    async def build(self, context: FixtureContext) -> FixtureBuildResult: ...

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
        snapshot: dict[str, Any],
    ) -> tuple[str, ...]: ...
```

Scenario 是 recipe 的所有者和选择入口；recipe 只是该 Scenario 的内部实现策略。Orchestrator 不得按名称 import 或查找 recipe，也不得新增独立的 fixture registry。

### 2. 数据模型与职责边界

建议增加以下冻结数据模型；精确字段可在实施时调整：

```python
@dataclass(frozen=True)
class FixtureContext:
    args: argparse.Namespace
    options: RuntimeOptions
    client: MCPStdioClient
    notebook: Mapping[str, Any]
    notebook_path: str
    spec: ScenarioSpec
    token: str
    recorder: FixtureRecorder


@dataclass(frozen=True)
class FixtureBuildResult:
    structure: Mapping[str, Mapping[str, Any]]
    evidence: Mapping[str, Any] = field(default_factory=dict)
```

职责划分：

- `FixtureRecipe.build()`：只创建当前场景声明的对象、Description 和内容 fixture，并通过 recorder 逐项登记精确 ID；
- `FixtureRecipe.validate()`：只验证当前场景特有的父子关系、编号、内容能力和 Description marker；
- common runtime：创建基础 manifest、调用 recipe、捕获统一 snapshot、执行通用 active-ID/profile checks、持久化 manifest/result、归一化失败状态；
- common primitive：单个 typed 创建/定位/回读操作，不知道 Scenario 名称、manifest schema 或报告格式；
- orchestrator：选择 Scenario、启动唯一 MCP client、调用 common fixture runtime，再执行 Scenario mutation；不构建任何具体 fixture。

`FixtureContext` 不得创建或持有第二个 MCP client，不得暴露 lifecycle wrapper，也不得允许 recipe 修改 policy/tool allowlist。Recipe 的所有调用仍经过 orchestrator 已启动的单一 allowlisted client。

### 3. 文件布局

建议采用统一的一场景一 recipe 模块，避免简单场景内联、复杂场景另建文件所造成的结构不一致：

```text
tests/manual_validation/scenarios/
  base.py
  rename.py
  reparent_page.py
  ...
  fixture_recipes/
    __init__.py
    create.py
    rename.py
    reorder_page.py
    reorder_section.py
    reorder_section_group.py
    reparent_page.py
    reparent_section.py
    reparent_section_group.py
    delete.py
    copy_page.py
    copy_section.py
    copy_section_group.py
    copy_notebook.py
    move_page.py
    layered_copy.py
  common/
    fixture_models.py
    fixture_runtime.py
    fixture_primitives.py
```

- `fixture_recipes/<scenario>.py` 保存场景专属 Description、构建和 validator；
- `layered_copy.py` 提供 Copy/Move 共享的 parameterized recipe 基类或组合 helper，但每个 Scenario 仍持有自己的配置实例；
- 现有 `fixture_builders.py` 可先重命名为 `fixture_primitives.py`，只保留真正共享、无 Scenario 分支的函数；
- 迁移完成后删除集中式 `common/fixtures.py`，不得保留转发 switch 或兼容 registry。

`fixture_recipes/` 不是第二个自动发现目录。具体 recipe 由对应 Scenario module 显式 import 并实例化；继续禁止 filesystem discovery。

### 4. Profile 与 Recipe 的单一来源

当前 `SCENARIO_SPECS` 集中声明 fixture profile，而 recipe 将成为实际实现。为避免形成两套权威来源，推荐分两步收敛：

1. 行为保持阶段：Scenario 仍取得现有 `ScenarioSpec`，registry/纯测试断言 `scenario.fixture_recipe.profile is scenario.spec.fixture` 或字段完全相等；
2. 收敛阶段：由 Scenario/recipe 暴露 fixture profile，`ScenarioSpec` 组合该 profile、policy 和 allowlist，不再在中央 `specs.py` 重复构造相同 profile。

Policy 与 allowlist 必须继续在 MCP 启动前静态可得。读取 `recipe.profile` 不得实例化 client、读取环境、创建路径或调用 OneNote。

Registry 在注册 Scenario 时至少验证：

- recipe 存在且 recipe/profile 名称与 ScenarioSpec 匹配；
- profile 的 `creation_tools` 是 scenario `tool_allowlist` 的子集；
- manifest keys 唯一且满足稳定命名规则；
- recipe 对象不被两个不兼容 Scenario 共享；允许显式共享无状态的 recipe 基类，但最终配置实例必须归属于一个 Scenario；
- 注册过程无 I/O、无 MCP、无 COM、无 lifecycle 副作用。

### 5. 通用 Fixture Runtime

从现有 `prepare_scenario_fixture()` 提取一个无 scenario switch 的模板方法：

```python
async def prepare_fixture(
    scenario: Scenario,
    context: FixtureRuntimeContext,
) -> tuple[dict[str, Any], dict[str, Any]]:
    recipe = scenario.fixture_recipe
    build = await recipe.build(context.for_recipe())
    snapshot = await capture_snapshot(context.client, context.notebook_id)
    common_checks = validate_profile(recipe.profile, build, snapshot)
    scenario_checks = recipe.validate(context.for_validation(), build, snapshot)
    return persist_fixture_evidence(...)
```

Common runtime 只允许以下通用分支：成功、构建失败、snapshot/validation 失败和 evidence persistence 失败。不得出现 `if scenario.name == ...`、Scenario 名称集合或业务字段判断。

### 6. 部分失败与 FixtureRecorder

拆分时不能降低当前失败保留语义。Recipe 如果创建到一半抛出异常，common runtime 仍应知道已创建的精确对象。建议提供受约束 recorder：

```python
recorder.record_structure("source_section", section)
recorder.record_evidence("rich_content", rich_fixture)
```

Recorder 要求：

- key 必须属于 profile 声明或 recipe 声明的 evidence schema；
- 每次登记后可原子写入 pending manifest/checkpoint，避免异常丢失部分 ID；
- 不接受任意路径、可执行 callback 或外部对象引用；
- 重复 key、对象缺 ID、类型不符或越出当前 Notebook 时立即 fail closed；
- common runtime 统一写入 `fixture_validation={status: pending|passed|failed}` 和失败原因。

如果实施审计发现当前构建阶段尚未完整保存部分状态，本 TODO 可以加强该行为，但必须把 evidence schema 变化单独列出并补兼容测试，不能在无记录的情况下静默改变报告。

## Manifest 与报告兼容策略

第一阶段保持以下现有外部/证据结构：

- `manifest.json` 的 `structure`、`scenario_policies`、`scenario_spec` 和 `fixture_validation`；
- `copy_fixture` 与 `reparent_page_fixture` 等报告当前读取的字段；
- `fixture-result.json`、`fixture-snapshot.json` 的路径、成功/失败状态和核心字段；
- Scenario `prepare_arguments()` 与 `execute(..., fixture_result=...)` 的输入语义。

Recipe 通过声明式 `evidence` 返回这些字段，由 common persistence adapter 写入旧 schema。完成行为等价迁移并通过测试后，若仍有必要统一为版本化 `fixture_evidence` namespace，应另行评审 schema migration，不作为删除中央 switch 的前置条件。

## 场景共享策略

共享的最小原则是“共享 primitive 和经过明确参数化的 recipe component，不共享场景所有权”：

- Copy Page/Section/SectionGroup/Notebook 与 Move Page 可复用 `LayeredCopyFixtureComponent`，统一 RichText/Table/Image/List/Tag 父子 Page 构建；
- Reorder/Reparent 的 Description marker 验证可复用 `DescriptionPageComponent`，但正文和 required markers 留在各 recipe；
- 三路 Notebook/SectionGroup 父级覆盖可以使用 typed helper 生成编号对象，但各 recipe 自己声明 manifest key 和预期拓扑；
- 不允许新建接受 `scenario_name` 后再次 `if/elif` 的“共享”函数；
- 不为了去重把不同场景的 permission、manifest key 或 validation 语义合并为宽泛配置字典。

## 风险与缓解

### P1：拆分后权限静态审查变弱

风险：创建工具隐藏在 recipe 内，`ScenarioSpec.fixture.creation_tools` 不再准确，导致 policy/allowlist 漏项或扩权。

缓解：profile 与 recipe绑定；registry 验证 creation tools 是 allowlist 子集；fake client 测试记录实际调用并与声明集合比较；真实 MCP 启动前仍进行现有 health-check 精确校验。

### P1：Manifest/report 契约漂移

风险：移动代码时顺便更名 key 或改变嵌套结构，Scenario、report 和历史 artifact 读取失败。

缓解：第一阶段保持 schema；为每类 recipe 增加 manifest/result contract 测试；schema 统一延后为独立评审步骤。

### P1：构建中途失败丢失已创建对象

风险：局部 `structure` 留在 recipe 栈帧，异常后 common runtime 无法生成清理/人工接管证据。

缓解：使用受约束 recorder 增量登记并持久化 pending checkpoint；统一 failure adapter 保留 Notebook、lease、精确 ID 和 evidence。

### P1：Scenario 类本身变成新的巨型模块

风险：把 1000 多行直接移动到各 Scenario class，使 mutation、fixture、restore 和报告仍难以审查。

缓解：Scenario 只拥有 recipe 实例和公开闭环方法；具体构建/验证放入一场景一模块的 `fixture_recipes/`；Scenario 与 recipe 通过 typed context/result 交互。

### P2：共享代码复制或过度抽象

风险：一场景一模块导致 Copy 富内容逻辑重复；反向过度配置化又会形成新的隐式 DSL 和中央分派。

缓解：只提取已至少由两个场景共享且 invariant 相同的 typed component；禁止以 scenario 名称驱动行为；配置只表达名称、manifest key 和结构参数，不表达权限绕过或任意 callbacks。

### P2：循环 import 与 registry 初始化顺序

风险：recipe import Scenario/registry，Scenario 又 import recipe，导致部分注册或重复实例。

缓解：protocol/model 位于无具体场景依赖的 `fixture_models.py`；recipe 不 import registry；Scenario module 单向 import 自己的 recipe；`scenarios/__init__.py` 仍是唯一显式公开导入清单。

### P2：与 TODO 010 并行修改冲突

风险：[TODO 010](010_registered_dry_run_test_cases.md) 也会修改 Scenario base、registry 和静态 profile 获取方式，两个重构并行实施会产生重复模型或冲突。

缓解：建议先实施本 TODO 的 FixtureRecipe/Scenario 扩展点，再让 TODO 010 的 dry-run case 从同一 Scenario 对象读取静态 profile；若并行实施，必须先共同确定 Scenario metadata 接口，禁止分别新增 registry。

## 分阶段迁移方案

### 阶段 A：建立骨架且保持行为

1. 增加 `fixture_models.py`、`fixture_runtime.py`、FixtureRecipe protocol 与 recorder 合同测试；
2. 给 Scenario base 增加必需的 `fixture_recipe` 所有权接口；
3. 提取 common manifest/snapshot/validation/persistence 模板，不改变 JSON schema；
4. 增加合同测试，证明没有新增 MCP process、policy、tool 或 lifecycle 能力。

### 阶段 B：迁移低复杂度场景

依次迁移 `rename`、`delete`、`create`：

- 用于验证 recipe 接口、scenario-specific args、disposable allowlist 和完整 preset；
- 每迁移一个场景，就从中央 build/validate switch 删除对应分支；
- 不允许新旧实现双写或运行时 fallback，测试只允许一种权威路径。

### 阶段 C：迁移 Reorder/Reparent

- 把 Description 文本和 marker 移入对应 recipe；
- 将 `_validate_fixture_snapshot()` 的顺序、父级、ID、Page topology 与富内容检查拆到各 recipe validator；
- 保持 capability assessment、恢复与 `--keep-worksite` 行为不变。

### 阶段 D：迁移 Copy/Move 共享组件

- 提取 layered rich Page component；
- 为四层 Copy 和 Page Move 创建各自 recipe 配置；
- 保持 `copy_fixture`、semantic page、fidelity gate 和 `copy_only` 失败证据不变。

### 阶段 E：删除中央模块并收敛测试

- 删除 `common/fixtures.py` 及所有 scenario-name switch；
- 测试按 recipe/runtime 所有权拆分，不再 import `_validate_fixture_snapshot`；
- 增加 repository contract，拒绝在 common fixture runtime 中重新引入 Scenario 名称分派；
- 同步 manual-validation AGENTS、README、开发验证文档和 TODO 003 的架构链接；
- 与 TODO 010 对齐 Scenario metadata 和 registry 命名。

## 测试方案

- Registry 测试：每个公开 Scenario 恰有一个 recipe，profile/spec 名称一致，creation tools 是 allowlist 子集；
- Recipe 单元测试：使用 recording fake client 验证创建顺序、精确 parent ID、manifest keys、Description markers 和无额外 tool call；
- Validator 单元测试：每个场景分别覆盖成功、缺失 ID、错误父级、编号/顺序错误、内容缺失和回收站对象；
- Runtime 测试：成功、build 中途异常、snapshot 失败、validator 失败、evidence 写入失败均保留正确 pending/failed 状态；
- Manifest/report 回归：迁移前后关键 JSON 和报告字段保持兼容，不使用隐藏具体错误的宽泛 snapshot；
- Process/policy 测试：每个场景仍最多启动一个 MCP，recipe 不能创建 client，实际 fake tool calls 不越出静态 allowlist；
- Ownership 测试：common runtime/primitives 不包含公开 Scenario 名称、`args.scenario` 分派或 fixture recipe registry；
- 完整 manual-validation 纯测试和全量 pytest；真实 OneNote 场景仍只能由用户本人执行。

## 非目标

- 不改变任何 fixture 的用户可见结构、编号、Description 语义或富内容能力；
- 不改变 mutation、restore/cleanup、`--keep-worksite`、failure handoff 或 report 成功判定；
- 不增加 MCP 进程、动态 policy、tool allowlist 或 lifecycle wrapper 权限；
- 不让 Scenario recipe 直接操作 OneNote COM、PowerShell bridge、`.one` 文件或 Notebook lifecycle；
- 不通过 filesystem discovery 自动导入 recipe 或 Scenario；
- 不在本 TODO 中重新设计全部 manifest/report schema；
- 不以 pytest、mock 或 dry-run 代替真实 mutation 证据，也不要求 Agent 执行真实场景。

## 完成定义

- 每个公开 Scenario 对象显式持有唯一 fixture recipe，orchestrator 只通过 Scenario 对象调用它；
- `common/fixtures.py` 已删除，common runtime、models 和 primitives 中不存在 Scenario 名称 switch 或第二 registry；
- 每个 recipe 的静态 profile、creation tools、manifest keys 与 ScenarioSpec/policy/allowlist 在注册或纯测试阶段 fail closed 校验；
- 场景专属 Description、构建和 validator 位于对应 recipe 模块，测试不再导入中央私有 `_validate_fixture_snapshot()`；
- Copy/Move 共享 layered component 不依赖 scenario 名称，不扩大任何场景的权限或 fixture 范围；
- build 中途失败会保留已创建对象的精确 ID、pending/failed validation 状态、Notebook lease 和人工接管证据；
- 第一阶段迁移保持现有 manifest、fixture-result、snapshot、report、dry-run 和历史 artifact 读取合同；
- 每个场景的 recording fake 测试证明实际 fixture tool calls 不越出声明的 creation tools/allowlist；
- 单 Scenario 单 MCP、fresh Notebook、静态最小权限、失败保留和 HUMAN-GATED 真实执行边界保持不变；
- manual-validation 纯测试与完整 pytest 通过；Agent 执行的所有 scenario 检查都显式带 `--dry-run`；
- manual-validation AGENTS、README、开发验证文档、TODO 003/010 的相关引用和 TODO 索引与最终架构一致。
