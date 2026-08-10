# 008：全部已打开 Notebook 的全局 Page 搜索

> ID：008
> 状态：待办
> 优先级：P1
> 类型：公开工具契约 / Search 能力
> 更新日期：2026-08-10

## 背景

当前 `search_pages` 强制要求 `scope_type` 为 `notebook`、`section_group` 或 `section`，并把非空 `scope_id` 解析为一个真实 hierarchy 对象。因此单次调用的最大范围是一个 Notebook，不能表达 OneNote Desktop `Ctrl+E` 对全部已打开 Notebook 的检索。调用方虽然可以先 `list_notebooks` 再逐个搜索，但这会把 `max_results`、扫描预算和错误边界拆散到多个请求中，也无法形成稳定的全局结果契约。

## 目标

为 `search_pages` 增加向后兼容的全局 scope：

```json
{
  "query": "needle",
  "scope_type": "all_open_notebooks",
  "scope_id": ""
}
```

- `scope_type="all_open_notebooks"` 表示查询本次 hierarchy 快照中的全部已打开 Notebook；
- 该 scope 不扫描已关闭 Notebook、本地备份目录或磁盘上的 `.one` 文件，继续保持基于 OneNote COM hierarchy、local-only 且不直接解析二进制文件；
- `scope_id` 对该 scope 必须为空，并在公开 tool 签名中变为可省略的默认空字符串；现有三种 typed scope 仍必须提供非空、类型匹配的 ID；
- `max_results` 是整个全局查询的上限，不得按 Notebook 重置；
- 现有调用者、默认 backend 和单 Notebook/Section 搜索语义保持不变。

## Backend 语义

### `local_scan`

- 从同一次 hierarchy 快照取得全部已打开 Notebook 的候选 Page，不通过名称重新选择对象；
- 在读取首个 Page 正文前，对跨 Notebook 的候选 Page 总数执行一次 `SearchBudget.max_pages` 检查；
- `max_page_chars`、`max_total_chars`、`max_seconds` 和 `max_results` 均按整个调用累计，不得为每个 Notebook 重新计数；
- 结果保持稳定、可复现的 hierarchy 顺序，并保留每个 Page 的 `notebook_id`、路径及其他 typed 元数据；
- `include_recycle_bin=false` 时排除所有 Notebook 的回收站 Page，`true` 时仍受同一全局预算约束。

### `onenote_index`

- 对 OneNote COM `FindPages` 传入空 `start_id`，由 OneNote index 在全部已打开 Notebook 中执行一次查询；
- COM/index 失败必须作为 `onenote_index` 错误返回，不得静默回退到 `local_scan`；
- 解析结果时使用同一次完整 hierarchy catalog 补全 `notebook_id`、父级和路径；
- `max_results` 在跨 Notebook 结果上统一截断，snippet hydration 也必须受有界的页数、字符数和耗时控制；
- 在真实 OneNote 验证前，不把空 `start_id` 与 Desktop `Ctrl+E` 的完全等价性视为已证实事实。

## 返回契约与错误边界

- 全局查询继续返回 `pages`、`count`、`search_backend` 和 `scan_budget`；
- `scope` 返回稳定的合成描述，例如 `resource_type="all_open_notebooks"`，并报告本次快照覆盖的 Notebook 数量；不得伪造真实 OneNote 对象 ID；
- `scope_type="all_open_notebooks"` 搭配非空 `scope_id` 时 fail closed，避免调用方误以为该 ID 会进一步过滤范围；
- typed scope 搭配空 `scope_id`、未知 scope、空查询和无效 backend 继续明确拒绝；
- 没有已打开 Notebook 或没有候选 Page 时成功返回空结果，而不是退化为磁盘扫描；
- 全局范围超过预算时返回明确的预算错误，且不得先读取部分 Page 后再以候选数量超限结束。

## 实施范围

1. 扩展 `search_pages` tool schema、`SearchService.search` 的 scope 校验与合成 scope 返回结构；
2. 复用单次完整 hierarchy 快照实现 `local_scan` 全局候选集，避免逐 Notebook 重复枚举和重复预算；
3. 为 `onenote_index` 接入空 `start_id` 的全局 `FindPages` 调用，并保持无隐式 backend fallback；
4. 对 index 结果的 snippet hydration 增加全局有界行为，确保全局 scope 不绕过搜索资源上限；
5. 补充自动化合同测试，覆盖两种 backend、跨 Notebook 命中、全局结果上限、全局预算、回收站过滤、空 hierarchy、scope 参数组合、metadata hydration 和显式失败；
6. 由用户在至少两个已打开、内容可控的 Notebook 上执行只读真实验收，确认两种 backend 的跨 Notebook 命中、结果归属和错误行为；该验收不得由 pytest、CI、hook 或智能体自动触发；
7. 实现完成后同步更新 `docs/design/tool_contracts.md`、`docs/design/architecture.md`、根 README 及相关 search/health-check 文档。

## 完成定义

- `search_pages(query, "all_open_notebooks")` 可在一次调用中返回多个已打开 Notebook 的 Page，并允许省略 `scope_id`；
- 原有 `notebook/section_group/section` 调用和默认 `local_scan` 行为保持兼容；
- `local_scan` 对全部候选 Page 使用单一、先检查后读取的全局预算，结果数和耗时等计数不会按 Notebook 重置；
- `onenote_index` 使用空 `start_id` 执行全局查询，失败时不回退，结果可正确补全所属 Notebook；
- 全局 `scope`、空结果、回收站、snippet、预算超限和参数冲突拥有稳定响应或错误合同；
- 自动化测试覆盖两种 backend 及关键边界，并通过完整纯测试集；
- 用户确认至少两个已打开 Notebook 的真实只读检索证据后，记录环境、调用参数、命中归属和 backend 结果；
- 当前设计文档、README 和 TODO 索引与最终实现一致。
