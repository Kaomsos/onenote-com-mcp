# Lesson 文档指令

这些规则适用于 `docs/lesson/`，并扩展父目录 [`AGENTS.md`](../AGENTS.md) 的文档治理规则。

## 定位与权威边界

- Lesson 用于沉淀从真实实现、排障和人工验证中得到的可复用工程经验，包括平台限制、失败模型和错误假设。
- Lesson 不是当前公开契约。当前行为和返回结构以 `docs/design/` 为准，执行步骤以 `docs/dev/` 和 `tests/manual_validation/README.md` 为准，未完成工作以 `docs/todo/` 为准。
- 每篇 Lesson 必须链接到相关 canonical 文档；不得复制一份会独立漂移的完整契约或操作手册。

## 证据与措辞

- 明确标注观察日期、环境范围和证据来源，区分“真实观察”“工程推断”“当前设计决策”。
- Mock、pytest 和 `--dry-run` 只能证明代码合同或编排，不能写成真实 OneNote COM 行为证据。
- 单一 OneNote/Office 环境中的现象不得表述为所有版本的普遍保证；未知行为必须保留为未知。
- 不记录 Page 正文、用户 Notebook 名称、真实对象 ID、用户路径、secret 或可能识别用户数据的原始 artifact。

## 索引与维护

- [`README.md`](README.md) 是 Lesson 的权威索引。每篇 Lesson 恰好对应一行，并给出主题、证据范围和 canonical 链接。
- 文件名使用简洁的 lowercase snake_case；一个文件聚焦一个可复用问题。
- 当实现推翻旧结论时，不得静默保留过期建议：更新 Lesson 的状态和日期，说明结论如何变化，并同步 canonical 链接。
- Lesson 中不得加入会自动或间接触发真实 OneNote mutation 的脚本、测试或命令。人工验证仍受 `tests/manual_validation/AGENTS.md` 约束。
