# 028：Reorder Section `all` 资格与 Progress 埋点

> ID：028
> 状态：已完成
> 优先级：P2
> 类型：人工验证 / 批处理资格与可观测性
> 更新日期：2026-08-14

## 决策与范围

只将真实后端已确认支持的 `reorder-section` 纳入 human-gated `all`。`reorder-section-group` 的真实负能力证据和产品拒绝结论保持不变：它继续作为 `limited/failed` 的独立诊断入口，显式 `included_in_all=False`，不得进入正向批处理验收。

`reorder-section` 继续覆盖同一 disposable Notebook 内的两种合法父级：

1. Notebook 直属 Section：`01,02,03 → 01,03,02 → 01,02,03`；
2. SectionGroup 直属 Section：`01,02,03 → 01,03,02 → 01,02,03`。

本轮不改变 tool schema、policy、fixture、mutation 次数、confirmation、写后不变量、默认恢复或失败保留合同。

## Progress 合同

场景的长步骤必须在 `normal`/`verbose` 输出中提供可区分、content-free 的 unit progress：

- 正向 case 依次为 `notebook-parent`、`section-group-parent`；
- 默认恢复按反向顺序依次为 `section-group-parent`、`notebook-parent`；
- 每一步分别发出 started 与 completed/PASS，并带 `index/total` 与 elapsed；
- 标签只来自代码内冻结的父级类型，不包含 Notebook/Section/Page 名称、ID、正文、XML、tool arguments 或 response payload；
- `quiet` 仍只显示主要 phase/failure，不展开 unit progress；`--json` 仍保持单一 JSON 文档。

## 实施清单

- [x] 将 `reorder-section.included_in_all` 设置为 `True`；
- [x] 保持 `reorder-section-group.included_in_all=False` 和 `limited/failed` assessment；
- [x] 为两个正向 case 使用静态父级标签，并为两个反向恢复步骤增加逐项 progress；
- [x] 添加纯合同，验证 progress 顺序、started/PASS、计数、elapsed 和不泄露 fixture ID/名称；
- [x] 更新 `all` 数量、排除集合、manual-validation README、开发指南与历史 TODO 链接；
- [x] 由用户本人运行当前版本真实 `all`，确认 `reorder-section` 在批处理中通过且默认恢复。

## 真实批处理证据（2026-08-14）

用户启动的 `all --use-cache` 在第 4 个 child 产生 `run-2026-08-14-00-08-58`。该 run 为 `validated_hit`、`status=passed`，两个正向 case 与两个逆序 restore progress 均完成，`scenario_result.restored=true`，working Notebook 最终精确关闭且 template 未打开。`reorder-section-group` 仍未进入 `all`。批处理后续无关 cache consumer 的失败不影响这个独立 child 的完成证据。

## Agent 纯验证记录

2026-08-13 的聚焦 pytest 为 `129 passed`，完整 manual-validation 纯合同为 `612 passed`，仓库完整 pytest 为 `977 passed`。`run.py all --dry-run --json --verbosity quiet` 为 `15 passed, 0 failed`：`reorder-section` 是第 4 个 child，三个 Reparent 随后为第 5–7 个；输出中没有 `reorder-section-group`。这些结果只证明 registry、dry-run 和纯合同，Agent 未启动真实 OneNote mutation。

## HUMAN-GATED 验收

Agent 只能运行纯测试与明确的 dry-run，不得执行以下真实命令。真实验收由用户本人在交互式前台终端启动：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py all
```

验收时应确认：

- `all` 包含 `reorder-section`，不包含 `reorder-section-group`；
- normal 输出能看到两条正向 case 和两条反向 restore progress；
- 两种父级都完成精确顺序 read-back，最终 `restored=true`；
- 失败时保持非零退出、开放现场与 evidence，不能为了继续批处理而跳过验证或恢复。

## 完成证据

代码、纯合同、dry-run、文档和用户真实批处理证据均已齐全；`reorder-section` 的 progress、read-back、restore、cache template 隔离和最终关闭均由上述 run 证明，本 TODO 标记为“已完成”。
