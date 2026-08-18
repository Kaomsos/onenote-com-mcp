# 缓存 Fixture 驱动的操作验收指南

> 级别：推荐实践（Recommended Practice）
> 读者：亲自在前台终端运行真实 OneNote manual-validation Scenario 的用户
> 本页不创建新的公开命令；实际可用 Scenario、参数和权限以 [`README.md`](README.md) 为准。

推荐把一次需要 UI 确认的真实验收理解成四步：

```text
从已验证 cache materialize 全新 fixture working copy
→ 执行本次具名 Scenario 声明的待验证操作
→ Runner 完成自动 read-back 与 machine comparison
→ 用户检查精确对象并输入 run-bound ACCEPT 或 REJECT
```

这套流程不限于 Copy。只要仓库已经提供相应的具名 Scenario、自动 comparator 和人工 verdict 阶段，也适用于 Reorder、Reparent、Move、非永久 Delete、格式/富内容转换等操作。不要把本文中的占位名称当作可直接运行的命令，也不要手工组合多个 Scenario 来制造未注册操作。

开发者如何设计和接入这条证据链，见[缓存 Fixture 驱动的真实操作验证推荐实践](../../docs/dev/cached_fixture_operation_validation.md)。

## 开始前确认

- 只使用 Scenario 创建的 disposable Notebook 和 synthetic 内容，不打开或选择业务 Notebook。
- 先在 [`README.md`](README.md) 确认目标 Scenario 已注册、fresh 还是 `--use-cache` 路径、人工输入格式和最终 cleanup 行为。
- 关闭或处理之前失败 run 明确列出的 working Notebook；不要按相似名称猜测，也不要删除其本地目录。
- Cache template 不应出现在 OneNote UI 中。你看到并操作的必须是本次 run 名称对应的 working Notebook。
- Agent、pytest、CI、hook 和后台任务不能代替用户运行真实命令。

## 第一步：先看 dry-run

对实际具名 Scenario 运行同参数的 dry-run：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> --use-cache --dry-run --json
```

检查输出中的：

- Scenario 名称和有序阶段；
- Notebook roles、working 名称和 run directory；
- cache 模式、固定 recipe/instance，以及 miss 时要求的 fresh authoring 路径；
- policy、tool allowlist、Copy/Move budget；
- 是否要求交互输入、timeout 和人工 verdict；
- lifecycle 是默认 restore/cleanup/close，还是显式 `--keep-worksite`。

`--dry-run` 不会访问 cache、启动 MCP、打开 OneNote 或读取 stdin。它只能证明计划和静态合同正确，不能代替真实验证。

## 第二步：Interactive 场景选择 fresh 或 cache 路径

Interactive/UserAuthored 场景只有统一入口 `interactive-<operation>`，不再存在独立的 bootstrap 命令。

**Fresh 路径（不带 `--use-cache`）** 在同一次 run 内完成 authoring、template 发布、working copy materialize 与 scenario 执行。推荐顺序：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <interactive-scenario> --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py <interactive-scenario>
```

真实 fresh run 中：

1. 只编辑终端显示的精确 Canvas 或 authoring zone；
2. 只添加说明要求的 synthetic 对象和数量；
3. 等待 OneNote 完成落盘后，输入终端显示的 run-bound confirmation；
4. 阅读 detector 的 requested/observed/missing/unexpected 摘要；
5. 只有 UI 结果正确时才输入该 run 的 `ACCEPT`；否则拒绝或让场景失败并保留现场。

成功后 runner 会先验证 authored snapshot/detection，再精确关闭 authored bundle、发布 immutable template，并在 fixture 阶段 materialize 第二份 working copy 完成 live validation，随后自动进入 scenario 阶段。Fresh 路径禁止传入 `--template-instance-id`。

**Cache 路径（带 `--use-cache`）** 跳过 bootstrap，直接从 ready template materialize 新 working copy。若 cache miss，错误会提示不带 `--use-cache` 重新 authoring，而不是隐式重建：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <interactive-scenario> --use-cache --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py <interactive-scenario> --use-cache
```

需要显式 instance 的场景（如 `interactive-move-page-content`、`interactive-user-authored-fixture` 在多个 ready instance 并存时）按 README 传入 `--template-instance-id authored-<24 hex>`；恰好只有一个 ready、mutation-eligible 且 fingerprint 匹配的 instance 时可自动选择。

## 第三步：运行待验证操作

Interactive fresh 路径在 bootstrap 与 fixture 阶段完成后自动进入 scenario；cache 路径 materialize 完成后同样进入 scenario。非 Interactive 场景则按各自 README 说明运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> --use-cache
```

运行期间不要在 OneNote 中修改 fixture，除非该 Scenario 明确要求。Runner 会依次完成：

1. materialize 本次 run 独有的 working Notebook bundle；
2. 打开声明的 Section/SectionGroup，按 live ID 重绑定 manifest；
3. 重新验证 fixture，证明 cache template 没有被打开；
4. 捕获 before、稳定 plan 和 confirmation；
5. 在最小权限下执行一次待验证操作；
6. 读取 after 并写 machine comparison；
7. 只有达到该场景的可人工评审状态后，显示精确 source/target 和 verdict 短语。

不要在 operation 执行中断、COM 卡顿或 partial failure 后自行重跑相同命令。先读取终端错误和 `run-failure.json`；mutation 不会由 Runner 自动重试，现场可能需要人工核对。

## 第四步：做人工检查并给出 verdict

终端会显示与本次 run 绑定的短语，通常形如：

```text
ACCEPT <run-id> <capability-or-operation>
REJECT <run-id> <capability-or-operation>
```

必须完整输入当前终端给出的短语，不要复用上一 run 的文字。

人工检查应按操作选择项目：

| 类别 | 检查内容 |
| --- | --- |
| 视觉/格式 | source 与 target 的文字、表格、图像、墨迹、形状、公式、位置、尺寸和空白是否符合预期。 |
| 媒体/交互 | source 与 target 是否都可播放或交互；时长、控件和可见状态是否一致。 |
| 身份/范围 | 终端列出的精确 target 是否属于预期 Notebook/Section；root-only 是否没有带入后代。 |
| Reorder/Reparent | 对象是否位于预期父级和顺序；未涉及对象是否保持原位。 |
| Copy | target 是否完整可用；source 和 collision anchors 是否仍未改变。 |
| Move | target 先完整可用，随后源才从活动树消失；不要把 Copy 成功单独当作 Move 成功。 |
| Delete | 只有精确 disposable 目标被非永久删除；其他对象仍活动。 |
| 多 case | 前一个 target 在后续操作后仍保持正确，不被第二个 case 改写。 |

选择规则：

- UI 与机器结论都符合预期：输入当前 run 的 `ACCEPT`；
- 视觉、播放、位置、范围或删除状态存在任何疑问：输入 `REJECT`；
- 无法确定目标、OneNote 尚未稳定或终端证据不完整：不要猜测接受，让场景超时/失败并保留现场。

人工 `ACCEPT` 不会覆盖 machine comparator 失败。如果终端已报告机器门失败，即使 UI 看起来正常，也应把该 run 当作诊断证据，而不是成功验收。

## 运行结束后检查证据

至少核对本次 run 目录中的以下职责证据；具体文件名可能按 Scenario 加 case 后缀：

- `cache-materialization.json`：命中/构建决策、working/template paths、`opened_template=false`；
- `cache-structure-remap.json`：template IDs 到 live working IDs 的映射；
- `fixture-result.json`：本次 working fixture 的 live validation；
- `before*.json`、`plan*.json`：操作输入与稳定计划；
- `copy-result*.json`、`move-result*.json` 或其他 operation result：真实调用结果；
- `machine-comparison*.json`：自动比较的逐项结论；
- `human-acceptance.json`：当前 run 的 accepted/rejected verdict；
- `worksite.json` 或 `restored.json`：保留现场或恢复/清理结果；
- `run-result.json` / `run-failure.json` 和 `report.md`：顶层结论与人工接管说明。

只有以下条件全部满足时，才把需要人工判断的 run 报告为通过：

```text
fixture live validation passed
+ operation reached its declared final state
+ machine comparison passed
+ human verdict accepted
+ scenario-required restore/cleanup/lifecycle passed
```

某些探索性 Scenario允许保存“机器已完成但人工拒绝”或“partial result 可供比较”的证据；这仍不是生产 allowlist、Move 源删除或完成状态的放行依据。

## `--keep-worksite` 的使用

需要在 verdict 后继续查看现场时，先对**同样参数**做 dry-run，再显式加入：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> --use-cache --keep-worksite --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py <scenario> --use-cache --keep-worksite
```

它只在 Scenario 自身的 read-back invariant 通过后保留动作现场，不扩展权限。成功后：

- working Notebook 保持打开；
- 应跳过的 restore/cleanup 由 Scenario 明确记录；
- `worksite.json` 给出精确 IDs 和人工清理说明；
- cache template 不会被 working mutation 更新；
- Cache 不维护 run working lease；独立 working run 不阻止 invalidation/cleanup，只有 template 自身的实际路径仍被 OneNote 打开时才拒绝。

查看完后，在 OneNote 中关闭 `worksite.json` 指定的精确 disposable Notebook。不要删除 run directory 或 Notebook 文件夹，也不要让名称相似的另一个 Notebook代替目标。

## 失败时如何停下来

| 错误 | 用户动作 |
| --- | --- |
| `interactive_cache_miss` | 使用同一 `interactive-<operation>` 命令并移除 `--use-cache` 重新 authoring；不要尝试从外部 Notebook 补 cache。 |
| active/live ID 或 path 冲突 | 根据旧 run ID 和 working paths，关闭精确 working Notebook 后再重试。 |
| hierarchy activation / ID rebind 失败 | 保留 working files/evidence，默认精确关闭本次 working Notebook；不要修改 cache template，不要删除 working 目录。 |
| detector missing/unexpected | 检查是否编辑了错误 Canvas、数量不对或 OneNote 生成了不同公开 `kind`；保留 evidence。 |
| machine comparison 失败 | 输入 REJECT（若仍提示）或让 run 非零退出；不要用肉眼判断覆盖差异。 |
| operation partial failure | 查看 allocated/resolved/created IDs，按 `manual_recovery_required` 人工核对；不要自动重跑 mutation。 |
| EOF/timeout/错误 verdict | 当前 run 没有正向人工结论；保留 evidence，重新运行会创建新的独立 run。 |
| restore/cleanup/close 失败 | run 未闭环；保留 artifacts。若 exact close 证明失败，不得继续 `all` 或启动其他真实场景，按 failure handoff 处理。 |

任何失败都不得通过删除 working Notebook、普通 artifact、失败现场或用户 Notebook 文件/目录来“重置”。Fixture cache runtime 的精确 entry 清理是独立、受 marker/ownership/containment/open-state/lease 门保护的具名流程，不是人工文件清理授权。

## 汇报一次真实验收

建议向开发者提供以下摘要，而不是只说“看起来成功”：

```text
run_id:
scenario:
cache_decision:
recipe_version / fingerprint / template_instance_id:
working roles and Notebook IDs:
operation and cases:
machine_comparison: passed | failed（附 evidence 文件）
human_verdict: accepted | rejected
UI checks performed:
restore / cleanup / lifecycle:
OneNote / Office / Windows / timezone:
remaining worksite or manual cleanup:
```

不得从一个 Office/OneNote 环境的单次 ACCEPT 外推跨版本保证；环境信息和负向 evidence 与成功 evidence 同样需要保留。
