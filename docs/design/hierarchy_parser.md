# 独立层级解析器设计

> 状态：已实现  
> 更新日期：2026-08-04  
> 实现：`src/local_onenote_mcp/hierarchy.py`

## 1. 模块边界

`hierarchy.py` 是 Notebook、SectionGroup、Section、Page 层级 XML 的唯一解析入口。它不导入 MCP server、COM bridge、mutation policy 或文件系统代码，只依赖 `domain.py` 中的静态领域模型。

```text
OneNote COM XML
      │
      ▼
hierarchy.parse_hierarchy
      │ stable typed resources
      ├─ resolve_resource
      ├─ find_resource_by_id / find_resource_by_path
      ├─ filter_resources
      └─ server tools / mutation confirmation / search
```

`xml_utils.py` 只负责 Page XML、正文/对象提取和 Page update XML 构造，不再定义 `HierarchyItem`、层级解析或标识符解析。

## 2. 公开内部 API

| API | 输入 | 输出/语义 |
| --- | --- | --- |
| `parse_hierarchy(xml, catalog=None)` | 完整或局部 COM hierarchy XML；可选完整 typed catalog | 稳定 `dict[]`，只含对象模型白名单字段。 |
| `resolve_resource(items, identifier, resource_type=None)` | typed snapshot、ID/路径/名称、可选类型 | 按 ID → 精确路径 → 唯一显示名解析。 |
| `find_resource_by_id(...)` | typed snapshot、ID、可选类型 | 单项或 `None`。 |
| `find_resource_by_path(...)` | typed snapshot、路径、可选类型 | 单项或 `None`。 |
| `filter_resources(...)` | typed snapshot、对象类型 | 同类型对象列表。 |
| `display_name(item)` | 任意 typed resource | 容器 `name` 或 Page `title`。 |

这些 API 是 Python 内部边界，不直接注册为 MCP 工具。

## 3. 完整树解析

完整 `GetHierarchy(..., pages)` XML 一次完成：

1. XML tag → `resource_type` 映射；
2. XML 白名单 attribute → 静态字段；
3. 容器 `parent_id/notebook_id/parent_section_group_id`；
4. 直属 `section_group_ids/section_ids` 和 `page_count`；
5. Page `order/page_level/parent_page_id/has_children`；
6. 回收站祖先传播；
7. 未知 XML attribute 丢弃。

Page 不生成旧式 `name` alias，解析和搜索统一通过 `display_name` 读取 `title`。

## 4. 局部 XML 与 catalog hydration

`FindPages`、`FindMeta` 等 COM 方法可能只返回 Page 或局部祖先片段。直接把片段当完整树会造成路径、Notebook/Section ID、Page order 错误。

调用方必须传入同一时点的完整 catalog：

```python
catalog = parse_hierarchy(full_hierarchy_xml)
matches = parse_hierarchy(find_pages_xml, catalog=catalog)
```

解析器先从片段提取命中 ID，再以完整 catalog 中同 ID 对象替换片段模型。因此 Search 和高级只读接口返回的仍是权威 typed resource，不泄漏搜索 XML 的未知 attribute。catalog 中不存在的 ID 才保留片段解析结果，此时调用方不得把缺失关系当作完整树证据。

## 5. 已删除的旧层

以下实现已移除：

- `xml_utils.HierarchyItem` 扁平 dataclass；
- `xml_utils.parse_hierarchy` 的原始 attribute 展开；
- `xml_utils.filter_items`；
- `xml_utils.resolve_item`；
- server 内 `_hierarchy_items/_resolve_id/_resolve_item/_find_item_by_*` 兼容链。

兼容的 `resolve_identifier` MCP 工具仍保留，但内部直接使用 typed snapshot 和 `resolve_resource`，返回值不再经历旧字段模型。

## 6. 测试责任

- `tests/test_hierarchy.py`：片段 hydration、Page title 解析、路径优先级和歧义处理；
- `tests/test_domain.py`：五类静态模型、关系和未知字段过滤；
- `tests/test_server.py`：typed parser 与工具层集成、Search 候选预算。

层级解析测试均为纯字符串输入，不连接 OneNote，也不需要写权限。
