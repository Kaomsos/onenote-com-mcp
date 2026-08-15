# 007：跨版本兼容性证据与环境元数据

> ID：007
> 状态：待办
> 优先级：P3
> 类型：兼容性验证 / 证据元数据
> 更新日期：2026-08-16

## 背景与当前决策

OneNote Desktop 的 COM 行为可能随 OneNote/Office 版本、发布 channel 和 Windows 环境变化。跨版本证据有助于判断 typed mutation 能力在哪些组合上经过真实验证，但人工填写环境元数据会增加当前场景的验收负担，也不能保证值准确。

`launch_onenote_gui` 尤其依赖安装环境：OneNote 的 ProgID、CurVer、CLSID、`LocalServer32`、32/64 位注册表视图、Click-to-Run/MSI 安装形态和可执行文件参数可能跨 Office 版本发生变化。单一 Microsoft 365/Office 16 环境上的成功不能外推为其他版本兼容；启动工具的 executable discovery、single-launch 与 GUI readiness 必须成为本 TODO 的重点证据面。

当前决定是不让任何既有 manual-validation 场景依赖 `--onenote-version`、`--office-channel` 或同类人工元数据；这些字段不作为运行、报告生成、功能验收或 TODO 完成的前置条件。现阶段真实验收只判断场景自身声明的 ID、父级、顺序、内容和恢复不变量。

## 目标

- 评估能否通过 local-only、只读且无遥测的方式可靠取得 OneNote/Office 完整版本、发布 channel、Windows 版本和必要的 COM 接口信息；
- 若无法可靠自动识别，设计明确标注来源和可信度的可选人工字段，不让缺失值阻塞任何 mutation 场景；
- 为同一具名场景在多个环境组合下的用户确认证据定义稳定 schema 和兼容性矩阵；
- 为 `launch_onenote_gui` 单独记录受信任注册链形状、注册表视图、安装架构、解析结果类别、启动结果和 readiness 结果，不只记录最终成功/失败；
- 区分“本次场景通过”“某个环境组合已验证”和“跨版本兼容”三种不同强度的结论；
- 明确脱敏、保留期限和报告展示规则，确保环境证据只写入本地 artifact。

## 非目标

- 不在本 TODO 实施前恢复任何必填版本或 channel CLI 参数；
- 不引入云 API、遥测、联网检测或远程上传；
- 不因单一机器或单一版本的一次通过而宣称普遍兼容；
- 不改变既有 mutation policy、tool allowlist、对象 ID 边界或 human-gated 执行规则；
- 不为兼容旧版或特殊安装形态而退化为 PATH 查找、宽文件系统扫描、shell command 解析、任意 executable 参数或未评审的 UWP/Store 启动回退。

## `launch_onenote_gui` 兼容性关注面

- 注册链：`ProgID → CLSID → CLSID\{...}\LocalServer32`，以及 versioned ProgID、`CurVer`、每机/每用户注册和 32/64 位 registry view 的差异；
- 值形状：`REG_SZ` / `REG_EXPAND_SZ`、quoted/unquoted executable、允许的 `/Embedding` 形态、环境变量展开和不存在/损坏/歧义注册；
- 安装组合：Microsoft 365 Apps、Office 2016/2019/2021/LTSC，Click-to-Run 与 MSI，x86 Office on x64 Windows、x64 Office，以及受支持 Windows 版本；
- 信任门：解析后仍要求本地绝对路径、文件存在、最终文件名精确为 `ONENOTE.EXE`、无 reparse point，不执行注册表中的任意 command text；
- 行为证据：完全未运行时只请求一次 launch，process-only 时不重复启动，已就绪时返回 `already_running`，启动后 `health_check` 和最小 typed hierarchy read 均成功；
- 证据隐私：默认优先记录注册来源类别、view、值类型、Office/OneNote file version 和解析结果；实际用户名或其他路径片段不得进入公开报告，完整本地路径如确需保存只能留在本地 artifact。

## 建议范围

1. 调研 OneNote COM、Office Click-to-Run 和本机注册信息中可安全只读取得的字段，并记录不同安装形态下的缺失与歧义；
2. 为 `launch_onenote_gui` 建立独立兼容性表，至少按 Office family/channel、x86/x64、安装形态、ProgID/CLSID/LocalServer32 形状和 Windows 版本分列；
3. 设计带 `source`、`confidence` 和 `recorded_at` 的版本化环境证据 schema；
4. 先以纯 helper 和 fixture 测试验证解析、字段缺失、注册表 view、值类型、脱敏及旧 artifact 兼容，再考虑接入具名 scenario 或独立 GUI 验收入口；
5. 接入后仍保持一般环境采集失败不阻塞 mutation 场景；但 Launch 无法证明受信任 executable 时必须继续 fail closed，并允许用户明确关闭非必要环境采集；
6. 定义至少覆盖不同 OneNote/Office 版本或 channel 的人工验证矩阵，以及只基于用户确认结果更新矩阵的流程；
7. 同步 manual-validation README、开发验证文档和报告合同。

## 完成定义

- 已记录环境字段的权威本地来源、可用范围、失败模式和隐私边界；
- 自动识别或可选人工输入的契约已完成评审，默认非阻塞且 fail-safe；
- 自动化测试覆盖成功、缺失、格式变化、拒绝联网、脱敏和历史 artifact；
- 用户在至少两个明确不同的环境组合中运行同一具名场景并确认证据；
- `launch_onenote_gui` 至少在两个明确不同的 Office/OneNote 安装组合上完成未授权拒绝、单次启动、重复调用幂等、health readiness 和最小 hierarchy read 的用户确认；
- 报告能准确区分单次结果与跨版本矩阵结论，且不把 mock 或 dry-run 当作真实兼容性证据；
- 相关当前文档、TODO 索引和证据 schema 已同步。
