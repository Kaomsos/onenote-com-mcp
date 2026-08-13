# 026：Manual Validation 实时进度与 Verbosity

> ID：026
> 状态：已完成
> 优先级：P2
> 类型：Manual Validation / CLI UX / 安全日志
> 更新日期：2026-08-13

## 目标

为具名 `run.py <scenario>` 增加 `quiet|normal|verbose` 三级实时、content-free 进度；具名场景默认 `normal`，`all` 保持默认 `quiet`。除显式 `--json` 外，终端不得再展开完整 run/scenario summary、Page 内容、XML、binary、参数或响应。

## 合同

- `quiet` 展示主要阶段和最终 PASS/FAIL；`normal` 增加 cache/fixture/case/mutation/restore 进度；`verbose` 增加 mutation timing、convergence/reconciliation 标量和批量 read 计数。
- `--json` 优先并保持机器可解析：具名场景输出一个完整 JSON document；`all` 保持 JSON Lines。
- 完整事实继续保存在 `run-result.json`、`run-failure.json`、`report.md` 和场景 evidence 中；终端 summary 始终有界。
- 不修改 mutation、evidence schema、exit code、失败保留或 lifecycle 安全门。

## 验证

- parser、事件过滤/顺序、redaction、紧凑结果、JSON兼容、`all` 透传/截断和 dry-run 零副作用均有纯合同测试；
- 完整 pytest、具名与 `all` dry-run 通过；
- 真实展示由用户前台运行 `copy-page --verbosity normal` 和 `rename --verbosity verbose` 确认，Agent 不执行真实 scenario。

## 实现进度

- [x] 共享 `RunProgressReporter`、唯一 verbosity 常量、RuntimeOptions 注入和 content-free MCP/lifecycle/case 事件已实现；
- [x] 具名非 JSON compact formatter、失败诊断上限、ID/XML/query redaction、verbose read batching 和 `all` 子进程透传/截断已实现；
- [x] `--json` 优先合同、dry-run 零副作用、三档过滤/顺序/flush/统计及敏感字段边界已有纯测试；
- [x] Manual-validation 纯测试为 `597 passed`；标准完整基线为 `930 passed, 1 warning`，warning 是仓库 `.pytest_cache` 的既有 `WinError 5`；`copy-page --dry-run --verbosity normal`、`rename --dry-run --verbosity verbose` 和 `rename --dry-run --json --verbosity verbose` 均通过；
- [x] 用户本人已运行真实 `copy-page --verbosity normal` 与 `rename --verbosity verbose`，确认长/短场景的实时精细度、紧凑结尾和 lifecycle 展示均无问题。

上述自动化与 dry-run 均未启动真实 OneNote scenario；它们不能替代最后一项用户前台展示确认。

## 用户前台确认

2026-08-13，用户明确确认两档真实展示“跑完了，没问题”。对应的本地 content-free artifact 复核如下：

- `run-2026-08-13-16-56-40`：`copy-page`，`status=passed`、`restored=true`、`worksite_preserved=false`、`lifecycle=closed_preserved`，单 MCP 进程，`report.md` 已生成；
- `run-2026-08-13-17-03-02`：`rename`，`status=passed`、`restored=true`、`worksite_preserved=false`、`lifecycle=closed_preserved`，单 MCP 进程，`report.md` 已生成。

真实场景由用户本人在前台显式启动；Agent 只读取上述已有 artifact 的 scenario/status/lifecycle/计数与报告存在性，未读取或记录 OneNote ID、正文、XML、binary、query 或完整响应，也未再次启动任何真实场景。至此实现、文档、纯合同、完整 pytest、dry-run 和用户真实展示确认全部闭合，本 TODO 标记为“已完成”。

## 完成定义

实现、文档和自动化合同全部闭合，并记录用户对两条真实前台展示命令的确认后，方可标记为“已完成”。
