# 021：Windows Fixture Cache 路径长度预算

> ID：021
> 状态：待办
> 优先级：P3
> 类型：Manual Validation / Windows 文件系统兼容性
> 更新日期：2026-08-13

## 决策摘要

Fixture cache 已对 Windows 短暂文件扫描/共享冲突提供状态守卫的有界原子发布重试，但 cache fingerprint、template instance、staging、run 和 role-specific working 名称叠加后仍可能形成很深的物理路径。测试中已观察到较长 pytest 临时根会触发 `WinError 3`；短路径 basetemp 下同一测试集合通过。这是独立于 `WinError 5/32` 锁竞争的路径预算问题，当前优先级较低，不属于原子发布修复范围。

目标方案已经定稿，权威设计见 [Windows Fixture Cache 路径配额设计](../design/windows_fixture_cache_path_budget.md)：所有受管绝对路径限制为 `240` 个 UTF-16 code units；完整 identity 保留在 metadata/evidence，磁盘使用 32-hex fingerprint key、programmatic `p`、authored `a/<1..24 hex>`、16-hex staging nonce、最长 12 字符 role 和最长 64 UTF-16 units working name。方案采用一次性 schema 切换，不提供旧 cache/run 布局兼容；切换前由用户通过既有 human-gated `clear all` 清理旧 cache 与历史 runs。Implementation 尚未开始，本 TODO 继续保持“待办”。

## 2026-08-13 本机参考快照

以下信息只记录一次当前开发机上的观察结果，用于后续计算路径预算和复现边界；它不是跨机器支持合同，也不表示 TODO 已完成：

- 系统：`Microsoft Windows NT 10.0.26200.0`，OS 与测试进程均为 `X64`；
- 系统长路径开关：`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` 为 `REG_DWORD 0`，因此本机未启用普通 Win32 long-path opt-in；显式 `\\?\` extended-length path 仍属于待评估范围；
- 项目工作目录：`E:\code\MCP\local-onenote-mcp`；
- Python/pytest：项目 `.venv` 中为 Python `3.13.12`、pytest `9.1.1`；
- 进程临时目录：`TEMP` 与 `TMP` 均为 `D:\Users\wt\AppData\Local\Temp`；
- pytest 未配置固定 `--basetemp`，默认根形如 `D:\Users\wt\AppData\Local\Temp\pytest-of-wt\pytest-<n>`；本次完整基线实际使用 `D:\Users\wt\AppData\Local\Temp\pytest-of-wt\pytest-805`，`pytest-current` 符号链接指向该目录；
- 默认命令 `.venv\Scripts\python.exe -m pytest -q` 在上述环境中得到 `845 passed, 1 warning in 21.42s`。唯一 warning 是仓库内 `.pytest_cache` 的 `nodeids` 写入遇到 `WinError 5`，不属于 `tmp_path` 的 `WinError 3` 路径长度失败。

该次通过只证明当次测试生成的具体路径未触发失败；由于 `LongPathsEnabled=0` 且最坏路径预算尚未建立，不能据此排除更长测试名、cache identity、role 或 artifact 组合仍超过传统 `MAX_PATH`。

## 已定稿路径合同

- 使用普通绝对 Windows 路径，统一上限为 `240` UTF-16 code units；不依赖 `LongPathsEnabled`，不采用 `\\?\` extended-length path；
- fingerprint logical identity 保持完整 64-hex SHA-256，磁盘目录只使用前 32 hex；短键命中后必须核对完整 identity，碰撞 fail closed；
- programmatic instance 的磁盘路径为 `instances/p`；user-authored 为 `instances/a/<1..24 lowercase hex>`，完整逻辑 ID/projection digest 保存在 metadata/evidence；
- role 使用 `[a-z][a-z0-9_-]{0,11}`；working leaf name 最多 64 UTF-16 units；
- publish staging 使用 `.s-<16 hex>`，materialize staging 使用 `.m-<16 hex>`，原子 metadata 临时文件使用 16-hex nonce；
- opaque relative path 静态上限为 96 UTF-16 units、最多 8 层，但最终允许值取全部必需派生路径的剩余预算最小值；
- 最终 cache、publish staging、materialize staging、working copy、inventory/artifact 和原子临时文件都必须在任何文件复制、发布或 COM 调用前完成 preflight；
- 不实现旧 cache/run 布局的 lookup、迁移或 maintenance 兼容；用户在切换前清理旧 payload，新 runtime 遇到任何残留旧布局时 fail closed；
- `WinError 3` 不重试；现有只处理 `WinError 5/32` 的状态守卫重试保持独立。

## 具体实现方案

### A. 公共预算与 identity 模型

1. 新增按 UTF-16 code units 计数的公共 path-budget helper，以及 `MAX_MANAGED_PATH_UNITS = 240`；
2. 将完整 logical fingerprint、32-hex disk key、logical instance identity 和 typed disk location 建模为不同字段，禁止继续把目录名等同于完整 identity；
3. 移除当前宽泛 `INSTANCE_PATTERN`，新 schema 只接受 `p` 或 `a/<hex>` typed location；
4. role 长度在 Recipe identity 建立时验证；working name 在 run identity 冻结时和 materialize preflight 中双重验证；
5. path evidence 记录 limit、longest path、remaining、kind 和 pass/fail，不记录 Page 正文；
6. 定义稳定的 `path_budget_exceeded` 错误合同，同一失败同时投影为醒目的终端 `ERROR`、`--json` 结构化错误和可写时的 run failure evidence，三者字段语义不得漂移；
7. 错误必须包含 phase、target kind、limit/actual/over-by、触发路径、非零退出状态、副作用声明，以及 typed `remediation.code` 和面向用户的 `remediation.message`。

### B. Cache publish/lookup/lock

1. 新 publish 写入 `<cache-root>/<fp32>/instances/p` 或 `<cache-root>/<fp32>/instances/a/<id24>`；
2. lock 位于 `<cache-root>/<fp32>/bundle.lock.json`，内容保留完整 fingerprint；
3. lookup 先用短键定位，再以 index、entry 和完整 identity 精确核对；不同完整 fingerprint 或 projection digest 共用短键时拒绝；
4. publish staging 改为 `<cache-root>/.s-<nonce16>`，exclusive create 冲突时重新生成；
5. copy 前枚举完整 source inventory，并验证最终 entry、staging、artifact 与原子临时文件的全部目标路径；
6. 失败不得截断 identity、改名 opaque Notebook 文件或以 `WinError 3` 重试。

### C. Materialize 与 working copy

1. materialize staging 改为 `<run-root>/.m-<nonce16>/<role>`；
2. working path 保持 `<run-root>/notebooks/<working-name>`，working name 上限降为 64 UTF-16 units，完整可读身份留在 evidence；
3. materialize 前同时预算 template source、staging copy、最终 working、inventory 和 evidence 临时路径；
4. 路径通过后才允许 opaque copy、atomic publish 和 OneNote open；实际打开路径仍必须精确等于 working path且不等于 template path。

### D. Schema 切换与 maintenance

1. 在实施新 schema 前，由用户使用仍支持当前布局的版本完成 `clear all` dry-run、交互确认和真实清理，清除旧 cache 与历史 runs；
2. 只有既有 receipt/summary 证明目标全部成功且受管 validation root 不含旧 payload，才进入新 schema 切换；
3. 新 runtime、lookup 和 maintenance 只识别新短键 schema，不提供 legacy parser、迁移、fallback 或删除能力；
4. 发现旧 marker/index、64-hex fingerprint 目录、full-instance 目录或旧 run metadata 时 fail closed，并提示用户回到升级前版本清理；
5. 新 schema 的 discovery、target assessment、index tombstone 收敛和 empty scaffold pruning 仍必须由 schema/typed path/完整 metadata 相互证明；
6. Agent/pytest 仍只能运行 maintenance `--dry-run`，真实 clear 权限不变。

### E. Pytest dummy fixture

1. 保留 `Open Notebook.onetoc2`、`Section.one` 等真实目录形状，不通过缩短 payload 名称掩盖风险；
2. 深层 cache/maintenance 测试改用 `tmp_path_factory.mktemp("fc")` 分配唯一短根；需要 canonical maintenance 结构时使用 `<short-root>/w/.local-validation/fixture-cache`；
3. 该 fixture 必须进程内唯一、并发隔离、可丢弃，且不得指向仓库、真实 validation workspace、用户 Notebook 或已有 evidence；
4. 默认 pytest 不需要手工传 `--basetemp`；短 `--basetemp` 仅保留为诊断复验工具。

## 自动化验证清单

- UTF-16 计数覆盖 ASCII、BMP 和 surrogate pair，精确验证 `239/240/241` 边界；
- programmatic `p`、authored `a/<24hex>`、32-hex fingerprint key 和最长 role/working name 路径；
- fingerprint/instance 短键碰撞、entry/index/lock 完整 identity 不匹配全部 fail closed；
- publish、lookup、inventory、materialize、quarantine、invalidated rebuild 和 immutable template verification；
- `.s-<16hex>`、`.m-<16hex>`、JSON/XML 原子临时文件和 cleanup-failure 保留；
- maintenance discovery、exact target、新 schema receipt、index tombstone 与 scaffold pruning；旧 marker/index/fingerprint/instance/run 残留必须覆盖 fail-closed 拒绝测试；
- opaque relative path component、总长、深度和每类派生目标的边界；
- 每一种预算失败都输出醒目的终端 `ERROR`，不得静默、不得降级为 warning，也不得只返回裸 `WinError 3`；
- `--json` 稳定返回 `ok=false`、`error_type=path_budget_exceeded`、phase、target kind、limit/actual/over-by、触发路径、副作用字段和 typed remediation；
- cache root、run root、opaque hierarchy、typed role/working/fixed layout 与旧 schema 残留分别返回准确、可执行且不放宽安全门限的修复指导；
- 终端、JSON 与可写的 failure evidence 对同一错误保持字段一致；evidence 路径也超预算时仍返回结构化错误并证明 `failure_evidence_written=false`；
- 所有预算失败均为非零退出，并证明 staging/cache entry/OneNote/mutation 等未发生副作用；
- `WinError 3` 单次失败且零退避，`WinError 5/32` 继续遵守既有状态守卫；
- 默认 `.venv\Scripts\python.exe -m pytest -q` 在不传 `--basetemp` 时通过。

## 非目标

- 不因路径较长而放宽 cache ownership、containment、reparse-point 或 actual-open-path 门禁；
- 不截断或碰撞 cache identity，也不按名称、mtime 或目录顺序猜测实例；
- 不直接编辑 `.one`/`.onetoc2`，不改变 local-only 边界；
- 不把 pytest 临时目录设置扩展为生产 MCP 配置或公开环境变量；
- 不以重试 `WinError 3` 代替确定性的路径预算修复。

## 完成定义

- [目标设计](../design/windows_fixture_cache_path_budget.md) 已由实现强制执行，所有必需路径在复制、发布或 COM 调用前完成 240 UTF-16 units preflight；
- 32-hex fingerprint disk key、`p`/`a` instance layout、role/working/staging/opaque path 配额均已落地，完整 identity 保存在新 schema metadata/evidence；
- 用户已确认升级前 `clear all` 的旧 cache/run 清理结果；新 runtime 对任何旧布局残留 fail closed；
- publish、materialize、lookup、失效与新 schema maintenance 在 `239/240/241` 和碰撞边界上有纯测试；
- 预算失败在普通终端和 `--json` 两种模式下都有明确、稳定、自动化覆盖的错误；错误显示实际配额差值、未发生的副作用，并按失败类别给出可执行修复指导；
- 默认 pytest 使用自动分配、并发隔离的短 cache fixture root，无需手工 `--basetemp`，且不写入用户 Notebook 或任意外部未管理路径；
- `.venv\Scripts\python.exe -m pytest -q` 默认全量通过，且没有把短路径复验表述为默认基线通过；
- 文档明确项目不采用 extended-length path；若未来改变该决策，必须另行审查身份、containment、COM 交互和错误报告语义。

## 关联

- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：已完成的 immutable fixture cache 架构与真实验收记录；本 TODO 不改变其完成状态。
- [Windows Fixture Cache 路径配额设计](../design/windows_fixture_cache_path_budget.md)：本 TODO 的权威目标布局、配额公式、identity 和一次性 schema 切换合同。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：当前 cache 使用方式、安全边界与本地原子发布重试合同。
- [当前架构](../design/architecture.md)：fixture cache、working copy 和 maintenance 的权威设计说明。
