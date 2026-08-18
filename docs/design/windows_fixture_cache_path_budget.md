# Windows Fixture Cache 路径配额设计

> 状态：当前实现合同
> 更新日期：2026-08-18
> 实施跟踪：[TODO 021](../todo/021_windows_fixture_cache_path_budget.md)
> 相关架构：[当前架构](architecture.md) · [Manual Validation Runner](../../tests/manual_validation/README.md)

## 1. 结论

Manual-validation 管理的 fixture cache、publish staging、materialize staging 和 working copy 统一采用普通 Windows 路径，并在任何复制、原子发布或 OneNote COM 调用前执行确定性路径预算。所有推导出的受管绝对路径不得超过 **240 个 UTF-16 code units**；传给 OneNote COM 用于 Notebook root create/open 的精确 working 路径另有基于真实 Fresh 双 Notebook create 证据的 **147-unit 安全兼容上限**。项目不依赖系统 `LongPathsEnabled`，也不使用 `\\?\` extended-length path。

磁盘布局采用短定位键，完整身份继续保存在 metadata/evidence 中：

- cache fingerprint 磁盘键固定为完整 SHA-256 的前 `32` 个小写 hex；
- programmatic instance 使用单字符磁盘键 `p`；
- user-authored instance 使用类型键 `a` 和不超过 `24` 个小写 hex 的 instance key；
- role 最多 `12` 个字符；
- publish/materialize staging 使用 `16`-hex nonce；
- working directory name 最多 `64` 个 UTF-16 code units，并根据当前 run root 的 OneNote 147-unit 预算确定性压缩；
- run evidence 最终文件名最多 `64` 个 UTF-16 code units，并为其 16-hex 原子临时名预留预算；
- OneNote 返回的 Notebook、SectionGroup、Section、Page 与内容对象 ID 是逻辑身份，不得进入任何受管物理文件名、目录名、working name 或临时名；artifact 使用固定语义 token 与有界 ordinal，完整 ID 只保存在 metadata/evidence；
- opaque Notebook 相对路径最多 `96` 个 UTF-16 code units，但最终可用值还受 240 总预算约束。

实现采用一次性 cache schema 切换，不兼容旧 64-hex fingerprint/full-instance 目录。切换前由用户使用升级前版本的 human-gated `clear all` 流程清理旧 cache 与历史 runs；新 runtime 只接受新 payload schema。唯一的过渡例外是：旧命令留下的 v1 marker/空 index 可在 durable `clear-all` 成功 summary、完整只读 open-path snapshot、零 refused/failed、零旧 payload/旧 run 残留共同证明后，由首次新 cache 初始化只重写为空的 ownership metadata；summary 之后创建且 state 明确为 v2 的 run 不会使该证明失效。任一证明不完整或存在旧 payload/旧 run 时直接 fail closed。

`WinError 3` 是路径预算失败，不进入面向 `WinError 5/32` 的原子发布重试。

## 2. 统一计数合同

预算对象是规范化、绝对、普通 Windows 路径。长度按 UTF-16 code units 计算，不使用 Python 字符数量近似：

```python
def windows_path_units(path: Path) -> int:
    return len(str(path).encode("utf-16-le")) // 2
```

全局合同为：

```text
MAX_MANAGED_PATH_UNITS = 240
MAX_ONENOTE_OPEN_PATH_UNITS = 147
```

计数不包含结尾 NUL。每个普通文件或目录 component 最多 `120` 个 UTF-16 code units。任一必需路径超过配额时，runtime 必须在 `copytree`、`os.replace`、inventory 写入或 OneNote COM 调用前 fail closed，并报告路径类别、root/固定布局/相对路径长度、实际总长和超额量。

## 3. 受管路径预算

下表中的 `<relative>` 是 immutable Notebook tree 内的 opaque 相对路径，包含其内部目录分隔符。它不得被重命名、截断或解析改写。

| 类别 | 目标路径结构 | 固定分项配额 | 总预算 |
| --- | --- | --- | --- |
| Fixture cache — programmatic | `<cache-root>/<fp32>/instances/p/notebooks/<role>/template-notebook/<relative>` | `fp32=32`、`p=1`、`role<=12`、`relative<=96` | `cache_root + 87 + relative <= 240` |
| Fixture cache — authored | `<cache-root>/<fp32>/instances/a/<id24>/notebooks/<role>/template-notebook/<relative>` | `fp32=32`、`a=1`、`id24<=24`、`role<=12`、`relative<=96` | `cache_root + 112 + relative <= 240` |
| Materialize staging | `<run-root>/.m-<nonce16>/<role>/<relative>` | staging name `19`、`role<=12`、`relative<=96` | `run_root + 34 + relative <= 240` |
| Cache publish staging | `<cache-root>/.s-<nonce16>/notebooks/<role>/template-notebook/<relative>` | staging name `19`、`role<=12`、`relative<=96` | `cache_root + 62 + relative <= 240` |
| Working copy | `<run-root>/notebooks/<working-name>/<relative>` | `working-name<=64`、`relative<=96` | 完整受管 tree：`run_root + 76 + relative <= 240`；Notebook root COM create/open：`<run-root>/notebooks/<working-name> <= 147` |
| Run evidence | `<run-root>/<evidence-name>` 与 `.<evidence-name>.<nonce16>.tmp` | `evidence-name<=64`、atomic suffix `22` | `run_root + 87 <= 240`（最坏临时名） |

“Run staging”不是独立的第三种 runtime 目录：发布 template 的 `.s-*` 位于 cache root；复制 working bundle 的 `.m-*` 位于 run root。

各类路径的实际 relative 配额取静态上限与当前 root 剩余预算中的较小值：

```text
programmatic cache = min(96, 153 - cache_root_units)
authored cache     = min(96, 128 - cache_root_units)
materialize staging = min(96, 206 - run_root_units)
publish staging     = min(96, 178 - cache_root_units)
working copy        = min(96, 164 - run_root_units)
```

其中公式按最长 role 和 working name 计算。实现仍应枚举每个实际目标，而不能只依赖公式估计。

Notebook root 的物理名称预算另按当前绝对 run root 动态计算：

```text
working_name = min(64, 147 - units(<run-root>/notebooks) - 1)
```

普通可读名称超出该预算时保留时间并加入 logical identity digest；更深但仍可支持的显式 run root 使用 `<12hex>` 的 12-unit 紧凑名称。若连 12-unit 名称也无法使 Notebook root 落入 147-unit 上限，则在任何 OneNote COM 调用前以 `onenote_open_path` fail closed，并要求改用更短的唯一 run directory。完整 scenario、role、cache/fresh 模式与时间仍保存在 run evidence 中。

## 4. 身份与磁盘定位键

### 4.1 Fingerprint

Recipe identity 继续产生完整 `64`-hex SHA-256。磁盘只使用前 `32` hex：

```text
logical fingerprint: 64 hex
disk key:             first 32 hex
```

完整 fingerprint 必须存在于 cache entry、index、lock、tombstone、quarantine、recovery、dry-run、manifest 和 report。以短键定位后必须读取 metadata 并精确核对完整 fingerprint；短键对应不同完整 fingerprint 时必须 fail closed，不得覆盖或猜测。

### 4.2 Instance

Programmatic recipe 在同一完整 fingerprint 下只有一个确定性 template，磁盘路径固定为：

```text
instances/p
```

现有逻辑 ID 可继续作为 evidence 字段，但不得重复进入物理路径。

User-authored recipe 允许同一 fingerprint 下存在多个显式实例：

```text
instances/a/<1..24 lowercase hex>
```

完整 `projection_digest` 继续保存为 `64`-hex SHA-256。Consumer 只接受显式 instance identity；短 instance key 命中后必须核对完整 projection digest，发生碰撞或 metadata 不一致时 fail closed。

Materialized authored bundle 的 live revalidation 必须重新计算完整 projection digest，并同时核对 entry 中的 64-hex digest 与 24-hex instance key；只比较 instance key 前缀不足以证明完整身份。

### 4.3 Role 与 working name

Role 使用：

```text
[a-z][a-z0-9_-]{0,11}
```

Working name 是 Windows-safe 单一叶名称，最多 `64` 个 UTF-16 code units，并受上述 Notebook root 147-unit COM 安全兼容上限进一步约束。完整 scenario、role、时间和显示信息保存在 evidence 中，不要求全部进入目录名；压缩 digest 同时绑定这些 logical identity 字段以保持 role 与 Fresh/Cache 唯一性。

## 5. Staging 与原子临时文件

Publish staging 使用：

```text
.s-<16 lowercase hex>
```

Materialize staging 使用：

```text
.m-<16 lowercase hex>
```

二者都必须以 exclusive create 分配；随机名称冲突时生成新 nonce，不得复用已有目录。Staging marker/entry 记录完整 logical identity，maintenance 只识别精确 typed name 与 ownership evidence。

JSON/XML evidence 的原子临时文件也属于路径预算。临时名应使用短 nonce：

```text
.<final-name>.<nonce16>.tmp
```

Preflight 必须覆盖最终文件和原子临时文件，不能只检查 Notebook payload。

## 6. Opaque Notebook tree preflight

Runtime 在复制前从 source tree 的 bounded inventory 计算：

- 单个 component 的 UTF-16 长度；
- relative path 的 UTF-16 长度和目录深度；
- 每个 role 在最终 cache、publish staging、materialize staging 和 working copy 下的完整绝对路径；
- runtime 自身将写入的 inventory、entry、artifact 和原子临时文件路径。

树遍历本身也受该顺序约束：runtime 每次只枚举已通过预算的当前目录，对发现的子路径先按 component、opaque relative、depth 和总长做 preflight，再执行 `stat`、进入子目录或读取文件。不得先用无界 `rglob`/`os.walk` 深入整棵树后才检查长度，否则 Windows 可能先返回裸 `WinError 3`。

Opaque relative path 的静态上限为 `96` UTF-16 units、目录深度上限为 `8` 层；最终实际上限由四类目标中最小的剩余预算决定。任何证明不完整或任一派生路径超过 240 都必须停止，且不得通过重试、截断 identity、改名 Notebook 文件或放宽 containment 来继续。

Path evidence 至少记录：

```json
{
  "limit_utf16": 240,
  "longest_path_utf16": 228,
  "remaining_utf16": 12,
  "kind": "authored_cache_template",
  "passed": true
}
```

内容无关的报告可以保存受管路径；不得保存 Page 正文或用户 Notebook 内容。

### 6.1 失败反馈与修复指导

Path-budget preflight 失败是明确的运行错误，不得静默退出、降级为普通 warning，或只暴露底层 `WinError 3`。同一次失败必须同时具备：

1. 面向交互终端的醒目 `ERROR: Fixture cache path budget exceeded`；
2. 面向 `--json`、run result 和自动化调用方的稳定结构化错误；
3. 非零退出状态；
4. 与失败原因绑定、可执行且不放宽安全门限的修复指导；
5. 对已经发生和明确未发生的副作用作出声明。

终端错误至少显示 phase、target kind、240 上限、实际长度、超额量、触发预算的受管/relative path，以及一条首选修复动作。例如：

```text
ERROR: Fixture cache path budget exceeded.
Phase: cache_publish_preflight
Target: authored_cache_template
Limit: 240 UTF-16 units; actual: 247; exceeded by: 7
Relative path: Group A\Group B\Section.one
No staging directory or cache entry was created; OneNote and scenario mutation were not started.
How to fix: move the repository to a shorter local path, or shorten the disposable fixture hierarchy, then start a new run.
```

结构化错误至少包含以下稳定字段：

```json
{
  "ok": false,
  "error_type": "path_budget_exceeded",
  "phase": "cache_publish_preflight",
  "target_kind": "authored_cache_template",
  "limit_utf16": 240,
  "actual_utf16": 247,
  "over_by_utf16": 7,
  "relative_path": "Group A/Group B/Section.one",
  "filesystem_changes_started": false,
  "cache_entry_published": false,
  "onenote_opened": false,
  "mutation_started": false,
  "remediation": {
    "code": "shorten_managed_root_or_fixture_hierarchy",
    "message": "Move the repository to a shorter local path, or shorten the disposable fixture hierarchy, then start a new run."
  }
}
```

修复指导必须按失败类别选择，不能统一返回模糊的“缩短路径”：

| 失败类别 | 必需修复指导 |
| --- | --- |
| cache root / repository path 过长 | 将仓库移动到更短的本地路径，再启动新 run；不得建议修改用户 Notebook 或启用任意外部 cache root |
| run root 过长 | 使用新的、更短且为空的唯一 `--run-dir`；不得复用已有 run/evidence 目录 |
| opaque relative path、component 或层级过长 | 仅调整本次 disposable fixture 的 SectionGroup/Section 文件层级后重新 build/bootstrap；runtime 不截断、不改名 |
| role、working name 或固定 metadata/staging 布局超限 | 报告为实现/声明合同错误，要求修正代码中的 typed name 或固定布局；不得要求用户手工修改 cache 文件 |
| 旧 schema 残留 | 要求回到升级前版本，通过既有 human-gated `clear all` 完成清理；新 runtime 不迁移或删除旧 payload，只能激活有 durable 成功 summary 证明的空 ownership 壳 |

如果 run evidence 路径本身也超预算，runtime 仍必须把完整结构化错误写到 stdout/stderr 或 JSON response，并明确 `failure_evidence_written=false`；不得改写到任意未管理临时目录。若能安全写 evidence，则保存相同错误合同，避免终端、JSON 和文件证据漂移。

## 7. Schema 切换与清理前置条件

当前实现不提供旧 64-hex fingerprint/full-instance 目录的 lookup、迁移或 maintenance 兼容。切换过程固定为：

1. 用户在仍支持旧 schema 的当前版本中，先 dry-run 审查既有受管目标；
2. 用户本人在交互式前台显式运行既有 human-gated `clear all`，清理旧 cache 与历史 runs；
3. 只有 durable `clear-all` summary 表明交互确认已发生、open-path snapshot 完整、目标全部成功，且 `.local-validation/` 中没有旧 payload/run 后，才允许切换；summary 后创建的 v2 run 必须以 schema、ownership flags、`started_at` 和 state 文件时间共同证明为 post-clear，不能被误判为旧残留；
4. 旧命令正常保留的 v1 cache marker、空 v1 index 与 history 文件构成“空 ownership 壳”。首次新 cache 初始化重新核对上述 summary、精确 managed roots、空 index、允许文件集合和时间顺序，然后先原子写 v2 空 index、再原子写 v2 marker；中途只完成 index stamp 时可根据同一 summary 安全续作；
5. 该激活不读取、移动、重命名或删除 legacy payload，也不迁移 index entry。存在 64-hex fingerprint 目录、full-instance 目录、旧 run、非空 index、未知文件、失败 summary 或身份不符时直接 fail closed，并要求回到升级前版本完成用户清理；
6. 新 runtime 只创建和读取短键布局，不增加绕过参数；新 maintenance 不获得 legacy discovery 或删除能力。

Maintenance 在创建 open lock 或获取 OneNote 的只读 open-path snapshot 前，先预算 validation/cache root、当前受管树、receipt、summary、index 及其原子临时路径；真实执行持锁后再复验一次。预算失败时不创建 lock、receipt 或 summary，也不调用 COM。

清理授权没有扩大：Agent、pytest、CI、hook 和后台任务只能执行 maintenance `--dry-run`；真实 `clear all` 仍只能由用户显式启动并现场确认。

## 8. Pytest dummy fixture

Dummy fixture 保留 `Open Notebook.onetoc2`、`Section.one` 等真实目录形状，不能为让测试通过而缩短 payload 文件名。深层 cache/maintenance 测试改用 pytest 自动分配的唯一短根，例如：

```python
@pytest.fixture
def cache_tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("fc")
```

需要验证 canonical maintenance root 的测试使用：

```text
<cache_tmp_path>/w/.local-validation/fixture-cache
```

该方案不共享并发目录，也不指向仓库或用户 Notebook。

预算边界测试应直接测试 preflight 计算器的 `239/240/241`、UTF-16 surrogate pair、最长 authored instance、role、working name、opaque path 和原子临时文件；不得依赖实际创建超长路径才观察失败。

## 9. 安全边界

路径缩短不改变以下合同：

- cache root ownership、containment、plain-tree 和 reparse-point 检查；
- template 从不由 OneNote 打开；
- working copy 每次物理独立并重新绑定 live identity；
- cache cleanup 只处理 exact owned entry/staging；
- 完整 identity 必须进入 metadata/evidence 并交叉核对；
- `WinError 5/32` 原子发布重试仍需状态守卫；`WinError 3` 永不重试；
- `.one`/`.onetoc2` 只做 opaque byte copy、hash 和 inventory，不直接编辑。
