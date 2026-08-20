# 安全模型与限制

[English](../../en/user-guide/safety-model.md) | [文档首页](../../README.zh-CN.md)

设计目标一句话可以说清：**配置错误或行为异常的客户端不应有能力损坏你的笔记本。** 下面所有机制都服务于这个目标。

## Local-only 边界

- 全部 OneNote 访问通过本地 PowerShell COM client 走本地 COM API。没有 Microsoft Graph、Azure、在线 OAuth、遥测或远程内容处理。
- 绝不直接读取或编辑二进制 `.one` 文件。
- 绝不把不可信内容插值到 PowerShell 源代码或命令字符串中；默认 transport 使用结构化 JSON 帧，显式 one-shot fallback 才使用临时 JSON 文件。

## Fail-closed 授权

- 七个独立门（Create、Writes、Deletes、Organize、Local File IO、UI Control、Notebook Lifecycle）全部默认**关闭**。见[配置](configuration.md)。
- 授权最先检查，先于任何 readiness 探测和后端工作。policy 拒绝产生**零**次后端调用。
- policy 在服务器启动时固定；运行期间没有任何机制可以扩权。
- Raw XML 访问、通用层级 mutation 和永久删除工具在生产 profile 中完全不发布。

## 精确 ID mutation

- 每个 mutation 都以精确 OneNote 对象 ID 加乐观 confirmation 字段（通常是对象最近已知的 `modified` 值）为目标。没有静默回退到名称匹配或宽泛目标。
- 删除工具始终非永久：对象进入 OneNote 回收站，可从 OneNote UI 恢复。
- Move 是重建式且严格有序的：先验证 Copy；然后才非永久删除源。Copy 失败或未验证时绝不删源。

## Readiness 与 effect 前置条件

- OneNote 就绪意味着同时存在运行中的 `ONENOTE.EXE` 进程和可见顶层窗口。每个已授权 effect 在授权之后、任何后端工作之前检查该前提；纯读取不需要。
- `health_check` 始终只做检查。`launch_onenote_gui` 是唯一的显式恢复 effect（UI Control 门），最多发出一次可信进程启动请求并做有界 readiness 观察。

## 有界工作量

- 搜索、复制和批量 mutation 受显式预算约束。预算耗尽是结构化失败，绝不是静默无界扫描。
- 批量（1–20 项）整体预检、按输入顺序执行、在第一个失败或不确定项处停止——不做大范围回滚，不重放 mutation。部分结果保留逐项状态，供在恢复前检查实时状态。

## Content-free 审计

日志和审计记录只捕获操作名、成功/失败和耗时——绝不记录笔记内容、bridge payload、secret 或原始工具参数。

## 经验证的 Copy 保真

富对象 Copy 保真采用 allowlist 且证据绑定：对象类型只有在真实后端验证证明可无损往返后才被接受。未支持或未验证的对象 fail closed，而不是产生静默降级的副本。该保真合同只覆盖受支持的标题、内容、对象和拓扑投影，不包含 source revision/authorship marker 或原始创建/修改时间。内容边界见 [copy content exclusions](../../../docs/lesson/copy_content_type_exclusions.md)，元数据非承诺见[产品能力边界](../../../docs/product/README.md)。

## 已知限制

- 仅支持 Windows 桌面单用户本地会话；没有云端或跨进程事务边界。
- Reparent 限于单个 Notebook 内；跨 Notebook 容器转移使用重建式 Move。
- Page 正文替换和递归 Copy/Move 是多步非原子操作。
- Copy/Move 会重建目标，不保留 source revision marker 或原始创建/修改时间；OneNote 可以生成目标自己的元数据。
- 外部入站链接无法跨重建式 Copy/Move 保持身份（会产生新 ID）。
- OneNote 可能在 COM 写入时规范化独立单行公式的空白（[观察到的限制](../../../docs/lesson/display_equation_com_leading_whitespace_normalization.md)）。
- 已验证行为都注明证据范围。在某一个 OneNote/Office 组合上通过，不等于对所有版本的保证。
