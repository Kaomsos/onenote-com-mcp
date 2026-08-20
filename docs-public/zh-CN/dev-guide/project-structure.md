# 项目组织结构

[English](../../en/dev-guide/project-structure.md) | [文档首页](../../README.zh-CN.md)

## 仓库布局

```text
onenote-com-mcp/
├─ src/local_onenote_mcp/     生产 MCP 服务器
│  ├─ domain/                 类型化 domain 对象（独立于 transport/COM）
│  ├─ page/                   Page 解析、格式化、构建、图片与 Copy 语义
│  ├─ services/               应用编排；policy、精确 ID、预算
│  ├─ tools/                  services 之上的精简 MCP 适配层
│  ├─ bridge.py               可信本地 COM 边界（adapter 装配与 audit）
│  ├─ com_client.py           常驻/one-shot PowerShell COM adapter
│  ├─ server.py / settings.py / policy.py   组合与进程级配置
│  └─ operation_catalog.py / tool_surface.py  canonical operation registry 与工具发布面
├─ tests/                     确定性自动化测试（mock/合同级）
│  └─ manual_validation/      HUMAN-GATED 真实后端验证框架
├─ docs/                      维护者文档：设计契约、开发流程、lesson、TODO 台账
├─ docs-public/               本公开文档（双语）
├─ scripts/                   只读 smoke 测试与诊断
├─ bin/                       npm launcher 入口
└─ pyproject.toml / package.json / uv.lock
```

## 架构分层

生产代码执行严格分层，权威说明见 [architecture](../../../docs/design/architecture.md) 和 [Operation Runtime](../../../docs/design/operation_runtime.md)：

- **`domain/`** 定义类型化 domain 对象（Notebook、SectionGroup、Section、Page、PageContentObject），与 MCP transport、子进程执行和 OneNote COM 访问保持独立。
- **`page/`** 拥有 Page XML 语义：解析、格式化、构建、图片和面向 Copy 的投影。XML 处理集中管理，由 round-trip/invariant 测试覆盖。
- **`services/`** 是编排层，也是 policy、精确 ID 定位、confirmation 字段、预算和可恢复失败行为的主要执行边界。
- **`tools/`** 把 MCP 输入输出适配到 services。Tool 函数保持精简、类型化，与已记录的响应 envelope 一致；绝不重新实现 service 逻辑。
- **`bridge.py`** 是可信的本地 COM 边界。它装配单一 `ComClient`、独占 audit 与错误投影，绝不把不可信内容插值到 PowerShell 源代码或命令字符串。
- **`server.py`、`settings.py`、`policy.py`** 负责组合与进程级配置。环境变量读取集中管理；没有隐藏的替代注册路径。

## PowerShell 与 OneNote COM 运行依赖

生产 bridge 仅支持 Windows，并通过 `powershell.exe` 启动 Windows PowerShell 5.1。默认 adapter 是常驻 STA host：

```text
powershell.exe -NoProfile -NonInteractive -Sta -EncodedCommand <UTF-16LE Base64>
```

生产代码不调用 PowerShell 7（`pwsh`），因此 `pwsh` 不是等价的兼容性 probe 或受支持的 bridge 替代 host。默认 host 创建一个 `OneNote.Application` COM client，并在同一 MCP 进程的后续 backend call 中复用。只有显式 fallback 才设置 `LOCAL_ONENOTE_BRIDGE_ADAPTER=one_shot_powershell`。常驻 host 初始化失败必须 fail-closed，不得静默降级。

OneNote COM XML 调用固定使用 OneNote 2013 schema 数值 `2`（`XMLSchema.xs2013`）。Hierarchy scope 是独立参数：

| 读取形状 | `scope` | `schema` |
| --- | ---: | ---: |
| 仅 Notebook | `2` | `2` |
| 读取到 Page 层级 | `4` | `2` |

尤其不能把 `HierarchyScope.hsPages = 4` 传作 XML schema。Schema 是内部常量，不是用户配置或失败后的重试 fallback。精确诊断与证据边界见维护者权威流程：[OneNote COM Bridge 运行依赖](../../../docs/dev/onenote_com_bridge_runtime.md)。

## Operation Registry

一个 canonical 的 **53-operation Registry** 为每个公开工具统一持有：发布面、类别、授权、独立平台 preflight policy、执行策略、handler、审计和重试语义。读取共享进程级 lease；mutation 和 lifecycle effect 通过 preflight、执行、reconciliation 和稳定 read-back 使用独占协调。

少量内部/孵化能力（`resolve_identifier`、`get_page_xml`、`navigate_to_url`、`get_special_locations`、`get_parent`）刻意不注册：没有任何环境开关能暴露它们。

## 文档地图

维护者文档位于 [`docs/`](../../../docs/README.md)，是权威来源：

| 目录 | 职责 |
| --- | --- |
| `docs/design/` | 当前架构、对象模型、解析器边界和工具契约 |
| `docs/dev/` | 开发、验证和排障流程 |
| `docs/lesson/` | 带明确证据边界的可复用工程经验 |
| `docs/overview/` | 带时间范围的调研与评估报告 |
| `docs/todo/` | 不可变 ID、状态纪律的项目 TODO 台账 |

本公开文档（`docs-public/`）只做摘要并链接到这些来源；绝不把契约细节 fork 成第二个相互竞争的权威。
