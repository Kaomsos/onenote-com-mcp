# Pytest Windows sandbox 临时产物隔离

本文记录默认自动测试脚手架如何在 Windows 人工终端、Codex elevated sandbox 和并发 pytest session 之间隔离临时目录与 cache。它只适用于纯 pytest 自动测试，不授权或触发任何真实 OneNote scenario；真实 mutation 验证继续受 [`tests/manual_validation/AGENTS.md`](../../tests/manual_validation/AGENTS.md) 约束。

## 问题与证据

2026-08-15 在 Windows Codex desktop elevated sandbox 中观察到：进程实际账户是专用 sandbox 账户，但继承的 `USERNAME` 仍是交互式用户。Python 的 `getpass.getuser()` 因此返回继承用户名，而 `os.getlogin()` 返回实际执行账户。

pytest 默认以继承用户名构造 `%TEMP%/pytest-of-<user>`，并为该根设置严格权限。人工终端与 sandbox 于是可能选择同名目录，但目录 ACL 属于不同 Windows 身份；后续 session 在扫描编号目录时会以 `PermissionError: [WinError 5]` 失败。该失败发生在 `tmp_path` fixture setup，能够把大量无关测试同时表现为 error。

证据边界：上述结论来自本机默认 pytest 全量运行、Windows 身份与环境变量的只读检查，以及改用独立临时根后的自动测试结果。它不涉及真实 OneNote COM 行为，也不说明所有 Windows 或 pytest 版本必然具有相同行为。

## 当前脚手架

仓库根 [`conftest.py`](../../conftest.py) 在 pytest 配置 tmp-path 与 cache 插件之前执行以下初始化：

1. 优先使用 `os.getlogin()` 获取实际执行身份；无登录 session 的 CI 或 detached terminal 才回退到 `getpass.getuser()`。
2. 将规范化后的身份生成 8 位 SHA-256 前缀键。该本机分区键用于区分少量人工、offline sandbox 和 online sandbox 账户，不作为安全标识；短键保持账户分区稳定、不在诊断路径中暴露账户名，并为深层 fixture 的 240 UTF-16-unit 路径预算留出空间。
3. 默认设置 `PYTEST_DEBUG_TEMPROOT` 为 `%TEMP%/om/<identity-key>`，并预先创建该精确身份目录；`om` 是此 Windows-only 项目的短命名空间。pytest 对自定义临时根只创建直属的 `pytest-of-<user>`，不会递归创建其父目录。
4. 默认设置 `PYTEST_CACHE_DIR` 为同一隔离根下的 `cache`。
5. 在根 conftest 仍执行时设置 `sys.dont_write_bytecode = True`，阻止后续测试 import 把 `__pycache__` 分散到仓库；它不改变非 pytest Python 命令的 bytecode 行为。
6. Python 可能在执行该开关前已经为根 conftest 选定 `.pyc` 位置，因此 session 结束时执行一次精确 best-effort 清理：只删除根 `__pycache__` 下的 `conftest.*.pyc`，且只对已空目录执行 `rmdir`；发现任何其他文件时保留目录，不递归扩大范围。
7. 使用 `setdefault` 保留调用者显式设置的两个环境变量，不覆盖有意的外部测试环境；显式临时根同样必须能够由当前身份创建或访问，否则测试在配置阶段 fail closed。

[`pyproject.toml`](../../pyproject.toml) 将 pytest cache 指向 `$PYTEST_CACHE_DIR`，并设置 `tmp_path_retention_policy = "failed"`、`tmp_path_retention_count = "1"`。成功 session 的 `tmp_path` 数据会清理；失败时最多保留最近一个编号 session 供诊断。Cache、bytecode 与可能保留的失败现场均不会写入 Git 工作树。

默认验证命令保持不变：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

合同测试 [`tests/test_pytest_scaffold.py`](../../tests/test_pytest_scaffold.py) 验证 artifact root 位于工作树之外、cache 使用同一隔离根，并锁定失败保留策略。

## 设计取舍

- 不在 `addopts` 中写死一个 `--basetemp`。pytest 启动时会重建显式 basetemp；多个 agent 或终端并发使用同一路径会互相删除或覆盖现场。
- 不把临时根或 cache 放入仓库后再依赖 `.gitignore`。忽略规则只能隐藏污染，不能避免目录增长、路径预算变化或并发干扰。系统 Temp 下的短命名空间和 8 位身份键保持足够短，避免测试脚手架本身消耗 manual-validation 的 240-unit 路径预算；pytest 进程同时关闭工作树 bytecode 写入。
- 不因测试目录冲突切换 Full Access 或降级 Windows sandbox。测试脚手架应适配实际执行身份，不能靠放宽整个 agent 的文件系统权限解决。
- 保留最近一次失败现场，而不是无条件全部删除。这样成功运行保持干净，失败仍有最小诊断证据；如将来改为零保留，应同时更新配置、合同测试和本文。

## 旧目录与排障

脚手架不会读取或删除历史 `%TEMP%/pytest-of-<interactive-user>`。该目录不再被默认仓库测试使用，可由其所有者在确认没有 pytest 进程运行后按普通系统临时文件策略处理。

如果默认测试仍在 fixture setup 阶段出现路径权限错误，依次检查：

1. `PYTEST_DEBUG_TEMPROOT` 是否被调用者显式覆盖到工作树或另一个身份拥有的目录；
2. `os.getlogin()` 与继承的 `USERNAME` 是否仍能区分实际 sandbox 身份；
3. `pytest --trace-config` 中根 `conftest.py` 是否已加载；
4. 当前失败是否来自测试自身的路径合同，而非 pytest 的共享临时根。
