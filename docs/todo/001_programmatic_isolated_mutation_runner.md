# 001：本地程序化 OneNote 隔离验证 Runner

> ID：001
> 状态：进行中
> 优先级：P1
> 类型：开发基础设施
> 更新日期：2026-08-04
> 触发边界：只能由用户在终端显式运行，不进入 CI、hook、自动化或默认测试

## 1. 目标

实现一组本地脚本，直接通过 MCP stdio client 调用 `local-onenote` tools，把 [isolated_mutation_validation.md](../dev/isolated_mutation_validation.md) 中重复的参数传递、回读、摘要比较、恢复和报告自动化。

Runner 不使用 Codex、LLM 或远程服务；Notebook 名称、ID、内容和结果只在本机流转。它仍然执行真实 OneNote COM mutation，因此只能由用户在终端手动启动。用户选择并运行具体 mutation 子命令本身即构成授权，脚本不再要求额外的权限开关或二次确认。

## 2. 建议文件边界

```text
tests/manual_isolated/
├─ run.py                       唯一用户入口
├─ runner.py                    参数解析、场景状态机和报告
├─ mcp_stdio_client.py          MCP server 生命周期和 call_tool adapter
├─ test_runner.py               不接触 OneNote 的纯 mock 测试
└─ README.md                    手动运行说明
```

不要为每个 mutation 创建独立脚本。场景、报告、权限和恢复逻辑集中在一个入口，避免开发工具继续散落。

## 3. 建议命令设计

```powershell
# 完全只读：发现目标和检查环境
uv run python tests\manual_isolated\run.py inspect `
  --notebook-name "_LOCAL_MCP_ISOLATED_TEST_"

# 准备测试结构；该子命令会自动为 MCP 子进程开启写权限
uv run python tests\manual_isolated\run.py create `
  --notebook-name "_LOCAL_MCP_ISOLATED_TEST_" `
  --run-dir .local-validation\run-001

# 保存 tree、Page XML hash、对象和 onepkg
uv run python tests\manual_isolated\run.py baseline `
  --notebook-id "<id>" `
  --output .local-validation\run-001

# 每个场景一次人工命令，自动完成正向、回读和恢复
uv run python tests\manual_isolated\run.py validate rename `
  --run-dir .local-validation\run-001

uv run python tests\manual_isolated\run.py validate reorder `
  --run-dir .local-validation\run-001

uv run python tests\manual_isolated\run.py validate move `
  --run-dir .local-validation\run-001

# Delete 必须是独立命令和独立进程；只允许非永久删除
uv run python tests\manual_isolated\run.py validate delete `
  --run-dir .local-validation\run-001 `
  --delete-target-id "<manifest 中的 disposable ID>"
```

命令名称应面向用户表达验证意图，不直接暴露 `UpdateHierarchy`、schema enum 等 COM 细节。

## 4. 通用参数

| 参数 | 默认值 | 语义 |
| --- | --- | --- |
| `--notebook-name` | create 有隔离默认值 | inspect/create/read 使用；必须精确匹配唯一 Notebook。validate 可用它交叉检查 manifest。 |
| `--notebook-id` | 无 | baseline/read 可直接指定；mutation 主键从 run manifest 读取，不再按名称解析。 |
| `--output/--run-dir` | `.local-validation/<timestamp>` | JSON、JSONL、XML hash、onepkg 和日志目录。 |
| `--delete-target-id` | 无 | Delete 场景必填；只接受 manifest 中记录且当前仍位于 Delete-Sandbox 下的 disposable ID。 |
| `--timeout` | `180` | 单个 MCP tool 超时秒数。 |
| `--dry-run` | `false` | 只输出计划、目标和待调用工具；不启动 MCP mutation。 |
| `--json` | `false` | stdout 只输出稳定 JSON，方便归档。 |

不设计 `--enable-writes`、`--enable-deletes`、`--enable-experimental-move`、`--yes` 或交互确认参数。命令行中的具体子命令、Notebook/运行目录和目标 ID 已完整表达用户要执行的操作；对象的 `expected_name`、`expected_parent_id` 等并发保护值由 Runner 从最新只读快照生成。

## 5. 权限配置

Runner 根据用户选择的子命令自动为 MCP 子进程构造最小权限 env，父进程和永久配置不变：

| 场景 | WRITES | DELETES | PERMANENT | EXPERIMENTAL_MOVE | RAW_XML |
| --- | ---: | ---: | ---: | ---: | ---: |
| `inspect/baseline/report` | false | false | false | false | false |
| `create/rename/reorder` | true | false | false | false | false |
| `move` | true | false | false | true | false |
| `delete` | false | true | false | false | false |

约束：

- 选择并运行具体 mutation 子命令即授权 Runner 设置该行所需权限，不再进行额外授权检查；
- Runner 必须从静态场景矩阵推导权限，调用方不能通过通用参数扩大权限；
- `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES` 永远固定为 `false`；
- `LOCAL_ONENOTE_ENABLE_RAW_XML` 永远固定为 `false`；
- Delete 场景不接受 `permanently=true`；
- 每次启动后先调用 `health_check`，若实际 policy 与矩阵不完全相等则停止；
- 进程结束后不修改 `.codex/config.toml`、`.mcp.json` 或用户全局配置。

## 6. MCP 调用层

使用项目依赖中的 Python MCP client：

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
```

`mcp_stdio_client.py` 负责：

- 用当前 `.venv` Python 启动 `-m local_onenote_mcp.server`；
- 只向子进程注入场景权限和专用 `TEMP/TMP`；
- 初始化 session、读取 tools/list 并执行 allowlist 检查；
- `call_tool()` 后解析统一 `{ok, complete, ...}` envelope；
- 为每次调用写入开始时间、结束时间、参数摘要和结果摘要；
- timeout 或 server 退出时终止场景，不自动重试 mutation；
- 只读调用允许有限重试，重试策略写入报告。

每个场景维护静态 tool allowlist。Runner 启动后若服务暴露的工具与预期不一致可以警告，但只能调用 allowlist 内工具。

## 7. 场景状态机

每个 mutation 场景固定执行：

```text
preflight
  → resolve exact IDs
  → capture before snapshot
  → validate target identity and expected fields
  → execute one mutation
  → capture fresh after snapshot
  → assert invariants
  → restore original state
  → capture restored snapshot
  → write report
```

任何一步失败：

- 停止后续 mutation；
- 不尝试猜测性补偿；
- 如果正向 mutation 已完成但恢复失败，退出码必须表示“需要人工恢复”；
- 报告记录最后成功步骤、当前对象 ID 和建议的只读检查命令；
- 不自动进入下一个场景。

## 8. 自动化验收项

### Rename

- SectionGroup/Section ID、父级和所有后代 ID 保持；
- Page order/level 保持；
- Page 内容摘要保持；
- 恢复原名称后再次验证。

### Reorder

- Page ID 和正文摘要保持；
- 目标 order/page_level 与 read-back 一致；
- Page 树符合 level 规则；
- 恢复原顺序和 level 后再次验证。

### Move

- 仅允许同 Notebook；
- Section ID 保持；
- Page ID、顺序、level 和内容摘要保持；
- 正向与恢复两次 Move 都通过才算成功；
- 报告始终保留“特定 OneNote 版本实测，不代表普遍保证”。

### Delete

- 只允许预先记录的 Delete-Sandbox 子对象；
- 要求显式对象 ID、expected_name 和 expected_parent_id；
- `permanently` 固定 false；
- 验证对象从默认列表消失，并在回收站 snapshot 中缺失或标记回收站状态；
- Runner 不自动清空回收站。

## 9. 报告格式

每次 run 至少生成：

```text
.local-validation/<run-id>/
├─ manifest.json             版本、策略、目标和场景
├─ calls.jsonl               MCP 调用审计记录
├─ before.json
├─ after.json
├─ restored.json
├─ page-hashes.json
├─ baseline.onepkg           可选
└─ report.md
```

日志不得记录二进制 base64、完整附件内容或无关 Notebook 数据。默认只保存目标隔离 Notebook 的白名单字段。

建议退出码：

- `0`：场景和恢复均成功；
- `2`：参数或目标身份检查失败，未 mutation；
- `3`：MCP/COM 失败，状态未知；
- `4`：正向 mutation 成功但恢复失败，需要人工处理；
- `5`：不变量验证失败。

## 10. 人工触发保障

实现时必须同时满足：

- 不添加到 CI、pre-commit、post-commit、安装脚本或 package script；
- 不由 import side effect 启动；
- `main()` 之外不能执行 MCP 或 COM；
- 真实 mutation 测试使用独立 pytest marker，默认 deselect；
- 用户手动运行具体 mutation 子命令即视为授权，不再弹出交互确认，也不要求额外权限参数；
- 仓库不配置 CI、hook 或其他程序化入口来调用 mutation 子命令；脚本本身不依赖 TTY，以便用户从普通终端或自己的受控脚本中运行；
- 不提供定时、后台或 watch 模式；
- README 只提供按场景运行的明确命令和文档链接，不提供隐式批量执行全部 mutation 的入口。

## 11. 实现任务清单

- [x] 实现 stdio MCP client adapter 和统一 envelope 解析；
- [x] 实现 tool allowlist 与场景权限矩阵；
- [x] 实现 `inspect` 和 `baseline` 只读命令；
- [x] 实现 `create`，支持幂等复用和重复检测；
- [x] 实现 Rename 正向/恢复场景；
- [x] 实现 Reorder 正向/恢复场景；
- [x] 实现 Move 正向/恢复场景；
- [x] 实现独立非永久 Delete 场景；
- [x] 实现 JSONL 审计、摘要和 Markdown report；
- [x] 为参数、权限和快照比较编写纯 mock 测试；
- [x] 在文档中明确真实 COM 验证只能由用户手动运行；
- [ ] 完成一次专用 Notebook 实测后记录 OneNote/Office/CLI 版本。

## 12. 完成定义

TODO 完成需同时满足：

1. 用户用一条明确命令启动一个场景，无需逐个手填 MCP tool 参数；
2. 只读子命令不开放权限；mutation 子命令自动开放该场景所需的最小权限，无需额外授权参数；
3. 每个 mutation 均有 before/after/restored 证据；
4. 任何失败都不会静默继续下一个 mutation；
5. Delete 永不永久执行；
6. 默认测试和 CI 无法触发真实 COM mutation；
7. `isolated_mutation_validation.md` 更新为以 runner 为推荐入口、手工 tool 调用为故障排查后备方式。
