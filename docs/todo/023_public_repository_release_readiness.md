# 023：公开仓库发布准备与来源合规

> ID：023
> 状态：待办
> 优先级：P0
> 类型：公开发布 / 品牌与文档 / 社区治理 / 来源与许可证合规
> 更新日期：2026-08-19

## 决策摘要

本 TODO 是仓库切换为公开可见之前的硬发布门。品牌与 Demo、双语入口、开发文档的编写与公开发布、社区协作规范、来源署名与 relicense、Git 历史整理、原作者通知以及发布安全检查必须全部有可复核证据；只准备草稿、只在私有仓库中保存开发文档、只存在 `LICENSE` 文件或只完成代码测试均不足以标记为完成。

公开发布前继续保持仓库私有。本 TODO 只记录和组织发布工作，不授权在实施时未经用户确认就重写共享 Git 历史、force-push、修改远端可见性、发布包、创建 Release，或代表用户向原作者发送 Issue。

## 已确认方向决策（2026-08-19）

以下四项由用户确认，作为本 TODO 后续实施的固定输入：

1. **独立的双语对外文档目录。** 面向 GitHub 用户与外部开发者的公开文档放入新的独立目录 `docs-public/`（中英平行树），与面向维护者本人的内部文档 `docs/` 分离。`docs/` 继续作为当前架构、契约与流程的内部权威来源；`docs-public/` 是对外稳定入口，摘要之外链接到 canonical 内容，不复制会独立漂移的完整契约。首个公开版本采用仓库内文档形态，不建独立文档站。
2. **公开文档同时覆盖使用文档与开发文档。** 使用文档面向 MCP 用户（安装、客户端配置、权限门限、工具能力、限制与故障排查）；开发文档面向外部贡献者（项目组织结构、工程规则公开摘要、测试分层），并必须包含独立一章“手动验证框架”说明。
3. **PyPI/uvx 发布准备独立跟踪。** 打包元数据、`uvx local-onenote-mcp` 调用入口和发布演练由 [TODO 042](042_pypi_release_uvx_entrypoint.md) 承载；其正式发布仍以本 TODO 的公开发布硬门为前置。
4. **目标许可证为 GPL 强传染许可。** 本项目以 GPL（建议 `GPL-3.0-or-later`；`only`/`or-later` 的最终选择在实施前由用户确认）公开发布，同时按原版仓库许可要求完整保留其许可文本、版权声明与来源署名。原 TODO 中“目标许可证待定/当前 MIT”的表述全部按本决策解释。

尚待用户补充的事实输入：当前 Git 历史首个提交为 `Initial public release`，仓库内未记录上游 fork 来源。实施 D/E/F 前必须由用户确认精确的上游仓库地址、fork 时上游 commit 及当时许可证文本。

## 工作范围

### A. 品牌设计与 Demo

- 确定项目的正式中英文名称、简短定位、标语、色彩、字体、Logo、仓库社交预览图和基础图形规范，并检查名称、图形和字体的许可及明显冲突；
- 保存可编辑的品牌源文件、导出规格和第三方素材来源，确保仓库内公开资产具有可分发授权；
- 制作一套简洁、可复现的中英文 Demo，覆盖安装、连接、只读能力、安全门限和至少一条代表性工作流；
- Demo 只使用全新 disposable Notebook 或明确授权的合成数据，不出现私人 Notebook、账户、文件路径、令牌、机器标识或历史验证现场；
- 为视频或动图提供字幕/说明、关键命令文本和静态截图替代，明确 Windows、OneNote Desktop、local-only、COM 与默认 fail-closed 边界；
- 在干净环境复演 Demo，并校验 README 中的命令、画面和当前公开 tool 契约一致。

### B. 中英文根目录 README

- 以根目录 `README.md` 和对应中文 README 组成清晰的双语入口，页首互链且维护相同的版本、安装方式、能力范围、安全说明和限制；
- 覆盖项目定位、支持平台、快速开始、客户端配置、权限环境变量、工具概览、常见问题、故障排查、贡献方式、许可证、Credit 和安全报告入口；
- 明确不使用 Microsoft Graph/Azure/OAuth、不上传 OneNote 内容、不直接编辑 `.one` 文件，并避免“绝对安全”“所有版本均支持”等超出证据的宣传；
- 根 README 只提供面向用户的稳定入口，详细契约链接到 `docs/design/`，开发与验证步骤链接到 `docs/dev/`，避免中英文副本成为相互竞争的权威来源；
- 校验包元数据、安装命令、仓库 URL、Issue URL、截图链接、目录链接及语言切换链接在公开地址下有效。

### C. 独立双语公开文档目录 `docs-public/`、Issue 与 PR 规范

- 新建独立目录 `docs-public/` 作为对外文档的唯一入口，与内部 `docs/` 分离并在 [`docs/README.md`](../README.md) 的目录职责表中登记边界；目标结构（实施时可微调，双语树必须同构）：

  ```text
  docs-public/
    README.md               # 公开文档总入口（英文），页首语言互链
    README.zh-CN.md         # 中文总入口
    en/
      user-guide/
        getting-started.md  # 前置条件、安装、首次连接与 health_check
        configuration.md    # 客户端配置样例与七个权限环境变量
        tools.md            # 53 工具概览、响应 envelope、预算
        safety-model.md     # local-only、fail-closed、非永久删除、限制
        faq.md              # 常见问题与故障排查
      dev-guide/
        project-structure.md    # 仓库组织结构与各目录职责
        engineering-rules.md    # 各层 AGENTS 治理规则的公开摘要
        testing.md              # 纯自动化测试分层与运行方式
        manual-validation.md    # 手动验证框架专章（见下）
        contributing.md         # 贡献流程，与根 CONTRIBUTING.md 互链
    zh-CN/                  # 与 en/ 平行同构的中文树
  ```

- 英文树是对外权威版本，中文树为同步镜像并在页首标注互链；双语树必须在同一变更中同步更新，避免成为相互竞争的权威来源；
- 开发文档必须包含独立一章“手动验证框架”（`manual-validation.md`）：说明 HUMAN-GATED 边界（Agent、pytest、CI、hook、timer、watcher 和后台任务不得运行真实 scenario）、扁平 scenario CLI 与 registry 架构、run-scoped disposable fixture 与最小权限隔离、before/after 证据与失败现场保留、`--dry-run` 与 `--use-cache` 的用户操作入口，以及 `clear runs|cache|all` 的交互确认要求；面向外部贡献者解释“为什么真实验收只能由人执行”；
- 公开文档只做摘要与导航，详细契约链接到 `docs/design/`，内部操作流程链接到 `docs/dev/` 与 `tests/manual_validation/README.md`，不复制其全文；
- 首个公开版本采用仓库内文档形态（不建文档站），公开入口为根目录中英文 README、贡献指南和 Release Notes；发布后以未登录访问验证链接、目录导航、语言切换和站内相对链接有效，不能以私有仓库内可见或本地预览代替公开可访问证据；
- 提供中英文贡献指南，并与各层 `AGENTS.md` 的安全约束一致；尤其说明 Agent、pytest、CI、hook、timer、watcher 和后台任务不得运行真实 OneNote mutation scenario；
- 建立至少包含 Bug、Feature/Proposal 的 Issue 模板，以及 PR 模板；要求复现信息、影响范围、测试/文档更新、兼容性、权限门限和敏感信息脱敏；
- 建立 `SECURITY.md`、行为准则和维护者/响应边界，给出私下报告安全问题的渠道，避免要求用户在公开 Issue 粘贴 Notebook 内容或本机证据；
- 明确破坏性契约变化、公开 tool 变化和真实 mutation 能力的评审与验收门，确保模板不会诱导自动化执行人工场景。

### D. Credit、来源审计与切换到 GPL 的 Relicense

目标许可证已确定为 GPL 强传染许可（建议 `GPL-3.0-or-later`，`only`/`or-later` 由用户在实施前最终确认）。

- 以 fork 边界和完整历史为依据，清点原仓库代码、后续贡献者、复制或改写的代码、文档、图形、字体、Demo 素材及依赖许可证；由用户确认精确的上游仓库地址、fork 时 commit 与当时许可证文本（当前仓库历史未记录该信息）；
- 确认 fork 时原项目的许可证文本、版权声明和署名要求；在 `NOTICE`（或 `CREDITS.md`/`THIRD_PARTY_LICENSES`）中完整保留原版仓库的许可文本、版权行与来源 URL/commit，并在根 README/Credits 中清晰说明项目来源与后续演进；
- 分别验证“上游许可证（预期为 MIT 类宽松许可）允许在保留原始声明的前提下并入 GPL 发行版”与“所有纳入内容都可按 GPL 公开”两个命题；对许可证与 GPL 不兼容、来源不明或需单独同意的内容，取得书面授权、替换、移除或在发布前寻求专业法律意见；
- 核对所有实际贡献者对 GPL 目标许可证的授权基础；若 relicense 需要原作者或其他权利人的明确同意，保存可引用的同意证据；
- 执行 MIT→GPL 的一致性切换：替换根 `LICENSE` 为完整 GPL 文本，更新 `pyproject.toml` 的 `license` 字段与 `License ::` classifier、README 许可章节、`package.json`（如保留 npm launcher）及任何 SPDX 表达；统一 `LICENSE`、包元数据、源码头、README、Credit/NOTICE 和第三方声明中的项目名、年份、权利人，避免互相冲突；
- 审查运行时与开发依赖的许可证与 GPL 分发的兼容性；
- 许可证和 Credit 审计结果以具体 commit、上游 URL、许可证版本及证据位置记录，不以口头判断代替。

### E. Git 时间线与公开历史

- 冻结并记录准确的 fork 边界、上游 commit、当前 commit、作者映射和预期公开时间线；
- 将 fork 前需要保留的上游历史整理为单个有来源说明的 `init` 提交，fork 后历史整理为可审阅的直线时间线；提交拆分应保留有意义的功能、测试、文档和安全决策边界；
- 历史压缩不能抹去作者 Credit、许可证来源或关键安全/验证证据；必要的作者与来源映射应在提交说明、Credits 或独立审计记录中保留；
- 在任何 rewrite 前创建经验证、可恢复且不公开敏感内容的备份引用或 bundle，由用户审阅 old→new commit 映射；只有用户明确批准后才能替换目标分支或 force-push；
- rewrite 后验证工作树等价、提交作者/时间/说明合理、无意外 merge、tag/branch/remote 指向正确，并从全新 clone 复核构建、测试、文档链接和安装流程；
- 对整个待公开历史执行凭据、个人信息、大文件、生成物、`.local-validation/` 证据和专有素材扫描；删除敏感历史时保留私下审计记录，但不得把秘密复制到 TODO 或公开提交信息。

### F. 通过 Issue 告知原作者

- 在原项目的官方 Issue tracker 向原作者发出清晰、尊重且可追踪的通知，说明 fork 来源、项目新定位、主要差异、目标许可证、Credit 方式和公开计划；
- 通知内容不得暗示原作者背书，不转贴私人沟通或敏感信息；如存在 relicense、命名或署名问题，应先解决或明确等待答复，不能以“已经发 Issue”替代授权；
- 保存 Issue 永久链接、发送日期和必要答复摘要，发布说明及 Credit 指向同一来源关系；若 tracker 不允许创建 Issue，记录失败证据并由用户决定等价的公开联系渠道；
- Issue 的实际发送属于对外动作，必须由用户明确确认或亲自执行。

### G. 其他公开发布事项

- 明确首个公开版本的能力范围；所有对外宣称的 mutation 能力均满足自动化合同和用户确认的真实隔离验证，未闭合项要么完成、要么从承诺中排除并如实列为限制；
- 清理公开仓库内容：忽略本地环境、缓存、真实运行证据和编辑器状态；检查示例配置没有密钥、用户名、绝对路径或默认开启危险权限；
- 审查运行时与开发依赖、锁文件、供应链来源和第三方许可证，生成需要的归属/NOTICE，并验证发布包只包含预期文件；
- 在全新 Windows 环境或等价的干净 clone 中验证 Python 与 npm 安装入口、launcher、只读 smoke test、完整纯测试、构建产物和卸载/升级说明；真实 OneNote mutation 验证仍只能由用户显式运行；
- 准备版本号、Changelog/Release Notes、已知限制、兼容性范围、升级说明和回滚方案；确认 PyPI/npm/GitHub Release 是否属于首发范围，不默认执行发布；PyPI 打包与 `uvx` 调用入口的准备工作由 [TODO 042](042_pypi_release_uvx_entrypoint.md) 承载；
- 配置公开仓库描述、topics、主页、社交预览、默认分支、Issue/Discussion 选项和最小分支保护；CI 只能运行纯测试，不得接触真实 OneNote 数据或触发 mutation scenario；
- 明确维护者、Issue/PR 响应预期、漏洞处理、发布负责人和后续双语文档同步责任。

## 发布检查顺序

1. 冻结拟公开范围和 fork/许可证事实，先完成来源、Credit 与 relicense 审计。
2. 完成品牌、双语文档、开发文档发布配置、社区文件与 Demo，并基于最终名称和公开 URL 校对。
3. 完成敏感信息、依赖许可证、包内容和公开历史审计。
4. 经用户批准后执行历史整理，在新历史上完成干净 clone、构建、纯测试及文档链接复核。
5. 由用户确认并发送原作者 Issue，记录链接及任何会阻塞发布的答复。
6. 完成最终发布审查；只有全部硬门有证据后，才由用户决定切换仓库可见性、公开发布开发文档及发布首个版本。

## 非目标与安全边界

- 本 TODO 不引入 Graph、Azure、在线 OAuth、遥测或远程 OneNote 内容处理；
- 不直接读取、编辑或重写二进制 `.one` 文件；
- 不为了制作 Demo 或发布截图使用真实用户 Notebook、失败现场或 `.local-validation/` 中的私人证据；
- 不以删除测试、放宽 policy、默认开启 mutation、永久删除或 raw XML 来获得更顺畅的演示；
- 不要求把所有历史开发笔记原样公开；公开范围应以安全、许可证、隐私和维护价值审查为准；
- 不在本 TODO 中自动执行远端可见性变更、历史破坏性重写、对外联系或包发布。

## 完成定义

- [ ] 品牌资产及其源文件/许可证清单完成，双语 Demo 在干净环境复演通过且不含私人数据；
- [ ] 根目录中英文 README、`docs-public/` 双语使用/开发文档（含手动验证框架专章）、贡献指南、Issue/PR 模板、`SECURITY.md`、行为准则和维护边界完成并互链；
- [ ] 开发文档已随首个公开版本正式发布，具有稳定公开入口和明确版本归属，并已通过未登录访问、导航、双语入口及链接检查；
- [ ] Credit、上游来源、贡献者授权、第三方素材/依赖及目标 GPL 许可证审计完成；任何需要的 relicense 同意都有可复核证据；根 `LICENSE`、包元数据、README 均为一致的 GPL 表达，且上游许可文本与版权声明已在 NOTICE/Credits 中完整保留；
- [ ] fork 边界和 old→new 映射已记录；经用户批准的公开历史为直线，fork 前内容合并为单次 `init` 提交，作者 Credit 与审计证据未丢失；
- [ ] 全历史秘密/隐私/大文件/专有素材扫描、发布包内容检查和文档链接检查通过，无 `.local-validation/`、真实 Notebook 数据或本机标识进入公开内容；
- [ ] 全新 clone 的安装、构建、纯测试、只读 smoke test 和 Demo 复演通过；所有真实 mutation 结果只引用用户确认的隔离证据；
- [ ] 对外能力矩阵与实际合同一致，未完成或受限功能已明确披露，不以待验证行为作为稳定能力宣传；
- [ ] 原作者通知 Issue 已由用户确认发送并记录永久链接；所有许可证/署名相关阻塞答复均已处理；
- [ ] 版本、Changelog/Release Notes、仓库元数据、分支保护、CI 安全边界和维护责任完成最终人工审查；
- [ ] 用户明确批准最终公开时间点，并确认公开仓库和首个包/Release 的具体发布范围。

## 完成证据记录

实施时至少记录以下证据，未填写不得将状态改为“已完成”：

| 证据 | 结果/位置 |
| --- | --- |
| fork 边界、上游 commit 与目标许可证 | 待填写 |
| Credit/relicense/第三方许可证审计 | 待填写 |
| 品牌资产与 Demo 复演记录 | 待填写 |
| 双语文档与社区文件审查 | 待填写 |
| 开发文档公开发布入口、版本与未登录访问检查 | 待填写 |
| 历史 old→new 映射及恢复验证 | 待填写 |
| 秘密、隐私、大文件及发布包扫描 | 待填写 |
| 干净 clone 构建、纯测试与只读 smoke test | 待填写 |
| 原作者 Issue 永久链接 | 待填写 |
| 最终人工发布批准 | 待填写 |

## 关联

- [项目文档治理](../README.md)：公开文档的职责划分与权威来源。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
- [当前架构](../design/architecture.md)：local-only、COM bridge 与安全边界的权威说明。
- [公开 Tool 契约](../design/tool_contracts.md)：README、Demo 和能力矩阵不得超出当前契约。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：真实 mutation 验收只能由用户显式启动。
- [TODO 007](007_cross_version_compatibility_evidence.md)：跨版本证据仍是独立的非阻塞长期工作；首发只声明已有证据覆盖的环境范围。
- [TODO 042](042_pypi_release_uvx_entrypoint.md)：PyPI 发布准备与 `uvx` 调用入口；其正式发布以本 TODO 的公开发布硬门为前置。
- [TODO 013](013_reparent_default_placement_contract.md)：发布范围审查时必须完成其真实验收，或从稳定能力宣传中明确排除未闭合部分。
