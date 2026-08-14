# Manual Validation Scenario 与 Fixture 架构

> 状态：当前实现态
> 作用域：`tests/manual_validation/` 的 human-gated 测试基础设施
> 非作用域：生产 MCP tool、service、bridge 和公开协议

本文是 manual-validation Scenario、Fixture Recipe、fixture cache、working-copy lifecycle 与证据流的权威设计文档。它解释系统为什么按当前边界拆分、哪些不变量必须成立，以及 fresh/cache 两条路径如何汇合到同一个 mutation 前信任门。

如何新增或修改 Scenario 的操作步骤见[缓存 Fixture 驱动的真实操作验证推荐实践](../dev/cached_fixture_operation_validation.md)。生产 MCP 总体架构见[生产架构](architecture.md)；测试基础设施不能反向定义或扩大生产 tool 契约。

## 1. 设计边界

Manual validation 是生产 MCP 之外的测试系统，但复用生产 tool 验证真实 OneNote COM 行为。它遵守以下边界：

- 真实运行只能由用户在交互式前台显式启动；Agent、pytest、CI、hook、timer 和 watcher只能运行纯测试或 `--dry-run`。
- 每个具名 Scenario 是独立的权限、fixture、mutation、证据和 lifecycle 单元；`all` 只负责串行启动已审查的 Scenario，不共享 Notebook、MCP 进程、run 目录或权限。
- Fixture cache 只复用已经验证并关闭的输入模板；永不复用 mutation 后的 working copy，也不把 cache hit 当作本次 live validation。
- 生产 tool 的参数、policy 和错误语义仍以生产设计为准。测试 runner 只能收紧调用范围，不能提供旁路或动态扩权。

## 2. 核心对象与职责

| 对象 | 负责 | 不负责 |
| --- | --- | --- |
| `Scenario` | 静态 policy/tool allowlist、fixture 所有权、mutation、比较、恢复或显式非恢复结果 | cache index、文件复制、Notebook 打开细节 |
| `RecipeBase` | role/profile/manifest keys、fixture 构建、role/bundle validator、稳定 cache identity | lifecycle、cache I/O、mutation、cleanup |
| Fixture runtime | recorder、snapshot、live validation、typed evidence handoff | 按 Scenario 名称分派业务逻辑 |
| Cache runtime | lookup、锁、inventory、publish、materialize、确定性失效 | 打开 template、持有 working lease、执行 mutation |
| Lifecycle wrapper | fresh create、working exact open、live ID/path lease、精确 close/keep | 修改 fixture 内容、发布或清理 cache |
| Scenario registry | 公开命令、dry-run case 与 `included_in_all` 资格的唯一目录 | pytest collection 或第二套构造列表 |

每个公开 Scenario 必须唯一拥有一个 Recipe instance。Recipe identity 由 recipe/version、有序 Notebook roles、fixture parameters、manifest keys、validation conditions、evidence schema 和 bundle invariants 组成；运行时生成的 OneNote ID、路径、token 和正文不得进入 fingerprint。

## 3. Fresh 与 Cache 的共同信任门

普通运行默认使用 fresh fixture，并且零 cache access。Recipe 在本次新建的 disposable Notebook bundle 上构建结构，recorder 在每个精确对象创建后增量保存 pending manifest；role 与 bundle validation 全部通过后，Scenario 才能取得 mutation 输入。

显式 `--use-cache` 时，数据流为：

```text
validated closed disposable bundle
        ↓ publish opaque immutable bytes
managed immutable cache template
        ↓ materialize to unique run-local paths
new working Notebook bundle
        ↓ exact open + parent-aware hierarchy activation
typed relative-address ID rebind
        ↓ two stable hierarchy observations
one full Page read per Page
        ↓ live Recipe validation + scenario-before handoff
Scenario mutation
```

Cache build 负责建立模板的权威内容基线；在发布 programmatic template 前，lifecycle wrapper 对每个 exact Notebook 请求一次 `SyncHierarchy`，再执行 `CloseNotebook(force=false)` 并确认精确 ID/path 已关闭，随后才允许 opaque copy。Sync 请求失败会保留 active lease 并阻断发布。此处不 reopen Notebook；Search 的 close/reopen 仍是独立的 index activation 例外。materialized working copy 仍必须重新证明 live identity、完整结构和内容真实性。两条路径最终都向 Scenario 提供同一种 run-local manifest、snapshot 和 validation result，Scenario 不应根据来源降低比较门限。

## 4. Template、Working Copy 与身份隔离

Template 是关闭、不可变且不被 OneNote 打开的 opaque byte tree。每次消费都 materialize 到新的 run-scoped working paths；OneNote、mutation、restore、`--keep-worksite` 和失败现场只接触 working copy。

Cache 不保存 working lease，也不与历史 run 建立所有权关系。多个 run 可以从同一 fingerprint/instance 生成物理独立 working bundle，但必须满足：

- role 内和 run 间的实际 live Notebook ID/path 不相交；
- 每个 role 的 live ID/path 由当前 run 的 lifecycle lease 证明；
- template inventory 在运行前后保持不变；
- working activation 或瞬态 COM 失败不污染已验证 template；只有确定性的 template identity、inventory 或证据失败才使 entry 不可命中。

## 5. OneNote GUI 与短命 COM 前置条件

当前环境已经稳定观察到：如果 Scenario 启动前 OneNote Desktop GUI 未运行，由首个短命 PowerShell/COM client 冷启动 OneNote 后，working Notebook 可能只剩可读空 shell，完整 hierarchy 在后续独立调用中持续缺失。相同 fixture 在 OneNote GUI 已预启动时可以完成层级收敛。

因此 runner 在创建或打开 working Notebook 前复用生产 `health_check` 的 check-only GUI 门限，真实 `all` 在启动首个 child 前检查一次。缺失或无法证明时 fail closed；runner 不隐式启动 GUI，也不把 sleep、重复激活、ID rebind 或通用 close/reopen 当作修复。完整观察和推断边界见[OneNote COM 冷启动 Fixture hierarchy 丢失](../lesson/onenote_com_cold_start_fixture_hierarchy_loss.md)。

## 6. Hierarchy 激活、ID 重绑与单次内容取证

Materialized role 只打开一次 exact working path。Lifecycle 在第一次 child COM 调用前冻结受 manifest 约束的请求，在同一个短命 PowerShell/COM session 中按 parent-before-child 批量激活 SectionGroup/Section，并尝试读取 Notebook Pages hierarchy。精确的顶层 `OneNote_RecycleBin` 是 OneNote 管理的系统子树：它继续保留在 opaque byte inventory 和 template 完整性验证中，但不会被构造成用户 SectionGroup/Section 激活请求；排除前仍必须通过 working-tree containment 与 reparse-point 检查，并在 materialization evidence 中记录 content-free 原因。

Fixture observer 随后重新枚举完整 hierarchy，按 Notebook-relative typed address 唯一重绑 SectionGroup、Section 和 Page。全部声明对象必须以相同 ID、type、parent、section、page level、parent Page 和 sibling order 连续稳定两次，之后才允许完整内容读取。

每个 Page 在一次 snapshot 中只调用一次 `get_page_xml(page_info=all)`。hash、canonical/reparent digest、capability、MathML 和 normalized object evidence 都从同一 XML 本地派生。该唯一完整 `scenario before` snapshot 同时承担 materialized 内容真实性复核和 mutation baseline，并通过 exact role/Notebook ID/digest 单次交给 Scenario；任一 role 未消费完时，首次 mutation 在调用前 fail closed。

Fresh Search 为激活 OneNote index 使用的 `CloseNotebook(force=false) → exact-path reopen` 是具名 Scenario 的窄例外，不属于通用 fixture persistence 或 cache working-copy 设计。

## 7. 失败隔离与收尾

Fixture 准备、working activation、ID rebind、双稳定、内容验证和 scenario-before handoff 都位于 mutation 之前。任一门失败必须：

- 不执行或重放 mutation；
- 保存已登记的 manifest、working path、live lease、阶段与底层错误；
- 默认精确关闭当前 run 的 Notebook，只有显式 `--keep-notebook`/`--keep-worksite` 才保持打开；
- 不删除 working Notebook files、普通 artifacts 或失败现场；
- 不写回 template，也不因 run-local 瞬态失败自动 quarantine template。

`all` 的子进程隔离保证单个 Scenario 的异常不会共享 MCP/COM/lease 状态；完成默认收尾后继续下一个 Scenario。用户中断整个 `all` 时不承诺继续剩余任务。

## 8. Interactive 与 User-Authored Fixture

Programmatic Recipe 可以在 cache miss 时自动构建 disposable template。Interactive/UserAuthored Recipe 只能由静态注册、排除于 `all` 的 human-gated bootstrap Scenario 发布；普通 consumer 的 miss 只返回 `interactive_bootstrap_required`，不得动态创作、猜测实例或读取任意用户 Notebook。

人工 verdict 只能补充 COM 无法证明的视觉、播放或交互证据，不能覆盖机器 validator、ID/topology/content comparator 或 policy 失败。

## 9. 路径、证据与 Maintenance 边界

所有 cache、staging、working 和 evidence 路径在副作用前执行普通 Windows 路径预算；详细公式以[Windows Fixture Cache 路径配额](windows_fixture_cache_path_budget.md)为准。OneNote ID 不得进入受管物理名称，完整身份保存在 JSON evidence。

Cache schema 不对旧 payload 提供隐式 lookup、迁移或删除兼容；未知、非空或证据不完整的旧状态一律 fail closed。本地文件/目录的原子发布只对 Windows `WinError 5/32` 使用身份守卫的有界退避；每次重试前 source/destination 的 `lstat` 身份必须不变，destination 必须持续不存在。该机制不适用于 COM、MCP、删除或 mutation，因此不构成 mutation 重试。

主要运行证据包括：

- `cache-materialization.json`：materialize 动作与 `validated_hit|cold_build|interactive_bootstrap` 来源；
- `materialized-hierarchy-open[-<role>].json`：parent-aware batch 结果；
- `cache-hierarchy-convergence.json`：逐 role 双稳定与内容阶段；
- `cache-structure-remap.json`：typed source→working identity；
- `scenario-before-snapshot-handoff.json`：snapshot 单次消费状态；
- `lifecycle-lease*.json`：当前 run 的 exact live Notebook identity。

`clear runs|cache|all` 是 Scenario registry 和生产 MCP 之外的 maintenance 边界。只有用户交互式确认的 maintenance action 可以删除由 ownership、containment、reparse、open-state 和 receipt 共同证明的精确 managed payload；普通 Scenario 永不获得该删除权限。

短时 open lock 内在打开 working bundle 前后各捕获一次当前开放 Notebook ID/实际目录 snapshot；历史 run-local lease 只与该 snapshot 做内存比较，不得逐 lease 重复枚举 OneNote。

## 10. 当前实现入口

- 当前运行合同与 Scenario 矩阵：[`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md)
- Recipe 基类：`tests/manual_validation/scenarios/fixture_recipes/recipe_base.py`
- Scenario-owned Recipes：`tests/manual_validation/scenarios/fixture_recipes/`
- 静态 profiles/policies：`tests/manual_validation/scenarios/common/specs.py`
- fresh/materialized validation：`tests/manual_validation/scenarios/common/fixture_runtime.py`
- cache state machine：`tests/manual_validation/scenarios/common/fixture_cache.py`
- lifecycle：`tests/manual_validation/lifecycle.py`

具体接入步骤、证据清单、负向测试和交付顺序见[开发实践文档](../dev/cached_fixture_operation_validation.md)；该文档不得重复定义本文的架构不变量。
