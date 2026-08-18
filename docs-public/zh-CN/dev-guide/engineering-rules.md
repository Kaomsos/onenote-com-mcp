# 工程规则

[English](../../en/dev-guide/engineering-rules.md) | [文档首页](../../README.zh-CN.md)

仓库由分层的 `AGENTS.md` 文件治理（仓库根、`src/`、`tests/`、`tests/manual_validation/`、`docs/` 及其子目录），对人类贡献者和 AI 智能体同等约束。本页是参与贡献前必须了解的规则的公开摘要；`AGENTS.md` 文件本身是权威来源，更具体的文件只能收紧——绝不能放宽——安全门限。

## 不可协商的安全门限

1. **Local-only 边界。** 未经明确的项目级决策，不得引入云 API、遥测、远程内容处理或直接编辑 `.one` 文件。
2. **Fail-closed 权限。** 写入、删除、永久删除、实验性 mutation、重建式 Move 和 raw XML 由相互独立、默认关闭的门控制。风险不同的新 mutation 能力必须获得自己的独立门。
3. **精确 ID、类型化 mutation。** 不按名称选择 mutation 目标，不做无界层级扫描，不搞临时拼凑的 raw XML 路径。
4. **真实 mutation 验证由人把关。** Agent、pytest、CI、hook、package/install 脚本、import、timer、watcher 和后台任务绝不能运行真实的 `run.py <scenario>`——只有用户能启动真实运行。见[手动验证框架](manual-validation.md)。
5. **不用破坏性操作图方便。** 用户数据和工作树中的无关改动受保护；绝不用破坏性 Git 或文件系统操作简化实现。

## 契约纪律

公开工具名、参数、响应结构、policy 要求和环境变量都是契约。契约变化时，同一变更必须同时更新：

- 实现；
- 自动化测试；
- `docs/design/` 下的当前设计文档；
- 面向用户的 README/文档内容。

重命名过的工具没有兼容别名，也没有隐藏的环境开关。

## 生产代码规则（`src/`）

- 绝不绕过 `MutationPolicy` 检查。
- Mutation 使用精确对象 ID 加当前 confirmation 字段；不静默回退到名称匹配。
- 搜索和 Copy 工作受配置预算约束；预算耗尽是显式失败。
- 绝不记录 OneNote 内容、bridge payload、secret 或原始工具参数——审计保持 content-free。
- 新增或修改非只读工具时，还必须在 `tests/manual_validation/` 下提供隔离的具名 scenario，真实执行留给用户。

## 文档规则（`docs/`）

- 当前行为在 `docs/design/`；流程在 `docs/dev/`；带证据边界的经验在 `docs/lesson/`；未完成工作在 `docs/todo/`（不可变 ID、索引同步）。
- 绝不能仅凭 mock、dry-run 输出或智能体推断就声称真实 OneNote scenario 已通过——只有用户确认的证据才算真实后端结果。
- 文档移动/改名必须在同一变更中更新仓库内全部相对链接。

## 验证基线

迭代时运行最小的相关纯测试；交付跨领域变更前运行完整测试集：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

真实 OneNote 验收命令始终交给用户执行，不属于自动化验证范围。
