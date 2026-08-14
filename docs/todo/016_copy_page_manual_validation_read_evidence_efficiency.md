# 016：Page Copy 人工验证只读取证降本

> ID：016
> 状态：已完成
> 优先级：P3
> 类型：Manual Validation 性能 / 只读证据复用
> 更新日期：2026-08-14

## 完成结论（2026-08-14）

本轮已部分覆盖阶段一，并将实现限制在 manual-validation runner：

- 通用 `capture_snapshot()` 对每个 Page 只调用一次 `get_page_xml(page_info=all)`；stable/canonical/reparent/raw hash、capability projection、MathML projection 与 normalized Page object evidence 均复用这份 XML。
- normalized object evidence 直接复用生产 `collect_page_objects()` 和 `content_objects()`，不在 manual validation 中维护第二套 XML 对象语义；`get_page_objects` 不再造成第二次 `GetPageContent`。
- cache materialization 在完整内容取证前新增 manifest-aware hierarchy convergence：全部声明的 SectionGroup、Section 和 Page 必须按 typed relative address 唯一出现，并以相同 ID、parent、section、page level、parent Page 和 sibling order连续稳定两次。该门同时避免在 descendant Page 尚未加载时过早进入 ID rebind/内容验证。
- 纯测试已覆盖单 Page snapshot 不调用 `get_page_objects`、缺失 Page 必须等待、连续两次稳定要求和 hierarchy 震荡拒绝；完整 manual-validation 纯测试为 `641 passed`，完整仓库回归为 `1013 passed`。

用户随后在 `all --use-cache` 中完成真实 `copy-page` 复验，证据为 `run-2026-08-14-00-20-12`：cache decision 为 `validated_hit`，两个 role 的声明层级均连续稳定两次后才进入完整内容验证；六个 case、9 个 fresh targets、cleanup/restore、双 Notebook 精确关闭和 template immutability 全部通过。

真实调用分类确认重复 Page XML 读取已消除：`get_page_objects=0`，场景工具调用从基线 `223` 降至 `170`，场景 bridge 从 `544` 降至 `494`，其中 `get_page_content` 从 `186` 降至 `156`。实际高于建议的 `150`/`115` 上界，原因不是恢复了同一 Page 的双读，而是当前增强合同增加了必要读取：`93 get_page_xml` 覆盖增长式受保护 Page 集，`49 get_tree` 覆盖 cache 双稳定、plan/read-back bookend 与 restore；`plan_copy`/`copy_page` 内部还需读取源内容。场景耗时为 `519.51s`，高于旧基线 `215.11s`，说明 OneNote 当次时延与新增安全门抵消了 I/O 降幅，因此不把耗时下降作为完成依据。

阶段一的目标和真实证据门已经满足。阶段二的 delta snapshot 会扩大“哪些历史 target 可以不再读取”的判断风险，本轮明确不实施；后续若仍需降本，应另开范围并保留当前全量保护合同。

后续在 TODO 030 范围内又补齐了一项与本 TODO 同方向但不改变其完成结论的复用：cache build 仍生成并验证权威内容基线；working copy 只打开一次并先完成轻量层级收敛；随后唯一一次完整 `scenario before` snapshot 同时承担 materialized 内容真实性复核和 mutation before 基线。Runner 通过 exact role/Notebook ID/digest 的单次 handoff 让 scenario 直接消费这份 snapshot，消除了 materialized validation 与 scenario before 之间整轮重复 hierarchy/Page 读取；未消费完全部 role 时 mutation 在调用前 fail closed。该实现仍只属于 manual validation，未引入阶段二 delta snapshot，也未改变生产工具契约。

## 背景

`copy-page` 具名人工验证不是单次 Copy smoke test，而是同 Section、跨 Section、跨 Notebook 三种目标范围与 root-only/subtree 两种范围组成的六 case 安全矩阵。它需要证明 fresh target identity、内容保真、相对层级、同名 anchors/source 不变、精确非永久 cleanup、双 Notebook restore 与 lifecycle close，因此大量调用本来就是只读取证，而不是重复执行 mutation。

TODO 015 最终用户真实成功证据 `run-2026-08-11-16-18-20` 提供了当前基线：

- 场景 MCP 共 `223` 次工具调用：`75 get_page_xml`、`75 get_page_objects`、`45 get_tree`、`12 plan_copy`、`6 copy_page`、`9 delete_page`、`1 health_check`；
- 场景 MCP 产生 `544` 次 bridge 调用；加上 lifecycle wrapper 的 `50` 次后总计 `594`；
- bridge 中 `316 get_hierarchy` 与 `186 get_page_content` 为只读；mutation 为 `9 create_new_page`、`18 update_page_content`、`6 update_hierarchy` 和 `9 delete_hierarchy`；
- 场景进程约 `215.11` 秒，六 case、9 个目标 Page、cleanup/restore 和双 Notebook close 全部成功。

页面证据量来自 5 个初始 Page，以及 initial、六次 after、restored 共八个 bundle snapshot；各 snapshot Page 数为 `5/6/8/9/11/12/14/5`，合计 70。fixture live validation 再读取初始 5 Page，因此 `get_page_xml` 与 `get_page_objects` 各 75 次。当前两个工具都会触发 `GetPageContent`；后者重新读取同一 Page XML，只为生成已可从前一次 XML 解析的内容对象投影，是最明确的首阶段重复 I/O。

## 目标

在不削弱 TODO 015 已闭合安全合同和证据可审计性的前提下，减少 Page Copy 人工验证中的重复 MCP/COM 只读调用、运行时间和 OneNote COM 压力。优先优化 snapshot evidence plumbing，不改变生产 `copy_page` 的公开语义。

## 不可削弱的证据门

- 六个 case 和 `3 destination scopes × 2 subtree modes` 矩阵保持不变；不得以减少 case、只验证 root-only 或拆掉同名 anchors 换取调用下降。
- 每个 case mutation 前仍要求连续两次相同的只读 plan digest；不稳定时最多三次并 fail closed，不能缓存旧 plan 代替当前计划。
- 每个 bundle snapshot 仍需在 Page evidence 读取前后验证双 Notebook hierarchy ID 集稳定；不能删除并发/后台变化检测。
- 每个 case 仍需证明全部新 target IDs fresh、互异、与 before/source/anchors 不相交，目标 Section、order、level 和 derived parent 正确。
- source Parent/Child、跨 Section anchor、跨 Notebook anchor，以及此前已经创建且仍应保持不变的 Copy targets，继续按其适用合同验证拓扑、稳定内容和对象身份。
- 默认 cleanup 仍按精确 ID、非永久、叶到根/反向 case 顺序执行；最终仍证明全 bundle identity/topology、Page 对象/能力、四个保护页内容、cache immutability 和双 Notebook lifecycle close。
- 任一证据缺失、解析不完整、ID 集变化或新旧 evidence 不一致时必须 fail closed；不能用“性能优化”降级为 warning。

## 建议实施阶段

### 阶段一：复用单次 Page XML

1. 在 `capture_snapshot()` 中只调用一次 `get_page_xml(page_info=all)`，从同一返回 XML 本地生成：stable/canonical/reparent/raw hashes、capability projection 和 normalized Page object evidence。
2. 复用生产 Page parser/Copy vocabulary，不在 manual-validation 中维护第二套 XML 语义。若现有 `get_page_objects` 含有不能从 XML 等价生成的字段，先明确字段来源并设计 content-free snapshot response，而不是静默丢弃。
3. 为 XML-derived object projection 与当前 `get_page_objects` 结果增加纯自动化 parity fixtures，覆盖 Outline/OE、Image、Table/Row/Cell、List/Tag，以及已支持的 attachment/media 类型。
4. evidence artifact 继续保存 normalized object identity，不保存或扩散原始正文；调用日志继续只记录字符数和 hash。

按当前基线，单独移除 75 次重复 `get_page_objects`，理论上可将场景 MCP 调用从 `223` 降至约 `148`，场景 bridge `GetPageContent` 从 `186` 降至约 `111`。这些数字是目标基线，不是允许删除其他安全检查的配额。

### 阶段二：有界 snapshot/delta 评估

1. 分析是否能用一个显式 evidence set 表达每轮必须重读的 protected Pages、历史 targets 和本轮新 targets，同时保留对意外新 ID 与不相关拓扑变化的完整双 Notebook tree 检查。
2. 只有在自动化证明与当前全量 snapshot 等价后，才允许避免读取与本轮 mutation 无关且已有独立稳定证据的 Page content。
3. 若历史 Copy targets 必须在后续 case 中继续证明未被触及，则继续重读；不得因其不是 manifest fixture Page 而从保护集合移除。
4. 评估 snapshot-specific 内部批处理接口时，保持 public tool profile 不扩大、raw XML 不公开、单 MCP process 和 local-only 边界不变。

阶段二是可选的进一步优化；阶段一完成后若收益已足够，可以单独关闭本 TODO，不为追求更低数字扩大实现风险。

## 自动化验证

- `capture_snapshot` 合同测试证明每个 Page 每次 snapshot 最多触发一次 `GetPageContent` 等价读取，不再串行调用 `get_page_xml + get_page_objects`。
- parity 测试证明旧/新 normalized object evidence 在所有当前验证内容类型上等价；未知节点、缺字段或解析失败均拒绝 snapshot。
- invocation-count 测试冻结六 case 编排中的 plan、Copy、tree stability、cleanup 和 restore 门，防止以后以减少安全检查制造表面性能提升。
- 现有 `copy-page` scenario 测试、manual-validation 全套纯测试、完整 pytest、`copy-page --use-cache --dry-run --json` 和 `git diff --check` 全部通过。
- 变更若触及公开 tool/response，则必须同步设计文档、README 与 tool schema；首选实现应保持公开合同不变。

## 真实验证与度量

只有用户可以运行真实 `copy-page --use-cache`。Agent 不得执行真实 scenario，只能检查用户生成的 evidence。

完成证据至少记录：

- 顶层 `passed`、六 case 全部 `verified=true/lossless=true`、9 个 fresh targets、cleanup/restore、双 Notebook close 和 cache template unchanged；
- 单 MCP process 保持不变；
- MCP tool count、scenario/lifecycle bridge count、按 tool/operation 分类的计数，以及 scenario process seconds；
- 阶段一目标：`get_page_objects=0` 或等价的零次重复 Page XML COM 读取，MCP tool count 不高于 `150`，场景 bridge `get_page_content` 不高于 `115`；若因新增的必要安全证据超过目标，必须解释差异，不能删除安全门凑数；
- 与 `run-2026-08-11-16-18-20` 的 `223/544/215.11s` 场景基线对照。耗时受 OneNote/机器状态影响，只作观察指标，不作为牺牲正确性的硬失败门。

## 非目标

- 不优化生产单次 `copy_page` 的语义或放宽 Copy/Move policy。
- 不减少六 case、同名碰撞、subtree、跨 Notebook 或 cleanup/restore 覆盖。
- 不把 raw Page XML 写入新增日志、遥测或远程服务。
- 不直接读取、解析或修改 `.one` 文件。
- 不用 mock 调用下降冒充真实 OneNote 性能收益，也不要求 Agent 执行真实 mutation。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| XML-derived object projection 遗漏 `get_page_objects` 当前字段 | 先做字段级 parity；缺失即 fail closed，必要时设计一次读取的内部 snapshot response |
| 过度缓存掩盖后续 case 对历史 target 的意外修改 | 明确增长式 protected target 集；只有等价性测试证明后才缩减读取范围 |
| 调用数测试把实现锁死在偶然数字 | 冻结不可削弱门和上界，不冻结无意义的精确内部顺序；真实计数保留分类 evidence |
| 为批处理扩大公开 raw XML 或 tool profile | 优先 runner 本地复用；任何内部接口保持 content-free、least-privilege 和非公开 |
| 性能改动让失败 evidence 变少 | 保持原 artifact schema 或提供可追溯迁移，确保每个 case 仍能独立审计 |

## 依赖与关联

- [TODO 015](015_mutation_target_identity_hardening_and_duplicate_page_regression.md)：已闭合的 mutation identity、重名回归、保护对象和恢复证据合同；本 TODO 只能降本，不能重定义其成功边界。
- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：双 Notebook cache materialization、live validation、immutable template 和 lifecycle evidence。
- [`tests/manual_validation/README.md`](../../tests/manual_validation/README.md)：当前人工验证入口、权限与 Agent 禁止真实执行边界。
- [`tool_contracts.md`](../design/tool_contracts.md)：生产 Copy/Move 的当前公开合同。

## 完成定义

- snapshot Page evidence 在一次 Page XML 获取中生成 hash、capability 与 normalized object identity，或采用经证明等价且同样只读取一次的内部方案。
- 自动化 parity、fail-closed、调用上界与六 case 安全门测试通过，未减少计划稳定、tree stability、保护对象、cleanup/restore 或 lifecycle 证据。
- 完整纯测试、dry-run 和文档同步通过；公开合同未意外变化。
- 用户真实运行增强后的原 `copy-page` scenario 并取得完整成功闭环。
- 真实 evidence 显示重复 Page XML 读取已消除，调用分类和运行时间相对 `16-18-20` 基线得到记录；若未达到建议上界，TODO 记录必要证据成本和最终决策。
