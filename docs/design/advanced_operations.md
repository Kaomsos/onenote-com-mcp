# 内部低层与诊断操作

> 状态：当前实现态
> 更新日期：2026-08-15

本项目不再提供生产 `advanced` MCP profile。生产 `tools/list` 只有 56 个 task-level typed Tool；低层 COM、raw Page XML 和已证明不受后端支持的能力只能留在 Service、Bridge、纯测试或明确的人工诊断代码中，不能据其内部实现推导出产品能力。

## 1. Exposure 与授权边界

- `src/local_onenote_mcp/tools/advanced.py` 的生产工具集合为空；`tools/__init__.py` 只注册默认 typed Tool。
- `LOCAL_ONENOTE_ENABLE_RAW_XML=true` 不参与 Tool 注册，也不会改变 `tools/list`。它只可能满足内部低层 service 自身的 raw mutation 授权检查。
- `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP=true` 同样只服务保留的内部诊断实现，不会创建 MCP adapter 或 Registry binding。
- Registry 中不存在 advanced binding；启动审计只接受默认生产 Tool 的精确集合。
- 隐藏不等于授权。内部诊断路径仍必须满足其 write/delete/raw policy，并且不能被公开 typed Tool 间接转换为任意 raw payload 入口。

## 2. 保留的内部能力

| 内部方法或 bridge operation | 保留用途 | 生产边界 |
| --- | --- | --- |
| `find_meta` | 已解析 hierarchy 上的底层 metadata 诊断 | 不注册；用户任务使用 typed Query/Search |
| `open_hierarchy` | 路径打开、生命周期和 COM 行为诊断 | 不注册；不能作为名称定位 mutation 入口 |
| `update_page_xml` | typed Page mutation 内部实现或 raw XML 诊断 | 不注册；内部 raw mutation仍要求 Writes + Raw XML |
| `merge_sections` | Section Merge 后端能力探测 | 不注册；没有稳定 typed Merge 产品合同 |
| `set_filing_location` | filing location 后端能力探测 | 不注册；没有用户级产品合同 |
| `delete_hierarchy` / `update_hierarchy` | typed Delete、Move、Reorder/Reparent 的受约束 bridge 原语 | 不接受公开任意 ID/XML Tool |
| `reorder_section_group` | 保存后端不支持结论的诊断 Service 与历史 fixture | 不注册；不能通过 Rename、Copy/Delete 或 raw XML 模拟 |

`delete_hierarchy` 与任意 hierarchy XML update 不存在公开 service facade 或 MCP adapter。Bridge 中保留固定 COM operation 只允许受约束 typed Service 使用精确 ID或内部构造 XML。

## 3. SectionGroup Reorder 的负能力证据

2026-08-10 的用户触发隔离验证中，Notebook 直属 Group 的 `A,B,C → A,C,B` 请求虽然由 `UpdateHierarchy(xs2013)` 返回成功，按精确 ID 立即回读仍是后端固定的名称升序。该结果证明当前后端没有可验证的 SectionGroup sibling order 原语。

因此当前产品合同是：

- 不存在 `reorder_section_group` MCP Tool；
- 环境变量不能恢复或枚举该 Tool；
- Service 与 fixture 可保留用于证据回归，但不构成生产注册旁路；
- Agent 不得以其他 mutation 组合模拟这个不受支持的能力。

## 4. 与默认 typed profile 的关系

Page、Section 和 SectionGroup Reparent 已迁移为精确 ID、具名 policy 和具名人工场景保护的 typed Tool。它们不依赖任何 advanced exposure，也不接受调用方提供 hierarchy XML。

能力评级只针对生产 typed profile：

- `T`：默认注册的稳定 typed 契约；
- `E`：默认注册、由独立实验 policy 保护的 typed 契约；
- `X`：没有 typed 产品契约、后端不支持或明确不公开；
- 内部低层/诊断方法：不是 Tool，不进入 `T/E/X` 对象—操作矩阵。

公开参数与 policy 见 [工具参数与返回格式](tool_contracts.md)，对象级能力见 [OneNote 对象模型评估](../overview/onenote_object_model_assessment.md)。
