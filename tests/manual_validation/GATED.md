# HUMAN-GATED REAL MUTATION AREA

本目录中的真实 scenario 具备以下不可放宽的边界：

- **GATED**：只能由用户本人显式运行 `run.py <scenario>` 或特殊串行入口 `run.py all`；Agent、CI、hook、timer、watcher 和后台任务禁止执行。
- **ISOLATED**：每条命令必须创建全新的 disposable Notebook 和独立证据目录，禁止复用现有 Notebook 或非空目录。
- **LEAST-PRIVILEGE**：每个 scenario 最多启动一个 MCP 子进程；其 fixture、mutation、证据回读与 restore/cleanup 使用该 scenario 固定的完整闭包 policy、tool allowlist 与一次 `health_check`，不得跨 scenario 合并权限或运行时扩权。源 Notebook create/get/close 只允许窄 lifecycle wrapper 按 lease 的精确 ID/name/path 操作。
- **NO FILE DELETION**：只允许场景约束内的非永久 OneNote Delete；本地 Notebook 文件与目录始终保留。

`all` 只依次启动显式注册的稳定测试 scenario；未来新增的探索性/验证性 scenario 默认不进入批量运行。各命令不共享 `run-dir`、Notebook、MCP 进程、权限或生命周期。

允许 Agent/自动化执行的范围仅限不访问 OneNote 的纯合同测试：

```powershell
.venv\Scripts\python.exe -m pytest tests\manual_validation\tests -q
```

完整规则和用户命令见 [README.md](README.md)。
