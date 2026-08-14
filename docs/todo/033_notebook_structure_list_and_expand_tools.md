# 033：Notebook 结构浏览的 List 与 Expand 工具重组

> ID：033
> 状态：待办
> 优先级：P1
> 类型：公开工具契约 / 层级浏览 / 对象操作模型 / Manual Validation
> 更新日期：2026-08-15

## 背景与决策

[TODO 022](022_typed_metadata_query_tools_and_native_scopes.md) 已完成四层 Typed Metadata Query。Query 定位为平展、过滤和分页的 hierarchy metadata 查询；后续不再把无过滤 Query 与 List 视为同一产品入口。

本 TODO 将层级浏览工具重组为两类：

- `list_notebooks` 是唯一保留的 `list_*`，作为 OneNote 没有真实 root 对象时的 dangling root discovery 入口；
- `expand_*` 返回以一个精确对象为 root 的嵌套树，按对象语义有限展开；`expand_hierarchy` 提供通用数值深度展开。

List/Expand 面向人类浏览习惯，Query 面向平展查找；精确单对象读取继续使用 `get_*`，Page 正文搜索继续使用 `search_pages`。`expand` 只表示读取并返回 hierarchy tree，不修改 OneNote GUI 的展开状态。

## 最终公开工具面

```text
list_notebooks()

expand_notebook(id)
expand_section_group(id)
expand_section(id)
expand_page(id)

expand_hierarchy(root_id, max_depth=8, include_recycle_bin=false)
```

四个 typed Expand 的唯一参数统一命名为 `id`。工具名称已经固定 root 类型，生产实现必须按精确 COM ID 验证实际对象类型；未知 ID、类型不符、已关闭 Notebook 或不允许的回收站 root 均 fail closed。

`expand_hierarchy` 是当前 `get_tree` 的直接更名，继续接受任意四层 hierarchy 对象作为 `root_id`，按 `max_depth` 展开，并保留 `include_recycle_bin` 选项。不得长期保留 `get_tree` alias。

## 对象操作模型

| 操作 | 工具 | Notebook | SectionGroup | Section | Page |
| --- | --- | --- | --- | --- | --- |
| List 根对象 | `list_notebooks` | `T` | — | — | — |
| Typed Expand | `expand_notebook` / `expand_section_group` / `expand_section` / `expand_page` | `T` | `T` | `T` | `T` |
| 通用深度展开 | `expand_hierarchy` | `T` | `T` | `T` | `T` |
| 平展元数据查询 | `query_notebook` / `query_section_group` / `query_section` / `query_page` | `T` | `T` | `T` | `T` |

选择规则：

```text
有哪些已打开 Notebook                    -> list_notebooks
按对象语义浏览 Notebook/Group/Section/Page -> expand_*
任意 root + 数值深度                     -> expand_hierarchy
按字段过滤、关系筛选或分页                 -> query_*
精确单对象 metadata                       -> get_*
Page 正文搜索                             -> search_pages
```

## 展开语义

| 工具 | Root | 完整展开边界 |
| --- | --- | --- |
| `expand_notebook(id)` | Notebook | 穿过任意层嵌套 SectionGroup，展开到全部 Section；Section 为叶节点，不读取 Page |
| `expand_section_group(id)` | SectionGroup | 不越出目标 Group，穿过嵌套 Group，展开到全部 Section；Section 为叶节点 |
| `expand_section(id)` | Section | 返回全部 Page，并按 `parent_page_id/page_level` 组织完整子页树 |
| `expand_page(id)` | Page | 返回该 Page 的完整后代子页树，不包含兄弟 Page |
| `expand_hierarchy(...)` | 任意四层对象 | 沿统一关系图展开到 `max_depth`；语义与当前 `get_tree` 一致 |

“有限展开”表示由 Notebook/Group/Section/Page 对象边界限制，而不是分页或静默数量截断。Typed Expand 不接受 `max_depth`、scope、过滤、分页、名称/path selector 或 `include_xml`。如果完整轻量 metadata tree 超出公共响应边界，调用必须明确失败，不得返回伪完整树。

## 调用与响应 Schema

四个 typed Expand 共享相同调用形状：

```text
{ "id": "exact OneNote COM ID" }
```

全部 Expand（包括 `expand_hierarchy`）共享完全相同的核心响应：

```json
{
  "tree": {
    "item": {
      "id": "...",
      "resource_type": "section"
    },
    "children": [
      {
        "item": {},
        "children": []
      }
    ]
  }
}
```

`children` 中每一项递归使用相同的 `{item, children[]}` 节点 schema；叶节点固定为 `children=[]`。不新增 `tree_info`、`expansion_mode`、`stop_at_types` 或按对象命名的重复数组字段。工具名称与参数已经表达展开模式，业务响应只保留 `tree`。

关系规则必须与当前对象模型一致：

- Notebook、SectionGroup、Section 的容器关系来自 `parent_id`；
- 顶层 Page 挂到 `section_id`；
- 缩进 Page 优先挂到 `parent_page_id`；
- children 保持同一次 OneNote hierarchy snapshot 中的稳定顺序；
- 每个对象在一棵返回树中恰好出现一次，关系不完整或重复时 fail closed。

`list_notebooks()` 不伪造 COM root，保持独立的简单响应：

```json
{
  "items": [],
  "count": 0
}
```

它无参数、无过滤、无分页，只返回当前打开的 Notebook；结果 ID 与顺序必须等于无过滤 `query_notebook` 在稳定 hierarchy 下逐页取尽的结果。

## 共享实现边界

不得为五个 Expand 复制树实现。生产 service 应抽取一个共享关系图和 tree builder：

```text
读取最浅必要 typed hierarchy metadata
        ↓
验证 exact root、真实类型、open-only 与 recycle 边界
        ↓
建立 parent_id / section_id / parent_page_id 关系图
        ↓
应用 typed boundary 或 max_depth policy
        ↓
返回统一 tree={item,children[]}
```

建议的最浅 COM scope：

| 工具 | HierarchyScope |
| --- | --- |
| `list_notebooks` | `hsNotebooks` |
| `expand_notebook` / `expand_section_group` | `hsSections` |
| `expand_section` / `expand_page` | `hsPages` |
| `expand_hierarchy` | 由 root 和既有通用合同决定，但不得读取 Page 正文 |

`expand_section`、`expand_page` 和 `expand_hierarchy` 必须共用现有 `get_tree` 已验证的 Page indentation builder；不得重新仅凭局部 `page_level` 发明第二套父子算法。

## 直接移除旧工具

本 TODO 是一次公开只读契约重组，实施时直接移除：

```text
list_hierarchy
list_section_groups
list_sections
list_pages
get_tree
```

同时完成：

- `list_notebooks` 删除 `include_recycle_bin`，统一为无参数、open-only root discovery，并把旧 `notebooks` 数组改为 `items`；
- 删除 tools 注册、仅服务旧入口的 service adapter、schema tests、README 示例、health capability 和 manual-validation allowlist 中的旧名；
- `get_tree` 的 service/tree builder 能力保留并被 Expand 复用，但公开名称只保留 `expand_hierarchy`；
- 不保留 deprecated alias、转发空壳、双名称注册或静默兼容参数；
- 更新当前设计与对象操作模型；历史证据文档只在需要避免误导当前契约时补充迁移说明。

## 单一 Manual Validation 场景

复用并扩展 [TODO 032](032_hierarchy_navigation_manual_validation.md) 的现有 `hierarchy-navigation` 场景，作为唯一的 Notebook 结构浏览场景；不得再并存一个只重复 Expand 关系的第二场景。

Fixture 使用两个同时打开的 disposable Notebook role。主 role 至少包含：

```text
Notebook
├─ Root Section
└─ Outer Group
   ├─ Group Section
   └─ Inner Group
      └─ Target Section
         ├─ Parent Page
         │  ├─ Child A
         │  │  └─ Grandchild
         │  └─ Child B
         └─ Root Sibling Page
```

同一场景依次验证：

1. `list_notebooks()` 包含两个 fixture role，并与无过滤 `query_notebook` 的独立分页基线一致；
2. `expand_notebook(id)` 保留多层 Group/Section 顺序，所有 Section 都是叶节点且没有 Page；
3. `expand_section_group(id)` 不越出指定 Group，并展开到全部 Section；
4. `expand_section(id)` 将 level 1/2/3 Page 投影为精确树，两个 child sibling 和 grandchild 各出现一次；
5. `expand_page(id)` 只返回 Parent Page 的后代，不包含 root sibling；
6. `expand_hierarchy(root_id, max_depth=...)` 分别接受 Notebook、SectionGroup、Section、Page，并在未被深度截断的局部与对应 typed Expand 产生相同节点关系；另保留一个明确的 depth boundary case；
7. scenario MCP audit 证明所有生产浏览调用只读取 hierarchy metadata，不调用 Page XML、正文、对象或二进制工具。

场景继续支持 fresh 与 `--use-cache`，默认 `included_in_all=false`；只有用户完成真实 fresh/cache 验收并单独批准后才可进入 `all`。Agent 只能运行纯测试和显式 `--dry-run`。

## 自动化测试

- 公开 tool registry 和生成 schema 精确包含最终六个浏览入口，不含五个旧名；
- 四个 typed Expand 都只接受必填非空 `id`，并严格拒绝错误 root 类型；
- 所有 Expand 返回相同递归 tree schema，叶节点、顺序和对象唯一性一致；
- Notebook/Group 的 typed boundary 不读取 Page，Section/Page 正确重建缩进树；
- `expand_hierarchy` 保持原 `get_tree` 的四类 root、`max_depth` 和 recycle 合同；
- `list_notebooks` 与稳定的无过滤 Query 全集等价，无参数且返回 `items/count`；
- 未知 ID、关闭 Notebook、回收站 root、关系断裂、重复 ID 和读取失败均 fail closed；
- tools/service/health/README/design/manual-validation 中不存在旧公开名称或兼容 adapter；
- 运行聚焦纯测试、manual-validation 纯合同、完整 `pytest -q`、相关 `--dry-run --json` 和 `git diff --check`。

## 文档交付

同步更新：

- `docs/overview/onenote_object_model_assessment.md` 的对象操作矩阵；
- `docs/design/tool_contracts.md` 的公开签名、响应和选择规则；
- `docs/design/object_model.md` 的容器关系、Page indentation 与共享 tree builder；
- 当前 architecture、README、manual-validation README 和相关开发文档；
- TODO 032 对 `get_tree` 名称和最终场景范围的引用；
- 默认 Tool 数量、health capability 和示例调用。

## 完成定义

- 生产代码交付 `list_notebooks`、四个 typed Expand 和 `expand_hierarchy`，且直接移除五个旧公开工具名；
- 四个 typed Expand 参数统一为 `id`，全部 Expand 共享 `tree={item,children[]}` 响应、关系算法和 tree builder；
- Notebook/Group 在 Section 停止，Section/Page 返回完整 Page indentation subtree，通用工具保持数值深度合同；
- 对象操作模型、工具合同、object model、README、health 和默认 registry 与最终工具面一致；
- 单一 `hierarchy-navigation` 场景覆盖 `list_notebooks`、五个 Expand、四层 root、depth boundary 和无正文读取；
- 聚焦与完整纯测试、dry-run 和 diff 检查通过；
- 用户确认 fresh 与 cache 两种真实场景均通过；是否纳入 `all` 仍需独立批准。
