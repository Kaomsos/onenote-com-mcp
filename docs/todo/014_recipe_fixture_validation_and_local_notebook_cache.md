# 014：Recipe 驱动的不可变 Notebook 模板缓存与隔离工作副本

> ID：014
> 状态：待办
> 优先级：P2
> 类型：验证架构 / Fixture 性能与本地缓存安全
> 更新日期：2026-08-10

## 背景

[TODO 011](011_scenario_owned_fixture_recipes.md) 已让每个公开 Scenario 显式拥有唯一 fixture recipe，并由 recipe 负责构建与场景专属验证。当前真实 manual-validation 仍为每次运行创建全新的 disposable Notebook；该默认值隔离性最强，但在重复调试同一复杂 RichText/Table/Image/List/Tag fixture 时，会重复支付相同的 COM 创建、回读与内容验证成本。

本 TODO 评估并实现一个以 recipe 为唯一所有者的本地 fixture cache：recipe 生成并验证一个固定格式的、不可变的 OneNote Notebook 模板；每次隔离验证都先把模板透明复制到全新的 run-scoped 工作目录，再让 OneNote 只打开和操作该工作副本。缓存母本本身永远不由 OneNote 打开，也永远不承接 scenario mutation。

缓存必须保持 local-only，不使用 Microsoft Graph、云存储、OAuth、遥测或远程内容处理。实现只允许对已经关闭的 disposable Notebook 目录执行不解析内容的 opaque byte-for-byte copy；不得解析、编辑、拼接或重写 `.one` 二进制内容。这个有限的文件复制/清理能力与仓库当前“人工验证不得删除本地 Notebook 文件或目录”的规则存在冲突，因此实施前必须先形成显式项目级安全决策并同步根 `AGENTS.md`：例外只能覆盖受管理 cache root 下未打开、未 leased 的模板/staging 路径，绝不能覆盖工作副本、普通 validation artifact 或用户 Notebook。

这里的“复用”只表示复用不可变模板字节，不表示重复打开或 mutation 同一个 Notebook 实例。任何一次 scenario 都必须获得独立工作副本、独立 lifecycle lease、独立 manifest 和独立 evidence。

## 目标

- 以 `FixtureRecipe` 为模板身份、构建和验证的唯一入口，不新增按 scenario 名称分派的 cache switch 或第二 registry；
- recipe 可声明稳定的 `cache_fingerprint`，覆盖 recipe/schema 版本、fixture profile、manifest keys、内容能力、创建工具、模板目录 inventory 和影响 fixture 的参数；
- 默认行为继续直接创建 fresh Notebook；只有用户显式启用缓存模式时才尝试 materialize 模板工作副本；
- cache hit 只复制母本到新的 run-scoped working path；lifecycle wrapper 必须打开该副本并证明 OneNote 回报的规范化路径等于 working path、绝不等于 cache template path；
- 工作副本在 mutation 前必须执行与 fresh build 相同或更严格的 active-ID、拓扑、标题、编号、内容和 capability 验证；
- cache miss、版本不兼容、模板 inventory/hash 失败、工作副本验证失败、路径绑定失败、锁冲突或状态不确定时，必须失效并清理精确 cache entry 路径，然后由 recipe 重新构建、验证和发布模板；
- 缓存只保存在本地 validation 工作区，拥有明确索引、规范化模板路径、byte inventory/hash、创建时间、最后成功 materialize/validation 时间、recipe fingerprint 和清理证据；
- 保持单 Scenario、单 MCP 进程、静态最小 policy/allowlist、before/after evidence、失败保留和 HUMAN-GATED 真实执行边界；
- 模板是只读发布物；scenario mutation、restore、`--keep-worksite` 和失败状态都只发生在工作副本，永远不回写母本；
- 模板失效清理与重建必须自动完成，但删除范围必须是经过 root containment、fingerprint、entry ownership、非打开状态和无 lease 校验的单个精确路径。

## 推荐契约

### 1. Recipe 声明缓存身份

在现有 recipe 合同上增加纯静态 metadata，不读取环境、不启动 MCP：

```python
@dataclass(frozen=True)
class FixtureCacheDescriptor:
    schema_version: int
    recipe_name: str
    recipe_version: int
    fingerprint: str
    reusable: bool

class FixtureRecipe(Protocol):
    profile: FixtureProfile
    cache: FixtureCacheDescriptor

    async def build(self, context: FixtureContext) -> FixtureBuildResult: ...
    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]: ...
```

Fingerprint 至少纳入：

- recipe/profile 名称与显式版本；
- manifest keys、expected structure、content capabilities 和 validation conditions；
- fixture creation tools 与影响结构或正文的 recipe 参数；
- fixture evidence schema 版本；
- 必要的 runner/contract compatibility version。

Fingerprint 不得包含随机 token、绝对 run directory、Notebook 运行时 ID 或时间戳，否则无法稳定命中；也不得仅依赖 Python 类名或源码文件时间。

### 2. 本地 Cache Index 与 Lease

建议默认使用未纳入版本控制的 `.local-validation/fixture-cache/`：

```text
.local-validation/fixture-cache/
  index.json
  <fingerprint>/
    cache-entry.json
    template-notebook/
      <opaque OneNote notebook files>
    template-manifest.json
    template-fixture-result.json
    template-snapshot.json
    byte-inventory.json
    lock.json
```

`cache-entry.json` 至少记录：

- fingerprint、recipe/profile 名称与版本；
- 规范化 template directory、发布 staging directory 和 cache root containment 结果；
- 模板关闭前最后确认的 Notebook ID/name/path，仅用于来源证据，不能作为工作副本运行时身份；
- template directory 中每个相对文件的长度和 cryptographic hash；不得记录或解释 `.one` 内部结构；
- manifest/snapshot/evidence schema version；
- 创建时间、最后成功 materialize/验证时间和 validation checks；
- `state=building|ready|invalid|rebuilding|cleanup_failed`；
- 当前 clone/rebuild lock owner、运行 ID、进程 ID 和有界过期策略；
- 失效原因、最后失败阶段、清理目标、清理结果和人工处理说明。

索引和 entry 更新必须采用原子替换。模板先写入 fingerprint 同级的随机 staging directory，完成 byte inventory 和 recipe evidence 校验后再原子发布；不得把半成品目录标为 `ready`。不得接受任意外部路径、符号链接/junction/reparse-point 逃逸、名称定位 mutation 目标或无界目录扫描。

每次命中后，working copy 固定放在当前 fresh run directory 下，例如：

```text
.local-validation/run-<timestamp>/
  notebooks/
    <scenario>-working-copy/
  cache-materialization.json
  lifecycle-lease.json
  manifest.json
  ...
```

`cache-materialization.json` 必须记录 template path、working path、复制前后 byte inventory、OneNote 实际打开路径和 `opened_template=false` 证明。

### 3. 显式复用模式

建议增加语义明确且默认关闭的具名 scenario 选项，例如：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page `
  --reuse-fixture-cache
```

约束：

- `--reuse-fixture-cache` 只影响 fixture 来源，不改变 scenario policy、tool allowlist、mutation 参数或真实执行授权；其含义是“从模板创建工作副本”，绝不是“打开缓存 Notebook”；
- dry-run 必须显示 fingerprint、cache root、template path、run-scoped working path、预期 miss/hit、copy/open/validate、锁、清理与重建步骤，但不得创建或读取 cache 目录，也不得探测 OneNote；
- `all` 第一阶段不得接受或透传复用选项，避免并发 materialize、模板重建或相同内部 Notebook ID 的工作副本同时打开；
- 不允许调用方传入任意 Notebook ID/path 充当 cache entry；选择只能通过当前 recipe fingerprint 和受控 index；
- cache miss 走“fresh recipe build → 完整验证 → 关闭精确 Notebook → opaque copy 到 staging → byte inventory → 原子发布模板”；构建中途失败的 entry 永远不能命中；
- 无论 hit 还是刚完成 rebuild，实际 scenario 都必须再从 `ready` 模板 materialize 一个新工作副本并打开该副本，不能直接继续操作用来生成模板的 build Notebook。

### 4. Materialize 工作副本并重新验证

命中 entry 后必须依次：

1. 原子取得 fingerprint 级 clone/rebuild lock，并确认没有同 fingerprint 的未决 working-copy lease；
2. 校验 cache entry、template root containment、无 reparse-point 和完整 byte inventory；
3. 创建全新的 run-scoped working directory，并将 template directory opaque byte-for-byte 复制进去；
4. 在打开前比较模板与工作副本的相对文件集合、长度和 hash；任何差异都视为 cache failure；
5. 通过 lifecycle wrapper 只打开 working path；打开后回读精确 Notebook ID/name/path，并强制 `actual_path == working_path`、`actual_path != template_path`；
6. 如果 OneNote 已在其他路径打开相同 Notebook ID，或实际路径无法唯一证明，立即拒绝，不得关闭/接管未知实例；
7. 使用当前 scenario 已启动的唯一 MCP client 捕获工作副本的完整 fixture snapshot；
8. 由当前 recipe 重新执行通用 profile checks 和场景专属 validator；
9. 核对 manifest 中每个精确 ID 的工作副本映射。若 clone 后 ID 保持不变，必须证明模板母本未打开且不存在另一份同 ID 工作副本；若 OneNote 重分配 ID，必须生成完整、唯一、类型一致的 `template_id → working_id` 映射；
10. 写出本次 `cache-materialization.json` 和 `cache-validation.json`，只有全部通过才把工作副本交给 Scenario mutation。

不得只凭路径存在、Notebook 名称、旧 `fixture-result.json` 或旧 hash 判定命中有效。Byte inventory 只证明复制完整，当前工作副本的 OneNote 回读和 recipe validation 才是 mutation 发布门。

### 5. 模板不可变、工作副本独立

缓存模板与运行状态必须单向流动：`validated fresh build → closed staging template → immutable ready template → run-scoped working copy`。任何运行结果都不得反向写回模板：

- scenario mutation、restore/cleanup、`--keep-worksite`、Delete、Move、Copy cleanup、`copy_only` 和失败 handoff 只影响 working copy；缓存模板字节和 template evidence 保持不变；
- working copy 继续遵守现有失败保留规则。它处于 open/lease uncertain 时不得删除，也不得为了释放 cache lock 强制关闭；
- 同 fingerprint 在已有 working copy 未关闭或状态不确定时默认拒绝 materialize 第二份副本，避免 OneNote 同时打开内部 ID 相同的克隆；
- 默认成功关闭 working copy 后，也只允许清理 run-scoped working path；不得用 working copy 刷新模板；
- 只有模板 inventory、materialization 或 mutation 前 recipe validation 失败才使模板 `invalid`。Scenario mutation 自身失败不自动证明模板损坏，但必须记录两类失败的边界；
- invalid entry 进入受控 cleanup/rebuild；旧模板永远不能从某个运行后的工作副本“修复”。

### 6. 失效清理与自动重建

失效必须执行确定性的 `invalidate → clean exact entry → rebuild → publish → materialize` 流程：

1. 把 entry 原子标记为 `invalid`，记录 fingerprint、原因、模板路径和失败证据；
2. 解析模板路径并同时证明：位于配置的 cache root 下、恰为该 fingerprint entry、不是 cache root 本身、不是 workspace/run root、不是 working path、没有 reparse-point、没有 open/lease owner；
3. 先删除或移动 entry 内已知的模板/staging 文件，再删除空 entry directory；禁止使用未解析环境变量、宽泛 glob 或由 Notebook 名称拼接的路径；
4. 清理完成写 tombstone/cleanup evidence 到 cache root 级日志；如果任何文件无法删除，状态改为 `cleanup_failed`，停止且不得在同一路径覆盖重建；
5. 清理成功后创建新的随机 staging directory，由 recipe 构建 fresh Notebook、完整验证、精确关闭并 opaque copy；
6. 对 staging 生成 byte inventory，原子发布为同 fingerprint 的新 `ready` entry；
7. 再从新模板 materialize 全新工作副本，OneNote 只打开该副本。

受控清理属于本 TODO 的必要能力，不是通用 Notebook 删除工具。实施前必须修改仓库级安全合同，明确只允许删除 managed cache root 内、由本系统创建、当前未打开且未 leased 的模板/staging；若该项目级决策未获接受，实现只能把 entry 隔离为 tombstone/quarantine，不能声称本 TODO 已完成。

## 安全边界与风险

### P0：OneNote 直接打开缓存母本

风险：路径绑定错误使 lifecycle wrapper 打开 `template-notebook/`，scenario mutation 永久污染所有后续运行的模板。

缓解：template path 与 working path 是不同的冻结 typed 字段；wrapper 只接受 working path，打开后强制回读规范化实际路径并写 `opened_template=false` 证据。任何相等、别名、junction 或无法证明的路径都在 mutation 前拒绝。

### P0：清理越界删除用户或工作数据

风险：invalid cleanup 使用错误 root、名称拼接、symlink/junction 或宽泛递归删除，命中 workspace、run evidence、工作副本或用户 Notebook。

缓解：只允许 managed cache root 下的精确 fingerprint entry；删除前验证 resolved containment、entry ownership、非 root、非 working path、无 reparse-point、无 open lease。清理目标和逐项结果写 root-level tombstone；任何不确定性 fail closed。

### P0：多个克隆以同一内部 Notebook ID 同时打开

风险：opaque copy 保留内部 ID；前一次失败工作副本仍打开时再次 materialize，OneNote 可能把两个路径视为同一 Notebook 或绑定到错误实例。

缓解：fingerprint 级独占 materialization lock 与未决 working-copy lease；打开前枚举/核对已打开 Notebook 的 ID/path，发现同 ID 异路径立即拒绝。必须用真实 disposable 证据确认 OneNote 对克隆 ID 的实际行为后才能开启复用。

### P1：缓存扩大权限或绕过 fresh 边界

风险：为了 publish/open/repair template 临时加入额外 lifecycle、Copy、Delete 或 raw XML 权限。

缓解：Notebook build/open/close 仍只能使用当前 Scenario 已声明的静态最小权限和窄 lifecycle wrapper；opaque filesystem copy/cleanup 是独立的受控 cache store，不暴露为 MCP tool，也不能在运行中改变 policy。

### P1：Fingerprint 不完整导致错误命中

风险：Description、内容 fixture、manifest schema 或 validator 已变化，但 fingerprint 未变化。

缓解：显式 recipe/evidence schema version；合同测试对所有影响 fixture 的静态字段进行 canonical serialization；变更 recipe 时必须更新版本或使结构化 fingerprint 自动变化。

### P1：本地路径或 Notebook 身份漂移

风险：cache index 指向被移动、替换或同名的 template，或者实际打开路径落到 template 而不是 working copy。

缓解：同时绑定 fingerprint、byte inventory、规范化 template/working path 和 cache root；任一不一致都拒绝，不按名称寻找替代对象。`.one` 只作为关闭状态下的 opaque bytes 复制/清理，绝不解析或修改。

### P2：缓存收益不足以抵消复杂度

风险：opaque clone、打开工作副本和完整 cache-hit 回读与验证的耗时接近重新构建，却引入状态机与清理负担。

缓解：实施前后分别记录 cold miss、validated hit、invalid rebuild 的 MCP calls、bridge calls 和总耗时；若复杂 recipe 无稳定收益，保留架构但默认 `reusable=False`，或取消复用能力。

## 实施步骤

1. 为 cache descriptor、canonical fingerprint、template entry、materialization evidence、状态机和 template/working 路径约束增加纯单元测试；
2. 在 `FixtureRecipe`/Scenario registry 中加入无 I/O 的静态 template metadata 与 fail-closed 完整性检查；
3. 在实施任何 Notebook 目录复制或 invalid entry 清理前，先形成项目级安全决策，并同步根级与 manual-validation `AGENTS.md`：只允许关闭状态下 disposable template 的 opaque copy，以及 managed cache root 内精确、未打开、无 lease entry 的受控清理；
4. 实现 local template cache index、随机 staging、byte inventory、原子 publish、fingerprint 级独占锁、working-copy lease、root-level tombstone 和 crash recovery evidence；
5. 扩展 branch-free fixture runtime：cold path 为 `fresh build → validate → close → stage copy → inventory → publish → materialize working copy`，hit path 为 `lookup → validate template → materialize working copy`；common 层不得出现 scenario 名称分派；
6. 收紧 lifecycle wrapper：只接受 typed working-copy path，打开后回读 OneNote 实际路径并断言其等于 working path、不同于 template path；发现同 Notebook ID 已从另一条路径打开时拒绝继续；
7. 先仅为一个构建成本较高、可完整恢复的 Copy recipe 开启 `reusable=True`，其他 recipe 默认关闭；
8. 为 hit、miss、版本变化、inventory/hash 不匹配、路径越界、reparse point、清理失败、同 ID 异路径、锁冲突、部分构建、人工编辑、keep-worksite 和崩溃 lease 增加 recording fake 合同；测试必须证明 template 从未被打开且 working mutation 不改变其 byte inventory；
9. 把 template/working path、缓存决策、fingerprint、inventory、lease、`opened_template=false`、验证结果、invalidation 与逐项清理结果加入 manifest/report，但保持内容无敏感正文；
10. 为 dry-run catalog 增加 cache 变体；harness 仍强制 `--dry-run --json` 并证明不创建、读取或清理 cache，不启动 MCP，不访问 OneNote；
11. 同步 manual-validation AGENTS、README、开发验证文档和当前架构文档，明确复用的是不可变 Notebook 模板、每次真实验证使用独立工作副本；
12. 运行 manual-validation 纯测试与完整 pytest。真实 cold rebuild、hit materialization 或 forced invalidation 只能由用户本人显式执行并确认。

## 非目标

- 不缓存或复用用户业务 Notebook；
- 不让 OneNote 打开、注册或修改 cache master；cache master 只作为关闭状态下的不可变 Notebook 模板；
- 不解析、编辑或重写 `.one` 文件；模板发布和 materialization 只允许对关闭状态下的 disposable Notebook 目录做 opaque byte-for-byte copy；
- 不删除 managed cache root 之外、不能证明归属的、已打开的、存在 lease 的 template/working Notebook，也不提供通用 Notebook 删除能力；
- 不引入 Graph、OneDrive、SharePoint、Azure、OAuth、远程对象存储或遥测；
- 不让 pytest、CI、hook、import、timer、watcher 或 Agent 启动真实 cache build/hit mutation；
- 不让不同 recipe、不同 fingerprint、不同 policy 或不同 Scenario 并发共享同一个可写 working copy；
- 不因 template cache hit 跳过 working-copy snapshot、recipe validator、health check 或 before/after evidence；
- 不把 mutation 后的 working copy 回写、合并或晋升为 template；
- 不把性能优化描述为新的 OneNote capability 证据。

## 完成定义

- 每个公开 recipe 都有稳定、无 I/O、可审查的 template cache descriptor；默认 `reusable=False`，仅经评审的 recipe 可显式开启；
- 每个 `ready` entry 都是已关闭、不可变、具有完整 byte inventory 的固定格式 Notebook 模板；本地 cache index/entry 使用受控根目录、原子 publish、精确路径、独占锁和 lease，不接受任意外部路径或名称目标；
- cold miss 只有在完整 build + snapshot + recipe validation + close + staging inventory 通过后才发布 `ready`，随后仍须另行 materialize 新 working copy；部分失败 entry 永不命中；
- 每次 cache hit 都复制到唯一的 run-scoped working path；lifecycle 证据证明 OneNote 实际打开的是 working copy，且 `actual_path == working_path`、`actual_path != template_path`、`opened_template=false`；
- 每个 working copy 在 mutation 前重新捕获 snapshot 并通过完整 recipe validation；template artifact、byte hash 或旧验证结果不能单独替代 live validation；
- working mutation、失败、keep-worksite 和成功后的清理都不改变 cache master；前后 byte inventory 证明 template 保持不变；
- template 失效时只清理受控根目录内经过 containment、ownership、reparse-point、open-state 与 lease 检查的精确 fingerprint entry，再从 recipe 重新构建并原子发布；清理失败转为 `cleanup_failed` 并停止，绝不原地覆盖；
- 项目级安全决策和相应 `AGENTS.md` 规则已明确授权上述狭窄 opaque copy/定点清理边界；若未获授权，则实现只能 quarantine/tombstone，不能宣称本 TODO 完成；
- 同一内部 Notebook ID 已从另一条路径打开或存在未决 working-copy lease 时，新的 materialization fail closed；真实 disposable 证据覆盖 OneNote 对克隆身份的行为；
- cache 复用不增加 MCP 进程、不动态扩张 policy/tool allowlist、不启用 raw XML，也不改变 HUMAN-GATED 真实执行授权；
- dry-run case 覆盖 template cache 参数、计划、template/working 路径和预期决策，但由 sentinel 证明零目录、零 cache lookup/cleanup、零 MCP、零 bridge 和零 lifecycle 副作用；
- recording fake 覆盖 hit/miss/invalidation/exact cleanup/rebuild/materialization/path assertion/ID conflict/lock/recovery，manual-validation 纯测试与完整 pytest 通过；
- 用户本人在 disposable 环境确认至少一次 cold rebuild、validated hit materialization 和 forced invalidation cleanup + rebuild，并由证据确认 cache master 从未被 OneNote 打开；Agent 不执行真实场景；
- manual-validation AGENTS、README、开发验证文档、当前架构文档和 TODO 索引与最终实现一致。
