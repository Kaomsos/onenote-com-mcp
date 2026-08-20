# 自动化测试指令

这些规则适用于 `tests/`；其中 [`manual_validation/AGENTS.md`](manual_validation/AGENTS.md) 会对真实后端验证区域施加更严格的规则。后者仍是更严格的权威：lifecycle、clear、真实 `run.py` 禁令和权限闭包以它为准，本文件只提供分层导航，不复制那些契约。

## 合同分层

- 根 `tests/`：生产 policy、[`USER_TOOL_NAMES`](../src/local_onenote_mcp/tool_surface.py)、MCP manager 与 service 合同。
- `tests/manual_validation/tests/`：CLI、dry-run catalog、lifecycle、recipe pin 与证据格式。细节与禁令见 nested [`AGENTS.md`](manual_validation/AGENTS.md)。
- 三套 `destination_position`（生产 projector、根期望 builder、验证侧 projector）必须独立，禁止互相 import。
- dry-run「零 MCP」以 [`test_registered_dry_run_cases.py`](manual_validation/tests/test_registered_dry_run_cases.py) 为 canonical；CLI/progress 文件测输出形状。
- keep-worksite 按 CLI / plan / orchestrator / scenario 四层保留，不收成单测。

## 自动化测试边界

- 默认 pytest 必须具备确定性、只在本地运行，并且在未安装或未打开 OneNote 时也能安全执行。测试不得修改真实 Notebook、启动真实 manual-validation scenario 或依赖用户文档。
- Bridge 和 mutation 行为应使用 fake、monkeypatch、已记录的最小 fixture 及合同级断言。绝不能让测试是否通过依赖当前 OneNote 状态。
- `write_contract` marker 标识经过 mock 或隔离的 mutation 合同；它不授权真实 mutation，也不能替代由用户执行的人工验证。
- 不得添加能够在缺少 `--dry-run` 时启动 `tests/manual_validation/run.py` 的 pytest collection hook、import、fixture、timer 或后台进程。

## 测试设计

- 覆盖公开行为和安全 invariant：policy 拒绝、精确 ID 定位、confirmation field、有界工作、content-free 日志、部分失败、restore/cleanup 以及稳定的响应结构。
- 优先使用能直接呈现相关层级或 Page invariant 的聚焦 fixture。避免使用会掩盖具体契约变化的宽泛 snapshot。
- 显式测试 fail-closed 行为。不得为了满足 mock 而削弱生产权限、验证安全门限或错误处理。
- 隔离并 mock 平台特有假设，使自动化测试集可以在普通开发环境中运行。

## 验证

- 迭代时运行最小的受影响测试文件。
- 对共享行为的变更，或在完成跨领域变更并准备交付前，运行 `.venv\Scripts\python.exe -m pytest -q`。
- 修改 `tests/manual_validation/` 下的内容时，遵循其嵌套 `AGENTS.md` 中的允许命令清单。
