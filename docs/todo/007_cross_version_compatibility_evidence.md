# 007：跨版本兼容性证据与环境元数据

> ID：007
> 状态：待办
> 优先级：P3
> 类型：兼容性验证 / 证据元数据
> 更新日期：2026-08-10

## 背景与当前决策

OneNote Desktop 的 COM 行为可能随 OneNote/Office 版本、发布 channel 和 Windows 环境变化。跨版本证据有助于判断 typed mutation 能力在哪些组合上经过真实验证，但人工填写环境元数据会增加当前场景的验收负担，也不能保证值准确。

当前决定是不让任何既有 manual-validation 场景依赖 `--onenote-version`、`--office-channel` 或同类人工元数据；这些字段不作为运行、报告生成、功能验收或 TODO 完成的前置条件。现阶段真实验收只判断场景自身声明的 ID、父级、顺序、内容和恢复不变量。

## 目标

- 评估能否通过 local-only、只读且无遥测的方式可靠取得 OneNote/Office 完整版本、发布 channel、Windows 版本和必要的 COM 接口信息；
- 若无法可靠自动识别，设计明确标注来源和可信度的可选人工字段，不让缺失值阻塞任何 mutation 场景；
- 为同一具名场景在多个环境组合下的用户确认证据定义稳定 schema 和兼容性矩阵；
- 区分“本次场景通过”“某个环境组合已验证”和“跨版本兼容”三种不同强度的结论；
- 明确脱敏、保留期限和报告展示规则，确保环境证据只写入本地 artifact。

## 非目标

- 不在本 TODO 实施前恢复任何必填版本或 channel CLI 参数；
- 不引入云 API、遥测、联网检测或远程上传；
- 不因单一机器或单一版本的一次通过而宣称普遍兼容；
- 不改变既有 mutation policy、tool allowlist、对象 ID 边界或 human-gated 执行规则。

## 建议范围

1. 调研 OneNote COM、Office Click-to-Run 和本机注册信息中可安全只读取得的字段，并记录不同安装形态下的缺失与歧义；
2. 设计带 `source`、`confidence` 和 `recorded_at` 的版本化环境证据 schema；
3. 先以纯 helper 和 fixture 测试验证解析、字段缺失、脱敏及旧 artifact 兼容，再考虑接入具名 scenario；
4. 接入后仍保持环境采集失败不阻塞场景，并允许用户明确关闭采集；
5. 定义至少覆盖不同 OneNote/Office 版本或 channel 的人工验证矩阵，以及只基于用户确认结果更新矩阵的流程；
6. 同步 manual-validation README、开发验证文档和报告合同。

## 完成定义

- 已记录环境字段的权威本地来源、可用范围、失败模式和隐私边界；
- 自动识别或可选人工输入的契约已完成评审，默认非阻塞且 fail-safe；
- 自动化测试覆盖成功、缺失、格式变化、拒绝联网、脱敏和历史 artifact；
- 用户在至少两个明确不同的环境组合中运行同一具名场景并确认证据；
- 报告能准确区分单次结果与跨版本矩阵结论，且不把 mock 或 dry-run 当作真实兼容性证据；
- 相关当前文档、TODO 索引和证据 schema 已同步。
