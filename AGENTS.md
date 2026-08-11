# 仓库指令

## 项目概况

Local OneNote MCP 是面向 Microsoft OneNote Desktop、仅支持 Windows 且 local-first 的 MCP 服务器。它通过 PowerShell bridge 使用原生 OneNote COM API；不使用 Microsoft Graph、Azure、在线 OAuth，也不直接操作二进制 `.one` 文件。

仓库划分为以下重要作用域。修改其中的文件前，先阅读距离目标文件最近的 `AGENTS.md`：

- [`src/`](src/AGENTS.md)：生产代码架构、policy 安全门限、bridge 安全和公开 tool 契约；
- [`tests/`](tests/AGENTS.md)：不得修改真实 OneNote 环境的确定性自动化测试；
- [`tests/manual_validation/`](tests/manual_validation/AGENTS.md)：具有更严格隔离和权限规则、由人工把关的真实后端验证；
- [`docs/`](docs/AGENTS.md)：文档归属、权威来源和链接维护；
- [`docs/lesson/`](docs/lesson/AGENTS.md)：带证据边界的工程经验、平台限制和 canonical 文档链接；
- [`docs/todo/`](docs/todo/AGENTS.md)：不可变 TODO ID、状态证据和索引同步。

更具体的 `AGENTS.md` 会增加目录局部规则。它们可以细化工作流，但绝不能放宽本文件中的安全门限。

## 全仓库工程规则

- 保持 local-only 边界。未经明确的项目级决策，不得引入云 API、遥测、远程内容处理或直接编辑 `.one` 文件。
- OneNote 操作应保持基于 ID 且类型化。避免按名称选择 mutation 目标、无界层级扫描或临时拼凑的 raw XML 路径。
- 权限默认值保持 fail-closed。写入、删除、永久删除、实验性 mutation、重建式 Move 和 raw XML 必须继续由相互独立的安全门限控制。
- 将公开 tool 名称、参数、响应结构、policy 要求和环境变量视为契约。契约变化时，应同时更新实现、自动化测试、当前设计文档和面向用户的 README 内容。
- 保护用户数据和工作树中的无关改动。不得为了简化实现而使用破坏性的 Git 或文件系统操作。

## 不可协商的 mutation 安全门限

- 每种需要 mutation-policy 权限的真实执行，都必须同时具备自动化合同覆盖，以及 [`tests/manual_validation/`](tests/manual_validation/) 下的具名 scenario。Mock 测试不能替代真实隔离验证。
- 智能体可以修改验证代码、运行纯测试或 mock 测试、检查已保存的证据，以及运行明确带有 `--dry-run` 的命令。无论前台还是后台，智能体都绝不能执行真实的 `run.py <scenario>` 或 `run.py all`。
- 真实 mutation 验证绝不能由 pytest、CI、hook、package/install 脚本、import、timer、watcher 或其他自动化触发。只有用户可以显式启动。
- 真实 scenario 必须使用新建的 disposable 数据、静态最小权限、精确 ID、有界操作以及 before/after 证据。失败或状态不确定的操作必须 fail closed 并保留证据。
- 不得仅为了让测试通过而启用永久删除或 raw XML。人工验证不得删除 working Notebook、普通 artifact、失败现场或用户 Notebook 文件/目录。
- 唯一的文件级例外是 fixture cache runtime：它可以对已由 lifecycle 精确关闭、由本次 disposable recipe bundle 创建的 Notebook 目录执行不解析内容的 opaque byte-for-byte copy；也可以清理由自身 marker 管理的 `.local-validation/fixture-cache/` 根下单个精确 `(fingerprint, template_instance_id)` template/staging entry。清理前必须同时证明 resolved root containment、entry ownership、非 cache/workspace/run root、无 reparse point、Notebook 未打开且不存在 active working lease，并把逐项结果写入 root-level tombstone。该例外绝不覆盖 working copy、普通 validation artifact、失败现场或用户 Notebook；任一证明不完整时必须 fail closed。

## 基线验证

先运行最小的相关纯测试；如果变更可能影响共享行为，再运行完整自动化测试集：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

真实 OneNote 验收命令始终交给用户执行，不属于智能体验证范围。
