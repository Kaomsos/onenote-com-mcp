# 贡献指南

[English](../../en/dev-guide/contributing.md) | [文档首页](../../README.zh-CN.md)

感谢你考虑参与贡献。本项目欢迎 Issue 和 Pull Request，但有几条为保护用户笔记本而存在的硬边界。

## 基本规则

1. **绝不从自动化中运行真实 OneNote mutation scenario。** Agent、pytest、CI、hook、timer、watcher 和后台任务禁止执行真实的 `tests/manual_validation/run.py <scenario>`。只有人类用户能启动真实运行。`--dry-run` 变体始终安全。
2. **保持 local-only 边界。** 贡献不得引入云 API、遥测、远程内容处理或直接编辑 `.one` 文件。
3. **保持默认 fail-closed。** 风险不同于既有门的新 mutation 能力需要自己独立的、默认关闭的授权门。
4. **绝不包含用户数据。** Issue、PR、测试、fixture 和文档中不得出现笔记内容、真实对象 ID、个人路径或机器标识。
5. **遵循分层 `AGENTS.md`。** 它们对人类和 AI 智能体同等约束；离你编辑的文件最近的那份适用，更具体的文件只会收紧规则。见[工程规则](engineering-rules.md)。

## 开发环境

```powershell
git clone https://github.com/Peteroooooooo/local-onenote-mcp
cd local-onenote-mcp
uv sync --all-groups
uv run pytest
```

自动化测试集具备确定性，无需安装 OneNote 即可运行。见[自动化测试](testing.md)。

## 提交变更的流程

1. 编辑某个作用域的文件前，先阅读最近的 `AGENTS.md`。
2. 迭代时运行最小的相关测试文件；提交前运行完整纯测试集（`uv run pytest`）。
3. 如果变更触及公开工具契约（名称、参数、响应形状、policy、环境变量），必须**在同一变更中**更新实现、测试、`docs/design/` 和面向用户的 README/文档。
4. 如果变更新增或修改非只读工具，还需添加具名 manual-validation scenario（静态 policy/allowlist、隔离 fixture、before/after 证据、失败 handoff）并记录精确的用户命令。真实执行交给维护者/用户——见[手动验证框架](manual-validation.md)。
5. 修改公开文档时保持双语树（`docs-public/en/` 与 `docs-public/zh-CN/`）同步。

## Pull Request 要求

- 说明变更内容、影响范围和兼容性影响。
- 说明新增/更新了哪些测试，以及完整纯测试集的结果。
- 显式指出任何权限门或契约变化。
- 脱敏所有敏感信息；PR 内容是公开的。
- 真实后端结论需要用户确认的证据；否则标注为待验证。

## 报告问题

- **Bug**：提供复现步骤、Windows/OneNote Desktop 版本、你的门配置，以及可用的结构化错误 envelope。绝不粘贴笔记内容。
- **功能提议**：描述使用场景；涉及 mutation 时，说明该能力如何保持精确 ID、有界和 fail-closed。
- **安全问题**：在专门的安全政策发布前，请通过 GitHub Issue 报告疑似漏洞，不要附带笔记内容或个人数据。
