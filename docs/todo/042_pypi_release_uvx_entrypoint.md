# 042：PyPI 发布准备与 uvx 调用入口

> ID：042
> 状态：待办
> 优先级：P1
> 类型：发布 / 打包 / 分发
> 更新日期：2026-08-19

## 决策摘要

为首个公开版本准备 PyPI 发布，使用户可以通过 `uvx local-onenote-mcp` 直接运行本 MCP 服务器，而不必 clone 仓库或依赖 npm launcher。本 TODO 只覆盖包元数据、构建产物审计、TestPyPI 演练和双语文档准备；向正式 PyPI 上传、创建版本 tag 和 GitHub Release 属于对外动作，必须由用户显式执行，且以 [TODO 023](023_public_repository_release_readiness.md) 的公开发布硬门（含 GPL relicense 与来源合规）为前置。

## 背景

- 当前安装方式是 npm global launcher 和仓库内 `uv sync` 开发流程；`uvx` 直接调用要求包已发布到 PyPI（或用户显式使用 `--from git+...`）。
- `pyproject.toml` 已具备 `[project.scripts] local-onenote-mcp` 入口和 hatchling build backend，但 `license` 字段当前仍为 MIT，必须随 TODO 023 的 GPL 决策同步切换。
- 项目仅支持 Windows + OneNote Desktop；PyPI 元数据与安装文档必须如实表达该边界，不得暗示跨平台可用。

## 工作范围

### A. 包名与元数据

- 核对 PyPI 上 `local-onenote-mcp` 包名可用性；如被占用，确定备选名并在同一变更中同步 README、客户端配置样例、`[project.scripts]` 入口名和 TODO 023 品牌决策；
- 完善 `pyproject.toml`：GPL 许可证表达（`license` 字段与 `License ::` classifier 一致）、`keywords`、`urls`（Homepage/Issues/公开文档/Changelog）、long description 的 PyPI 渲染检查；
- 明确 Windows-only 边界的表达方式：至少在包描述与 README 中显式声明；是否追加运行时平台显式报错以现有行为核对为准，不为打包引入新逻辑。

### B. 构建产物审计

- 以 `uv build` 生成 wheel 和 sdist，逐项审计内容：只包含 `src/local_onenote_mcp`、许可与说明文件；不得包含 `tests/`、`docs/`、`docs-public/` 大件、`.local-validation/`、本机 MCP 配置或任何证据文件；
- 验证 wheel 安装后 `local-onenote-mcp` console script 可解析（`--help` 或 stdio 握手层面，不接触 OneNote）；
- 记录产物清单作为证据，供发布前复核。

### C. uvx 调用与客户端文档

- 在干净环境验证 `uvx local-onenote-mcp` 的冷启动 stdio MCP 握手（TestPyPI 演练之后执行）；
- 在根 README 与 `docs-public/` 双语使用文档中新增 uvx 安装/配置样例（`"command": "uvx", "args": ["local-onenote-mcp"]` 及 TOML 等价形式），与现有 npm 与开发安装方式并列，并给出版本 pin 建议（`local-onenote-mcp==X.Y.Z`）与升级说明；
- 确认现有 npm launcher 的去留定位（保留、标注为备选或弃用），避免双入口文档漂移。

### D. 发布通道与安全边界

- 选择发布通道：推荐 GitHub Actions Trusted Publishing（OIDC），备选用户本机手动 `uv publish`；真实启用哪一种由用户确认；
- 完成一次 TestPyPI 全链路演练：build → 上传 TestPyPI（用户执行）→ 干净环境 `uvx --index ...` 安装 → `scripts/smoke_mcp.py --tools-only` 通过；
- 任何发布相关 CI 工作流只能构建与运行纯测试，不得接触真实 OneNote 数据或触发 mutation scenario；
- 准备版本号策略、Changelog 条目和 yank/回滚方案。

## 非目标与安全边界

- 不在本 TODO 内向正式 PyPI 上传包、创建 tag 或 Release；上传动作（含 TestPyPI）由用户显式执行；
- 不改变 local-only 运行边界，不为打包或发布引入网络运行时依赖、遥测或云路径；
- 不移除现有 npm launcher（其长期去留在 C 节定位后由用户决定）；
- smoke 验证仅使用 `--tools-only` 等不接触 OneNote 后端的形式；真实 OneNote 验收仍由用户执行。

## 完成定义

- [ ] 包名可用性核实，最终名称与入口名确认并同步全部引用；
- [ ] `pyproject.toml` 元数据完成且 GPL 许可表达与 TODO 023 一致，PyPI long description 渲染检查通过；
- [ ] `uv build` 产物内容审计通过并记录清单，console script 安装后可解析；
- [ ] TestPyPI 演练闭环：用户上传后，干净环境 `uvx` 安装并通过 `scripts/smoke_mcp.py --tools-only`；
- [ ] 根 README 与 `docs-public/` 双语文档的 uvx 安装/配置样例完成并互链，npm launcher 定位明确；
- [ ] 发布通道、负责人、版本号与回滚方案经用户确认；正式 PyPI 发布时间点由用户批准（本条不要求实际发布完成）。

## 完成证据记录

| 证据 | 结果/位置 |
| --- | --- |
| 包名可用性核查 | 待填写 |
| 元数据与渲染检查 | 待填写 |
| 构建产物清单审计 | 待填写 |
| TestPyPI 演练与 uvx smoke | 待填写 |
| 双语文档更新与互链检查 | 待填写 |
| 发布通道与负责人确认 | 待填写 |

## 关联

- [TODO 023](023_public_repository_release_readiness.md)：公开发布硬门、GPL relicense 与 `docs-public/` 双语文档结构；本 TODO 的正式发布以其完成为前置。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
