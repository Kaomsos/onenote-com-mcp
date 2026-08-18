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
│  ├─ bridge.py               可信本地 COM 边界（PowerShell、JSON 传输）
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
- **`bridge.py`** 是可信的本地 COM 边界。它使用结构化 JSON/临时文件传输，绝不把不可信内容插值到 PowerShell 源代码或命令字符串。
- **`server.py`、`settings.py`、`policy.py`** 负责组合与进程级配置。环境变量读取集中管理；没有隐藏的替代注册路径。

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
