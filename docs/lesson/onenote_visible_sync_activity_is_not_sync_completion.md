# OneNote 可见同步活动不等于显式同步请求或远端完成

> 状态：当前有效的工程经验<br>
> 观察日期：2026-08-17<br>
> 范围：Windows OneNote Desktop、单一当前环境、已打开的 online-backed Notebook、一次 `create_section_group` 用户前台观察<br>
> 证据来源：[`TODO 037 / UT-006`](../todo/037_user_testing_experience_feedback_and_optimization.md#ut-006online-backed-notebook-创建-sectiongroup-后出现-onenote-同步)<br>
> 当前 Tool 契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)<br>
> 当前架构边界：[`../design/architecture.md`](../design/architecture.md)

## 结论

在 online-backed Notebook 中完成本地 COM mutation 后，OneNote Desktop 可能显示同步活动。这个时间上的相邻现象只能证明“本次用户操作后 UI 出现了同步行为”，不能证明 MCP 显式调用了 `request_notebook_sync`、直接访问了云 API，也不能证明远端同步已经完成。

本项目应继续把本地 mutation 的成功判定建立在有界的本地 COM postcondition 上。OneNote 应用自身是否随后同步、何时同步以及远端是否收敛，是另一个观测域；不能用 UI 动画、固定等待或一次同步请求 accepted 来替代完成证据。

## 真实观察

2026-08-17，用户在 OneNote Desktop 已经打开的一个 online-backed Notebook 中，通过受支持的 MCP 客户端调用 `create_section_group`。本地创建操作后，用户观察到 OneNote UI 出现同步行为。

这次观察来自用户前台使用，不是 pytest、mock 或 Agent 启动的 manual-validation scenario。记录不包含用户 Notebook 名称、对象 ID、路径、Page 内容或网络信息。

## 证据边界

本次没有保存以下证据：

- bridge audit 中的精确 operation 序列；
- OneNote/Office channel、build 或账户类型矩阵；
- 网络请求、服务端状态或同步状态机数据；
- local-only Notebook 与 online-backed Notebook 的对照；
- 显式 `request_notebook_sync` 存在/缺席的对照；
- 可重复运行次数、同步开始延迟或完成时间。

因此，现有证据不能区分“本地 COM mutation 使 OneNote 自行安排同步”“OneNote 原本已有待同步工作”或其他应用内部原因。它也不足以把这一现象推广为所有 OneNote 版本、所有 online-backed Notebook 或所有 mutation 的稳定保证。

## 工程推断

online-backed Notebook 的本地编辑最终通常需要由 OneNote 自身传播到其服务端表示，所以本地 hierarchy mutation 后出现应用级同步活动是合理现象；但这是工程解释，不是本次证据直接证明的因果链。

这里最重要的状态分层是：

```text
本地 mutation applied
    ≠ OneNote 已开始一次可归因的显式同步
    ≠ 远端同步完成
```

本地 COM postcondition 可以证明 SectionGroup 已在当前活动 hierarchy 中稳定出现。它不能观察其他设备、网页版或服务端副本是否已经收敛。反过来，可见同步活动也不能替代 exact ID、confirmation 和本地 read-back。

## 当前设计决策

- `create_section_group` 保持 local-only COM mutation，不隐式调用同步工具，不增加网络探测、遥测、后台轮询或固定 sleep。
- mutation 成功继续依赖当前 Tool contract 定义的本地 confirmation、单次 execute 与有界 read-back。
- `request_notebook_sync` 保持独立的 Notebook Lifecycle 能力；其成功只表示同步请求被 OneNote 接受，不表示远端完成。
- 不因为本次 UI 观察修改权限、Tool schema、Operation Runtime 或 manual-validation scenario。
- Agent 不应向用户承诺“已同步到云端”；若任务确实要求跨设备可见，应明确说明现有工具只能证明本地状态，并让用户通过 OneNote 自身的同步状态界面确认。

上述公开行为以 [`tool_contracts.md`](../design/tool_contracts.md) 为准；本 Lesson 只解释为什么本地 mutation、同步活动和远端完成必须分开建模。

## 未来验证条件

只有当产品任务明确要求解释、抑制、等待或验证该现象时，才值得设计新的证据矩阵。最小调查也必须保持 content-free、local-only，并至少区分：

1. local-only 与 online-backed disposable Notebook；
2. mutation 前后是否存在显式同步 operation；
3. bridge audit 中的本地 COM operation；
4. OneNote UI 的用户观察与本地 hierarchy postcondition；
5. 多次运行及明确记录的 OneNote/Office 环境范围。

即使扩展这些证据，也不得通过抓取云端内容、引入 Graph/OAuth、网络遥测或无界后台等待来突破项目边界。远端完成若没有受支持且可验证的接口，必须继续标记为不可证明。

## 适用边界

本 Lesson 足以否定“看到同步活动就等于 MCP 显式同步或远端完成”的推断，但不否定 OneNote 在 online-backed Notebook mutation 后经常自行同步的可能性。未来若 OneNote COM 提供稳定、类型化且可验证的远端完成状态，或跨版本证据推翻当前边界，应更新本 Lesson，并以 canonical 设计文档为最终行为来源。
