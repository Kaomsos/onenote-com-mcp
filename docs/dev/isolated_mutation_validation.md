# OneNote mutation 隔离验证流程

> 本文只定义人工触发流程。任何 Agent、CI 或本次开发会话都不得自动执行本流程。  
> 真实验证对象必须是专用、无业务数据、可丢弃的本地 Notebook。

## 1. 目标

隔离验证 COM `UpdateHierarchy` 在以下 P1 操作中的真实语义：

1. SectionGroup/Section Rename 是否保持对象 ID 和子对象；
2. Page Reorder/Page Level 是否保持 Page ID、正文和子树；
3. Section 在同 Notebook 内 Move 是否保持 Section ID、Page ID、顺序和完整 Page XML；
4. 回收站 Delete 是否满足默认非永久语义。

其中 `move_section` 在完成本流程前必须保持实验状态。永久删除不属于本流程。

## 2. 人工准备

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

复制一份只用于该 Notebook 的 MCP 配置并重启独立 server 进程：

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
3. 在 OneNote UI 中人工关闭并清理隔离 Notebook；Notebook Delete 不由本 MCP 提供；
4. 若任一步发生 ID 变化、内容摘要变化、重复 Section/Page 或恢复失败，保留隔离 Notebook，不继续后续 mutation，并记录 OneNote 版本、Office channel 和操作前后快照。

## 10. 自动化边界

仓库中的 `write_contract` pytest 只使用 mock，不接触 OneNote；可在明确授权后单独运行：

```powershell
.venv\Scripts\python.exe -B -m pytest -m write_contract -p no:cacheprovider
```

真实 COM mutation 永远不能进入默认 CI、pre-commit 或 smoke test。本次实现会话只运行 `-m "not write_contract"` 的只读测试。
