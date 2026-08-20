# Opaque cache working copy 中的嵌套 Section 不能假定可安全激活

> 状态：当前有效的工程经验<br>
> 观察日期：2026-08-20<br>
> 观察环境：Windows `10.0.26200.0` x64、OneNote Desktop `16.0.20228.20190`、本地进程外 COM、默认 `persistent_powershell` validation adapter<br>
> 当前 Manual Validation 契约：[`../design/manual_validation_scenario_fixture_architecture.md`](../design/manual_validation_scenario_fixture_architecture.md)<br>
> 人工验证边界：[`../dev/isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md)、[`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md)<br>
> 未解决工作：[`TODO 052`](../todo/052_nested_section_cache_crash_investigation.md)

## 结论

在当前观察环境中，把已经关闭的 Notebook template opaque-copy 为新的 working directory 后，`SectionGroup` 出现在 hierarchy、甚至其子 `.one` 的 `OpenHierarchy` 返回 ID，都不足以证明该嵌套 Section 已经可安全使用。用户执行的多个 `--use-cache` scenario 出现了两种失败：一类在 parent-relative 打开 Group 子 Section 时使 `ONENOTE.EXE` 在 `onmain.dll` 内发生访问冲突并退出；另一类成功取得全部 Group/Section ID，但 Section 内声明的 Page 在整个有界 convergence 窗口中始终缺失。

问题原因仍未知。现有证据既不能证明 immutable template bytes 损坏，也不能证明常驻 PowerShell client 是必要原因。由于失败可能直接终止 OneNote Desktop，manual validation 当前把所有已知会形成 `SectionGroup → Section` 的 programmatic Recipe 标记为 fresh-only；显式 `--use-cache` 在 GUI probe、run directory、cache lookup、Notebook lifecycle 和 MCP 启动之前 fail fast。该决策是测试基础设施的临时安全门，不是 OneNote 的通用平台结论，也不改变生产 tool 契约。

## 真实观察

用户在同一环境中连续执行了多组隔离 scenario，保存证据显示：

- `rename --use-cache` 与 `move-section-group --use-cache` 在打开各自 Group 下的唯一 Section 时，先得到 `0x800706BE/RPC_S_CALL_FAILED`，随后得到 `0x800706BA/RPC_S_SERVER_UNAVAILABLE`；业务 mutation 尚未开始。
- Windows Application Error/WER 将这些退出记录为同一签名：`ONENOTE.EXE`、`onmain.dll`、异常 `0xc0000005`、错误偏移 `0x00000000000ab0e3`。这证明 RPC 错误是 OneNote 进程崩溃后的表现，而不是单纯的 bridge transport timeout。
- `copy-section-group --use-cache` 能激活 source/destination 的 Group 与子 Section，但 source Section 内两个声明 Page 连续 16 次观察均缺失，最终在 fixture convergence 阶段 fail closed；对应时间没有 OneNote crash event。
- `query --use-cache` 能依次激活两层 SectionGroup 和最深层 Section，但最深 Section 内四个声明 Page 连续 16 次缺失，同样在 mutation 前 fail closed，且没有 crash event。
- `copy-notebook --use-cache` 对同一 cache fingerprint 和相同 byte inventory，一次在 Group 子 Section 激活时以相同签名崩溃，另一次完整通过。这削弱了“固定 template 文件损坏”的解释，并说明该 shape 的结果至少受尚未识别的运行状态或时序影响。
- `reorder-section`、`reparent-section` 与 `reparent-section-group` 的部分 cache run 在同一环境中通过。`SectionGroup → Section` 因而是当前风险边界，而不是已证明的充分崩溃条件。
- 同期 fresh fixture 路径由当前 OneNote 会话程序化创建并保持 live identity，没有复现相同 cache materialization failure。

上述事实来自用户显式启动的真实 runs 和本机 Windows event evidence。pytest、mock 与 `--dry-run` 只证明新增 fail-fast 合同，不构成 OneNote 行为证据。本文不记录 Page 正文、用户 Notebook 名称、真实对象 ID、用户路径或原始 artifact。

## 已排除或尚未证明的解释

### 不能归因为固定的 cache byte 损坏

失败前后的 template/working inventory 一致；相同 fingerprint/byte inventory 的 `copy-notebook` 既出现过崩溃也出现过成功。一次 working activation failure 不应自动 quarantine 或删除 immutable entry。

### 不能归因为 scenario mutation

崩溃和 Page 缺失都发生在 fixture materialization/validation 阶段，公开 Copy、Move、Rename 或 Query 业务动作尚未执行。场景名称只决定了触发该 hierarchy shape 的 Recipe，不是已证明的业务根因。

### 不能仅凭当前证据归因为 persistent client

失败均使用默认 `persistent_powershell` adapter，但同一 adapter 也完成过平层 cache 和部分嵌套 cache run。尚无同一最小 workload 在 persistent 与 one-shot adapter 下的受控真实 A/B，因此不能把 host 生命周期写成已证实根因。

### Group 可见或 Section ID 返回不是 readiness 证明

崩溃案例中 parent Group 已以正确 type/path/parent 出现在 Notebook hierarchy；fixture convergence 案例中子 Section 也已返回并回读到精确 ID。两者都没有保证 OneNote 内部的子 `.one` 重绑定、TOC/index 状态和 Page hierarchy 已完成。

## 当前设计决策

1. 已知会在任一 Notebook role 中形成 `SectionGroup → Section` 的 programmatic Recipe 设置 `supports_cache=False`，并共享明确的 fresh-only reason。
2. 真实 `--use-cache` 在任何 OneNote、cache 或 filesystem side effect 前拒绝；`--dry-run --json` 返回 `rejected_fresh_only`、零 MCP start 和空 allowed operations。
3. Fresh execution 继续可用，因为当前真实证据没有把 fresh fixture 与上述 crash 签名关联起来。
4. 不用自动 sleep、重复 child activation、自动重启 OneNote、猜测关闭 Notebook 或扩大 retry 作为修复。请求是否已送达以及 OneNote 是否已改变状态不明时必须保留现场并 fail closed。
5. 不因 run-local COM/activation failure 自动失效或删除 immutable cache template；cache ownership、inventory 与清理规则保持独立。
6. 是否重新开放这些 Recipe 的 cache 由 [`TODO 052`](../todo/052_nested_section_cache_crash_investigation.md) 跟踪，必须先完成最小复现、adapter A/B、OneNote/Office 版本矩阵和生产调用可达性审计。

## 生产代码边界

Fixture cache、working directory opaque copy 和 Recipe materialization 属于 `tests/manual_validation/`，不是公开 MCP 功能。生产 PowerShell host 虽包含供 lifecycle 使用的 `open_hierarchy_batch` backend operation，但当前证据不能仅凭目录归属断言崩溃触发面绝不可能进入生产调用链。TODO 052 必须审计所有 `OpenHierarchy` 调用来源、参数形成方式和 public-tool reachability，并建立负向合同，确保未经证明的 copied nested path/parent-ID 组合不会因共享 bridge 实现泄漏到生产请求。

OneNote 已经退出后的 persistent proxy 刷新由 [`TODO 051`](../todo/051_persistent_com_client_restart_refresh.md) 单独跟踪；它解决的是 crash 后 client state，不能替代 TODO 052 对 crash trigger 的隔离，也不能授权自动重放未确定请求。

## 适用边界

本经验只覆盖上述单一 Windows/OneNote build、当前 manual-validation cache 格式和已保存的 scenario 矩阵。它不证明所有 OneNote 版本都会崩溃，不证明所有嵌套 Section 都会失败，也不证明 one-shot client 安全。当前实现契约以 [`manual_validation_scenario_fixture_architecture.md`](../design/manual_validation_scenario_fixture_architecture.md) 为准；本 Lesson 只解释临时门限的证据与原因。
