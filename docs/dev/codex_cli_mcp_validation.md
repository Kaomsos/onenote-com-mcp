# Codex CLI 间接 mutation 验证（已停用）

> 状态：已停用，不得用于真实 OneNote mutation
> 更新日期：2026-08-06

本项目曾验证过由 `codex exec` 间接编排本地 OneNote MCP 的方式。该方式会让 Agent 获得选择和调用真实 mutation 工具的能力，已经不符合当前“真实 mutation 只能由用户本人启动本地 runner”的权限边界，因此不再保留可执行命令模板。

当前唯一推荐入口是 [`tests/manual_validation/run.py`](../../tests/manual_validation/README.md)：

```powershell
# 每个具名 scenario 自身就是完整隔离 suite；Agent 只把命令交给用户。
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run

# 下面这条真实 mutation 命令只能由用户本人在终端运行。
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

每个扁平的 `run.py <scenario>` 都会通过窄 lifecycle wrapper 创建全新的本地 disposable Notebook，并在该 scenario 唯一的 MCP 子进程内准备最小 fixture、运行所选 mutation 和完成证据/恢复闭环。失败即停止并保留现场；默认在该 scenario 成功并生成报告后，由 wrapper 按 lifecycle lease 的精确 ID/name/path 关闭源 Notebook，但不删除任何本地 Notebook 文件夹。`--keep-notebook` 会跳过最终关闭。不存在 `validate` 分组、公开诊断 action 或聚合 `suite`；特殊 `run.py all` 仅由用户显式启动并串行调用测试注册表中的独立命令，不共享状态或权限，未注册的探索性验证 scenario 不会被自动调用。

禁止以下方式访问真实 OneNote mutation：

- Codex/Codex CLI prompt 编排；
- 前台或后台 Agent 调用 runner、MCP、COM 或替代脚本；
- pytest、CI、hook、安装脚本、timer、watcher；
- 自动批准一组 mutation tools 的非交互 MCP 会话。

Agent 仍可修改 runner、运行 `tests/manual_validation/tests` 下不访问 OneNote 的合同测试，并把最终命令与风险说明交给用户。历史 CLI 参数与 approval 行为只保留在版本控制历史中，不再构成当前验证流程。
