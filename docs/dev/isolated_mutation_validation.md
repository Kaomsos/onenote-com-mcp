# OneNote mutation 隔离验证流程

> 本文只定义用户本人在终端显式触发的隔离流程。CI、hook、前台/后台 Agent 或默认测试不得执行。
> 真实验证对象必须是专用、无业务数据、可丢弃的本地 Notebook。

推荐由用户按 [Human-gated Manual Validation Runner](../../tests/manual_validation/README.md) 显式运行一个扁平的 `run.py <scenario>`。每个 scenario 本身就是完整隔离 suite：一个用户命令创建全新 Notebook、准备 fixture、运行所选 mutation（fixture-only 的 `create` 除外）、生成报告并按选项关闭或保留 Notebook。`validate`、`inspect`、`read`、`report` 和聚合 `suite` 均不是公开 action；本页的手工 tool 调用只保留为故障排查说明，不构成可执行入口。Agent 不得通过 Codex CLI、shell 或 MCP 代用户执行真实 mutation；历史 Codex CLI 编排记录见 [已停用流程](codex_cli_mcp_validation.md)。实现进度见 [TODO 001](../todo/001_programmatic_isolated_mutation_runner.md)。

P2 Copy 与 Page 重建式 Move 只能使用 Runner 中各自的具名场景；精确命令、权限矩阵、目标清理和 Notebook 残留规则见 [tests/manual_validation/README.md](../../tests/manual_validation/README.md)，进度见 [TODO 002](../todo/002_p2_copy_and_reconstructive_page_move.md)。不得把本页的 raw/manual 片段组合成另一个隐式 Copy 入口。

## 1. 目标

隔离验证 COM `UpdateHierarchy` 在以下 P1 操作中的真实语义：

1. SectionGroup/Section Rename 是否保持对象 ID 和子对象；
2. Page Reorder/Page Level 是否保持 Page ID、正文和子树；
3. Section 在同 Notebook 内 Move 是否保持 Section ID、Page ID、顺序和完整 Page XML；
4. 回收站 Delete 是否满足默认非永久语义。

其中 `move_section` 在完成本流程前必须保持实验状态。永久删除不属于本流程。

## 2. 具名 Scenario 自动准备与人工后备

推荐流程无需在 OneNote UI 中预先创建结构：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

第一条只展示计划；第二条必须由用户本人明确运行。把 `rename` 替换为另一个顶层 scenario 即可验证其他行为；每条命令都独立创建 Notebook，不依赖上一条。默认 Notebook 名称为 `__LOCAL_MCP_TEST_ISOLATED__<TIMESTAMP>`，默认证据目录为 `.local-validation\run-<同一 TIMESTAMP>`。只有在 scenario 失败后排障或专门验证附件、墨迹、媒体等没有稳定 typed 创建工具的内容时，才需要下面的 UI 人工准备。

在 OneNote UI 中手工创建仅用于测试的 Notebook：

```text
__LOCAL_ONENOTE_MCP_ISOLATED__
├─ Group-A
│  └─ Move-Source
│     ├─ Parent        pageLevel=1，正文含唯一 token
│     ├─ Child         pageLevel=2，正文含唯一 token
│     └─ Sibling       pageLevel=1，含图片或附件副本
├─ Group-B
└─ Delete-Sandbox
   ├─ Disposable-Group
   └─ Disposable-Section
```

不得复用真实 Notebook，也不得在测试结构中放置唯一副本。开始前等待 OneNote 完成同步，并保留 UI 截图或导出副本供人工比对。

## 3. 独立进程配置

使用推荐 Runner 时无需修改任何 MCP 配置；每条场景命令最多启动一个独立 server。源 Notebook create/get/close 由 lease 约束的窄 lifecycle wrapper 完成；该场景唯一的 MCP 进程使用固定的 fixture + mutation + evidence + restore/cleanup 最小权限闭包，并在 fixture 前用 `health_check` 核验。仅在使用后文手工 tool 调用排障时，才复制一份只用于该 Notebook 的 MCP 配置并重启独立 server 进程：

```toml
[mcp_servers.local-onenote-isolated.env]
LOCAL_ONENOTE_ENABLE_WRITES = "true"
LOCAL_ONENOTE_ENABLE_DELETES = "false"
LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES = "false"
LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION = "true"
LOCAL_ONENOTE_ENABLE_RAW_XML = "false"
```

先调用 `health_check`，确认只有 `writes_enabled` 和 `experimental_move_section_enabled` 为 `true`。禁止设置 raw XML 或永久删除开关。

## 4. 建立只读基线

1. 用 `resolve_identifier("__LOCAL_ONENOTE_MCP_ISOLATED__", "notebook")` 取得 Notebook ID；后续 mutation 禁止继续用名称或路径。
2. 调用 `get_tree(notebook_id)`，记录所有 ID、父级、Page `order/page_level`。
3. 对 3 个 Page 调用 `get_page_xml(page_id, "all")`，在测试记录中保存 SHA-256。
4. 对含图片/附件的 Page 调用 `get_page_objects`，记录 `id/callback_id/format`，但不要把二进制粘贴到日志。
5. 调用 `list_sections` 和 `list_pages`，确认没有回收站对象混入。

任何 ID、标题、父级或内容与准备结构不一致时立即停止。

## 5. Rename 验证

依次人工调用并在每次后执行 `get_tree`：

```json
{
  "tool": "rename_section_group",
  "arguments": {
    "section_group_id": "<Group-A ID>",
    "new_name": "Group-A-Renamed",
    "expected_name": "Group-A",
    "expected_parent_id": "<Notebook ID>",
    "expected_modified": null
  }
}
```

然后以新快照确认字段改回 `Group-A`。对 `Move-Source → Move-Source-Renamed → Move-Source` 重复同一流程。验收：对象 ID、父级、Page ID/顺序及 Page XML SHA-256 均不变。

## 6. Reorder 与缩进验证

1. 将 `Sibling` 放到 `Parent` 后，并设 `page_level=2`；确认其 `parent_page_id=Parent ID`。
2. 将 `Sibling` 恢复到原位置和 `page_level=1`。
3. 每一步后读取 `list_pages`、`get_tree` 和 3 个 Page 的完整 XML 摘要。

调用模板：

```json
{
  "tool": "reorder_page",
  "arguments": {
    "page_id": "<Sibling ID>",
    "expected_title": "Sibling",
    "expected_section_id": "<Move-Source ID>",
    "after_page_id": "<Parent ID>",
    "page_level": 2,
    "expected_modified": null
  }
}
```

验收：调用返回的 `order/page_level` 与只读回读一致；所有 Page ID 和正文摘要保持不变；UI 中缩进树与 `get_tree` 一致。

## 7. Section Move 验证

调用：

```json
{
  "tool": "move_section",
  "arguments": {
    "section_id": "<Move-Source ID>",
    "destination_parent_id": "<Group-B ID>",
    "expected_name": "Move-Source",
    "expected_parent_id": "<Group-A ID>",
    "expected_modified": null
  }
}
```

工具会自动比较 Section ID、Page ID/顺序和完整 Page XML SHA-256。还必须人工检查 OneNote UI、附件可打开性和同步状态。随后使用最新快照中的确认字段移回 Group-A，并重复检查。

只有正向和恢复两次 Move 都通过，才可在设计记录中把本机版本组合标为已验证；这不等同于对所有 Office/OneNote 版本解除实验状态。

## 8. 非永久 Delete 验证（可选、单独重启）

关闭测试进程，将 `LOCAL_ONENOTE_ENABLE_DELETES` 改为 `true`，永久删除仍为 `false`。仅对 `Delete-Sandbox` 下的 disposable 对象调用 typed delete，且完整提供 `expected_name/expected_parent_id`。

验收：返回 `permanently=false`，对象从默认列表消失；启用 `include_recycle_bin=true` 时对象缺失或标记 `is_in_recycle_bin=true`。不要调用 `permanently=true`。

## 9. 停止与清理

1. 关闭独立 MCP server，移除全部 enable 环境变量；
2. 用普通只读 profile 再次运行 `health_check`，确认写、删、实验 Move、raw XML 全部为 `false`；
3. 分场景后备流程需在 OneNote UI 中人工关闭并清理隔离 Notebook；默认具名 scenario suite 只在当前场景通过并生成报告后用 typed `close_notebook` 回读确认，不删除本地 Notebook 目录。中途失败或使用 `--keep-notebook` 时源 Notebook 保持打开；
4. 若任一步发生 ID 变化、内容摘要变化、重复 Section/Page 或恢复失败，保留隔离 Notebook，不继续后续 mutation，并记录 OneNote 版本、Office channel 和操作前后快照。

## 10. 自动化边界

### 仓库开发规则

凡真实执行时需要 mutation policy 权限的 tool，包括 Write、Delete、Permanent Delete、Experimental Mutation、Raw XML 以及未来新增的非只读权限，都必须采用本页这种半自动化手动验证：

1. 自动化 pytest 只允许 mock/纯合同测试，不能访问真实 OneNote；
2. 真实场景统一放在 [`tests/manual_validation/`](../../tests/manual_validation/README.md)，通过一个总入口由用户显式选择顶层场景；每个 `run.py <scenario>` 自身包含 lifecycle create、该场景最小 fixture、mutation、report 与 close/keep；不得公开辅助 action，也不得增加聚合或 batch 入口；
3. 每个 scenario 最多启动一个 MCP 子进程。Runner 为其推导覆盖 fixture、mutation、evidence 与 restore/cleanup 的静态最小权限闭包，并在 fixture 前用 `health_check` 精确核验；源 Notebook 生命周期只能通过精确 lease 约束的窄 wrapper 操作；
4. 使用专用可丢弃 Notebook、精确 ID、最新确认字段和 before/after 证据；可恢复操作还必须执行恢复与 restored 回读；
5. 不可恢复操作只能命中 manifest 白名单中的 disposable 对象，并在报告中明确最终状态和人工处理方式；
6. 新增或修改非只读 tool 时，必须同步新增/更新对应 manual scenario 和使用命令；用户完成隔离实测前，不得声明真实后端验证完成。

该规则不授权自动运行 mutation，也不允许用普通集成测试、临时脚本或直接手调 tool 绕过 Runner 的权限矩阵、身份检查和证据链。

### 默认测试边界

仓库中的 `write_contract` pytest 只使用 mock，不接触 OneNote；可在明确授权后单独运行：

```powershell
.venv\Scripts\python.exe -B -m pytest -m write_contract -p no:cacheprovider
```

真实 COM mutation 永远不能进入默认 CI、pre-commit 或 smoke test。`write_contract` 仅是 mock 合同测试；真实隔离验证必须由用户在终端明确启动。

本地程序化 Runner 不是默认自动化：只有用户本人手动运行具体 `run.py <scenario>` 才构成授权；Agent 只能修改 runner、运行不接触 OneNote 的合同测试或把命令交给用户，不能代为执行。Runner 为该场景唯一的 MCP 子进程开启完整闭环所需的静态最小权限，不要求额外权限开关或二次确认，也不跨场景合并权限。当前通用隔离 Runner 中永久 OneNote Delete 和 raw XML 始终关闭；所有 scenario suite 都不删除本地 Notebook 文件。将来若开发相应 tool，只能新增权限更窄、目标约束更强的独立手动场景，不能扩大现有场景权限。
