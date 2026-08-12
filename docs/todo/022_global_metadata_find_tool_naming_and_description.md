# 022：全局元数据查找 Tool 命名与 Agent 可见合同

> ID：022
> 状态：待办
> 优先级：P1
> 类型：公开工具契约 / Agent 可发现性
> 更新日期：2026-08-12

## 背景

当前公开 Tool 名称 `query_hierarchy` 偏向实现术语，无法让 Agent 仅从名称判断两个关键事实：它查询的是一次 OneNote hierarchy 快照中的结构化元数据，而不是 Page 正文；它也没有调用方可选的 Notebook scope，实际意图是跨全部当前已打开 Notebook 的全局查找。

这容易产生两类误用：

- Agent 为查找 Page 标题而调用成本更高的 `search_pages`；
- Agent 误以为 `query_hierarchy` 只查询某个局部层级，或者误以为 `limit` 能限制 COM 获取和元数据扫描范围。

当前实现还只是获取完整 `GetHierarchy("", hsPages)` 快照后在 Python 中过滤，并未像全局 `search_pages` 一样显式建立“仅限已打开 Notebook”的集合边界。因此不能只改描述而继续让真实行为依赖 COM 快照是否包含 `isClosed=true` 的 Notebook。

## 设计结论

将公开 Tool 从 `query_hierarchy` 重命名为：

```text
global_query
```

名称保持简短，并把详细语义交给 Tool description：

- `global`：明确该 Tool 没有局部 Notebook scope，范围是全部当前已打开 Notebook；
- `query`：表达对结构化元数据应用条件过滤，不与 Page 正文全文搜索混同；
- `hierarchy`、`metadata` 等实现细节不进入名称，由 description 和返回字段解释。

不采用 `find_items_across_open_notebooks`，因为名称过长且把完整 description 重复编码进 Tool 名；不采用 `query_all_hierarchy`，因为它继续暴露内部结构术语；不采用笼统的 `query`，因为名称无法提示范围可能较宽。

## Agent 可见 Tool 描述

Tool description 应直接说明使用时机、范围和反例，建议以以下文本为基线：

> Find Notebook, SectionGroup, Section, or Page metadata across all currently open OneNote notebooks. Use this for titles/names, exact parent relationships, and modification-time filters. This reads hierarchy metadata only and does not search Page body content; use `search_pages` for body-text search. `limit` caps returned items after the global metadata snapshot is filtered.

参数 description 也必须进入生成的 Tool schema，至少说明：

- `resource_type`：必填枚举 `notebook | section_group | section | page`；
- `name_equals`：不区分大小写的完整标题/名称匹配；
- `name_contains`：不区分大小写的标题/名称子串匹配，不是正文搜索；
- `parent_id`：只匹配直接父关系，不表示递归 subtree；Page 接受直属 Section ID 或缩进父 Page ID；
- `modified_after` / `modified_before`：约定并校验公开时间格式后再比较；
- `include_recycle_bin`：只决定是否包含已打开 Notebook 范围内的回收站对象，不得把已关闭 Notebook 加入范围；
- `limit`：只限制过滤后的返回数量，不减少 `GetHierarchy` 快照大小，也不是扫描预算。

Agent 的选择规则应保持一句话可判定：

```text
标题、名称、类型、直属父级或修改时间 → global_query
Page 正文内容                       → search_pages
```

## 全局范围语义

每次调用取得一次完整 hierarchy 快照，然后：

1. 识别 `resource_type="notebook"`、`is_open is not false` 且不属于回收站的 Notebook；
2. 只保留这些 Notebook 本身及其后代对象；
3. 根据 `include_recycle_bin` 决定是否保留这些 Notebook 内部的回收站对象；
4. 在 Python 中应用所有元数据过滤器；
5. 计算 `total_matches`，最后应用 `limit`。

范围不得扩展到已关闭 Notebook、本地备份目录、磁盘 `.one` 文件或 Page 正文。没有已打开 Notebook 时成功返回空集合。

返回应增加稳定的范围说明，使 Agent 无需从 Tool 名称反推执行边界：

```json
{
  "items": [],
  "count": 0,
  "total_matches": 0,
  "truncated": false,
  "scope": {
    "mode": "all_open_notebooks",
    "notebook_count": 0
  },
  "query_kind": "hierarchy_metadata"
}
```

`query_kind` 明确区分元数据过滤与 `search_pages` 的 Page 文本检索；`scope` 不得伪造 COM 对象 ID。

## 迁移策略

仓库当前仍为 `0.1.0` alpha，建议执行一次协调的公开 Tool 重命名：

- 默认 profile 注册 `global_query`；
- 删除默认 profile 中的 `query_hierarchy`，避免 Agent 同时看到两个等价 Tool 后随机选择；
- 不在 service 中长期维护两套入口或隐式别名；
- 如果发布兼容性要求必须保留旧名，只允许给出一个有明确移除版本的短期 deprecated alias，并确保描述把 Agent 引向新 Tool；该例外需要单独决策，不能成为默认方案。

## 实施范围

1. 重命名 tools 层公开函数和默认 Tool 注册项，service 内部方法可另行选择不暴露的实现名称；
2. 将参数类型收紧为可生成枚举、范围和时间格式约束的 schema，并补充每个参数的 Agent 可见 description；
3. 对完整 hierarchy 快照建立显式的 all-open-notebooks 过滤边界；
4. 返回 `scope` 与 `query_kind="hierarchy_metadata"`，保留现有 `items/count/total_matches/truncated` 字段；
5. 更新 `health_check` 的能力枚举或诊断字段，使宿主能发现全局元数据查询能力；
6. 更新 `docs/design/tool_contracts.md`、README 以及所有引用旧 Tool 名称的文档和示例；
7. 增加 Tool schema、全局范围、已关闭 Notebook、回收站、空 hierarchy、标题查询、直接父级、时间过滤、limit 和返回 envelope 的纯合同测试。

## 完成定义

- Agent 在默认 Tool 列表中只看到 `global_query`，不会同时看到无期限保留的等价旧名；
- Tool 名称、description、参数 description 和返回结构都明确表达“全部已打开 Notebook 的 hierarchy 元数据过滤”；
- Page 标题可通过该 Tool 查询且不读取任何 Page 正文；
- 已关闭 Notebook 及其后代始终不参与匹配，`include_recycle_bin=true` 也不能扩大该边界；
- `limit` 只在完成全局元数据过滤后截断，`total_matches` 与 `truncated` 保持准确；
- Agent 可从描述稳定判断何时使用本 Tool、何时使用 `search_pages`；
- 聚焦纯合同和完整自动化测试集通过；该只读元数据变更不需要真实 mutation scenario；
- 当前设计文档、README、health check 与最终实现一致。
