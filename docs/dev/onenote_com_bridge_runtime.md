# OneNote COM Bridge 运行依赖

> 状态：当前生产行为与开发排障基线
> 更新日期：2026-08-20

## Windows PowerShell host

当前生产 [`OneNoteBridge`](../../src/local_onenote_mcp/bridge.py) 仅支持 Windows，并固定启动：

```text
powershell.exe -NoProfile -NonInteractive -Command -
```

这里的 `powershell.exe` 指 Windows PowerShell 5.1。当前生产代码不调用 PowerShell 7 的 `pwsh`，开发、诊断和兼容性结论也不得把 `pwsh` 当作等价 host。Windows PowerShell 5.1 通常以 STA 启动；任何未来的常驻 COM host 都必须显式建立并验证 STA 所有权，不能依赖调用方线程或跨线程传递 COM proxy。

当前 bridge 每次 backend operation 都启动一个新的 Windows PowerShell 进程，在固定 PowerShell 程序中创建一次 `OneNote.Application`，并通过临时 JSON 请求/响应文件交换结构化数据。参数始终作为数据传递，绝不插值到 PowerShell 源代码或命令字符串。常驻 PowerShell host 是 [TODO 048](../todo/048_persistent_com_client_bridge.md) 的默认目标实现，但尚不是当前 production transport。

## OneNote COM XML schema

生产代码统一使用 [`XML_SCHEMA_2013 = 2`](../../src/local_onenote_mcp/constants.py)。这是 OneNote COM 的 `XMLSchema.xs2013` 数值，不是 hierarchy scope，也不是用户可配置项。

`GetHierarchy` 的 scope 与 schema 是两个独立参数：

| 用途 | `scope` | `schema` |
| --- | ---: | ---: |
| 只枚举 Notebook（`HierarchyScope.hsNotebooks`） | `2` | `2` |
| 读取到 Page 层级（`HierarchyScope.hsPages`） | `4` | `2` |

正确的只读 PowerShell 调用示例：

```powershell
$xml = ""
$onenote.GetHierarchy("", 2, [ref]$xml, 2)
```

不要把 `HierarchyScope.hsPages = 4` 传到第四个 `schema` 参数。`GetHierarchy("", 2, [ref]$xml, 4)` 不是生产调用，也不能作为 COM 注册、PowerShell binder 或 `[ref]` 兼容性证据。

服务层必须复用 `XML_SCHEMA_2013`，不得在各 operation 中散落 schema 字面量、接受用户提供的 schema，或在失败后猜测其他 schema 重试。Scope 仍由类型化 operation 按 `HIERARCHY_SCOPES` 选择。

## 只读诊断基线

仓库的 [`powershell-com-dispath-smoke-v0.ps1`](../../scripts/powershell-com-dispath-smoke-v0.ps1) 使用 Windows PowerShell 5.1、STA、`scope=2` 与 `schema=2`，只读验证：

- 一个 `OneNote.Application` COM client 能完成 `GetHierarchy`；
- 同一个 client 能串行处理两个 content-free JSON request；
- response 不包含或持久化 hierarchy XML；
- 最终显式释放 COM RCW。

该 smoke 只证明当前机器上的只读调用与进程内 client 复用可行，不证明独立常驻 child process、stdin/stdout framing、全部 operation、timeout、崩溃、shutdown、不重放语义或端到端性能。真实 mutation 验证仍只能由用户通过具名 manual-validation scenario 显式启动。

## 变更纪律

修改 PowerShell host、启动参数、COM apartment、XML schema 或 bridge transport 时，必须同步更新实现、纯合同测试、当前设计文档、本开发说明和公开 developer guide。不得以另一个 PowerShell edition、错误 schema probe、mock 或 dry-run 结果替代真实 OneNote 兼容性证据。
