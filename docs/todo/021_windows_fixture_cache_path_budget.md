# 021：Windows Fixture Cache 路径长度预算

> ID：021
> 状态：待办
> 优先级：P3
> 类型：Manual Validation / Windows 文件系统兼容性
> 更新日期：2026-08-12

## 决策摘要

Fixture cache 已对 Windows 短暂文件扫描/共享冲突提供状态守卫的有界原子发布重试，但 cache fingerprint、template instance、staging、run 和 role-specific working 名称叠加后仍可能形成很深的物理路径。测试中已观察到较长 pytest 临时根会触发 `WinError 3`；短路径 basetemp 下同一测试集合通过。这是独立于 `WinError 5/32` 锁竞争的路径预算问题，当前优先级较低，不属于原子发布修复范围。

## 待评估范围

- 计算 cache root、64 位 fingerprint、`instances`、instance ID、role、template/working bundle 及最深 artifact 的最坏路径预算；
- 评估缩短 staging、fingerprint 磁盘目录和 instance 组成，同时保持完整 identity 存在 metadata/evidence 中；
- 为 cache publish、materialize、inventory 和 maintenance target 增加 Windows 路径长度合同测试；
- 设计 pytest 默认使用工作区外、唯一且短的 `--basetemp` 的持久化方案，避免不同测试进程共享目录；
- 评估普通路径与 `\\?\` extended-length path 在 `pathlib`、`shutil`、`os.replace`、OneNote COM path identity 和现有 containment 检查中的一致性。

## 非目标

- 不因路径较长而放宽 cache ownership、containment、reparse-point 或 actual-open-path 门禁；
- 不截断或碰撞 cache identity，也不按名称、mtime 或目录顺序猜测实例；
- 不直接编辑 `.one`/`.onetoc2`，不改变 local-only 边界；
- 不把 pytest 临时目录设置扩展为生产 MCP 配置或公开环境变量；
- 不以重试 `WinError 3` 代替确定性的路径预算修复。

## 完成定义

- 最坏路径预算和支持边界有明确、可执行的合同；
- 采用的缩短方案保持 fingerprint/instance 精确身份和旧 evidence 可诊断性；
- publish、materialize、lookup、失效与 maintenance 在边界路径上有纯测试；
- pytest 短 basetemp 方案具备并发隔离，不写入用户 Notebook 或任意外部未管理路径；
- 如采用 extended-length path，所有身份、containment、COM 交互和错误报告语义均通过审查与测试。

## 关联

- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：已完成的 immutable fixture cache 架构与真实验收记录；本 TODO 不改变其完成状态。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：当前 cache 使用方式、安全边界与本地原子发布重试合同。
- [当前架构](../design/architecture.md)：fixture cache、working copy 和 maintenance 的权威设计说明。
