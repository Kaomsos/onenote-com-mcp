# 自动化测试

[English](../../en/dev-guide/testing.md) | [文档首页](../../README.zh-CN.md)

## 测试分层

项目把验证分为三层，信任级别严格不同：

| 层 | 位置 | 运行位置 | 接触真实 OneNote？ |
| --- | --- | --- | --- |
| 纯自动化测试 | `tests/` | 任何环境（CI 安全） | 绝不 |
| 手动验证合同测试 | `tests/manual_validation/tests/` | 任何环境（CI 安全） | 绝不 |
| 真实后端 scenario | `tests/manual_validation/run.py` | 用户本机，且只能由用户启动 | 是——隔离的 disposable Notebook |

本页覆盖前两层，第三层见[手动验证框架](manual-validation.md)。

## 运行自动化测试集

```powershell
.venv\Scripts\python.exe -m pytest -q
```

或使用 uv：

```powershell
uv run pytest
```

默认测试集具备确定性、只在本地运行，未安装或未打开 OneNote 时也能安全执行。测试绝不修改真实 Notebook，绝不启动真实 manual-validation scenario，也不依赖用户文档。

## 测试设计规则

- **在 bridge 层 mock。** Bridge 和 mutation 行为使用 fake、monkeypatch、已记录的最小 fixture 和合同级断言测试。测试是否通过绝不依赖当前 OneNote 状态。
- **覆盖安全 invariant，不只是功能**：policy 拒绝、精确 ID 定位、confirmation 字段、有界工作、content-free 日志、部分失败、restore/cleanup 和稳定响应结构。
- **显式测试 fail-closed 行为。** 绝不为了满足 mock 而削弱生产权限、验证门限或错误处理。
- **`write_contract` marker** 标识经 mock 或隔离的 mutation 合同测试。它不授权真实 mutation，也不能替代用户执行的人工验证。
- 平台特有假设被隔离和 mock，使测试集可在普通开发环境中运行。

## 自动化测试能证明什么、不能证明什么

Mock、pytest 和 `--dry-run` 输出证明的是**代码合同与编排**，不能证明真实 OneNote COM 行为。真实后端证据只来自用户显式运行的具名 manual-validation scenario；文档绝不能仅凭自动化结果把真实 scenario 报告为已通过。

## Smoke 测试

在仓库 checkout 中可运行只读传输 smoke 测试：

```powershell
uv run python scripts\smoke_mcp.py --tools-only   # 校验 53 工具列表，不连接 OneNote
uv run python scripts\smoke_mcp.py                # 只读探测；需要可见的 OneNote Desktop
```
