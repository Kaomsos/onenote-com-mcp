# DisplayEquation：COM 生成与重建会添加前置空白包装

> 状态：当前有效的局限性记录
> 观察日期：2026-08-12
> 范围：Windows OneNote Desktop、本地 COM、通过 `UpdatePageContent` 写入 standalone block MathML
> 当前 Copy/Move 契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)
> 当前对象模型：[`../design/object_model.md`](../design/object_model.md)
> 人工验证流程：[`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md)

## 观察环境与证据边界

证据来自 OneNote/Office `16.0.20228.20158` x64、Windows build `26200`、中文区域/中国标准时间的单一环境。用户运行的 `interactive-copy-display-equation` 三跳场景保留了 content-free projection 和显式授权的本地 Page XML capture；运行标识为 `run-2026-08-12-10-45-04`。后续 `copy-display-equation` 的 `run-2026-08-12-11-18-03` 确认程序通过 `append_to_page` 建立的 source 在第一次 Copy 前已经带有同一空白包装，但第二跳因 stale plan 在 mutation 前失败。修复 stale-plan 编排后的 `run-2026-08-12-11-28-08` 完成三跳：每跳均清理一个 span/break，目标均稳定回读一个 break，且 `semantic_display_equation`、`verified`、`lossless` 和 `copy_contract_satisfied` 全部通过。

本文只记录合成公式的结构统计，不记录 Page 正文、Notebook 名称、对象 ID、用户路径或二进制内容。单一环境结果不能推广为所有 OneNote 或 Office 版本的保证。

## 真实观察

程序生成的 standalone `display="block"` MathML 在首次 COM 回读时已经带有一个纯空白 span，其中包含一个 `<br/>`。这一步发生在 fixture 的 `append_to_page → UpdatePageContent → read-back`，早于任何 Copy，因此不是 Copy 独有现象。Copy 只是重复提交同类 Page XML reconstruction 的一条已取证路径；在没有删除该包装的情况下，目标在同一个 span 内每次重建增加一个 break：

| 阶段 | span 数 | break 数 | MathML hash |
| --- | ---: | ---: | --- |
| 初次程序化 COM source | 1 | 1 | 基准 |
| Copy hop 1 | 1 | 2 | 不变 |
| Copy hop 2 | 1 | 3 | 不变 |
| Copy hop 3 | 1 | 4 | 不变 |

Outline、OE、T 和公式数量没有随跳数增长；公式 display 模式、元素树、token、可见文本、对象签名和二进制证据保持一致。累积仅发生在同一个公式 CDATA 中、紧邻 block MathML 的纯空白 span 内。一个基准 break 就说明序列化并非输入的干净 MathML；它可能表现为公式前间距，连续 break 则会形成并累积可见空行。

### `UpdatePageContent` 的 XML 递推模式

初次程序化写入以及本次三跳路径都经过 `UpdatePageContent`。三跳部分执行“读取上一页 Page XML → 以 reconstruction payload 写入新目标页 → 回读目标页”。省略公式正文、字体值和条件注释细节后，相关 `OE/T` 的结构始终等价于：

```xml
<one:OE>
  <one:T><![CDATA[
    <span style="...">
      <br />
      <!-- 第 k 跳之前已经累积的其余 br -->
    </span>
    <!-- OneNote 的 MathML 条件注释包装 -->
    <math display="block">...</math>
  ]]></one:T>
</one:OE>
```

设发送给下一次 COM 写入的同一纯空白 span 含 `n_k` 个 break，则本次环境的实际回读满足：

```text
initial authored payload: 0 -> COM read-back: 1
reconstruction read-back: n_(k+1) = n_k + 1
```

因此初次程序化写入是 `0 → 1`，随后未清理重建链为 `1 → 2 → 3 → 4`。每次重建只在原有 span 内增加一个序列化为 `<br />` 的 break，并伴随两个换行字符；对应的 `T` 解码后字符数为 `224 → 232 → 240 → 248`，去除 MathML 后的残余字符数为 `89 → 97 → 105 → 113`，均为每跳增加 8。没有生成第二个 span、第二个公式 OE、额外空 OE 或新的 MathML 节点；MathML 片段的内容 hash 在四个阶段保持相同。

以上是对这条本地 COM 写入路径的实测描述：能够确认的是初次程序化写入已经产生基准包装，保留该包装的连续 reconstruction 回读为 `n + 1`。普通用户直接在 OneNote UI 中输入的公式不经过本项目的 COM authoring payload，不属于本文已验证范围；单一环境结果也不能推广为所有 OneNote 版本或所有 Update payload 的保证。

旧清理器只匹配直接紧邻 `<math display="block">` 的裸 `<br/>`，无法跨过 `</span>` 和 OneNote 的 MathML 条件注释，因此没有处理真实写回形状。

## 产品影响与当前设计决策

- `DisplayEquation` 由完整、有界且带 `display="block"` 的 Presentation MathML 分类，不是公开 PageContentObject 的 `kind`；行内公式仍属于 RichText。前置包装是 COM 写入行为，不依赖是否进入 Copy 分类。
- 所有最终通过 COM `UpdatePageContent` 写入 standalone block MathML 的产品路径，都不得承诺 Page XML 字节等同于输入，也不得承诺公式前绝无 OneNote 生成的间距或空行。当前涉及 HTML MathML 的 `create_page`、`append_to_page`、`replace_page_body` 以及 Copy/Move reconstruction 都在这个产品局限边界内。
- 当前专用发送前清理只实施在 Copy reconstruction：它删除紧邻 DisplayEquation 的纯空白 span 及其中全部 break；span 含可见文字、其他子标签或不在公式前时不删除。普通 Create/Append/Replace 的成功合同不伪称已经消除 OneNote 首次写回的基准包装。
- DisplayEquation comparator 严格比较 MathML 语义、可见文本、对象和二进制，只额外容忍 OneNote 写回零个或一个纯空白 `span + br`。span 的字体属性不参与判定；第二个 break、额外包装、可见正文或其他 markup 仍拒绝。
- 该受限比较通过时允许 `verified=true`、`lossless=true` 与 `copy_contract_satisfied=true` 并存。这里的 lossless 是 Copy 合同中的语义保真，不是 XML/CDATA 字节相同。
- 已观察到的空白包装属于明确记录的平台规范化限制，不作为意料外失败，也不单独阻止 Copy/Move 合同；但任何超出这一精确形状的漂移继续 fail closed。
- 当前真实回归入口是完全程序化的 `copy-display-equation`：它不读取 stdin、不依赖 interactive bootstrap/cache consumer 配对，固定执行三跳 Copy，并在默认模式下逆序非永久清理目标。旧的 `bootstrap-display-equation-fixture` 与 `interactive-copy-display-equation` 只作为下述历史证据来源，不再是公开场景。

真实 `run-2026-08-12-11-28-08` 已确认 Copy 发送前将包装清零后，递推过程为 `n_k → outbound 0 → readback 1`，链式行为从未清理时的 `1 → 2 → 3 → 4` 收敛为 `1 → 1 → 1`。这不会消除初次 COM 写入的 `0 → 1` 平台行为，只阻止 reconstruction 继续累积；结论仅适用于上述记录环境。

## 与 Online Video 限制的区别

两者都属于应明确记录、不能伪装成意外错误的平台限制。区别是 Online Video reconstruction 已观察到播放器语义丢失，当前不能通过 lossless Copy 合同；DisplayEquation 的 MathML 语义和 UI 内容保持完整，已知差异只有受限的空白序列化，因此可以由专用 comparator 接受并进入共享 Copy 门禁。
