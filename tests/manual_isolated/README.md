# OneNote 隔离手动 Smoke Tests

本目录提供一个“总入口 + 场景子命令”的本地验证套件，用来对专用、可丢弃的 OneNote Notebook 执行真实 COM 读写。它不会由 pytest、CI、hook、安装脚本或 import 自动启动；只有用户在终端显式运行 `run.py` 才会访问 OneNote。

## 1. 安全与权限模型

- 运行具体 mutation 子命令本身即构成授权，不再询问二次确认，也不需要 `--enable-*`、`--yes` 等开关。
- 每条命令启动独立 MCP stdio 子进程，并覆盖全部七个 mutation 环境变量。父终端或全局 MCP 配置不会被修改。
- 子进程启动后首先调用 `health_check`。实际 policy 只要与静态矩阵有一个字段不一致，场景就在 mutation 前停止。
- Runner 会把场景 `--timeout` 同步传给子进程 COM bridge，并核对 `health_check.timeout_seconds`；不会出现 MCP 客户端仍在等待而内部 90 秒默认超时已提前中断的配置分裂。
- 七项 `LOCAL_ONENOTE_MAX_COPY_*` 在隔离子进程中固定为文档默认预算并通过 `health_check.copy_budget` 精确核对，父终端遗留的放大值不会进入真实场景。
- `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES` 和 `LOCAL_ONENOTE_ENABLE_RAW_XML` 在所有场景中始终为 `false`。
- mutation 调用不自动重试；只读调用最多重试一次。超时或 server 退出后不会猜测性重复 mutation。
- “运行中不受权限拦截”指本项目的 mutation policy 已由场景自动配置并预检。Windows 用户权限、OneNote 未安装/未登录、Notebook 只读或 COM 故障等外部条件仍会作为错误明确退出。

| 场景 | Writes | Deletes | Permanent | Section Move | Copy | Page Reconstructive Move | Raw XML |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `inspect/read/report` | false | false | false | false | false | false | false |
| `create/rename/reorder` | true | false | false | false | false | false | false |
| `move` | true | false | false | true | false | false | false |
| `delete` | false | true | false | false | false | false | false |
| `copy-page/copy-section/copy-section-group` | true | true（仅清理目标） | false | false | true | false | false |
| `copy-notebook` | true | false | false | false | true | false | false |
| `reconstructive-move-page` | true | true | false | false | true | true | false |

请只使用无业务数据、无唯一副本、可丢弃的专用 Notebook。建议使用默认名称 `__LOCAL_ONENOTE_MCP_ISOLATED__`，并先等待 OneNote 完成同步。

## 2. 总入口

在仓库根目录使用项目虚拟环境运行：

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py --help
```

所有真实场景均支持：

- `--run-dir/--output`：本地证据目录；`validate` 场景必须显式指定；
- `--timeout`：单次 MCP 调用超时；普通场景默认 180 秒，五个 P2 Copy/重建式 Move 场景默认 1,800 秒，与执行预算一致；
- `--dry-run`：只显示目标、静态权限和 tool allowlist，不启动 MCP；
- `--json`：stdout 只输出一行稳定 JSON，server stderr 写入证据目录。

不存在批量执行所有 mutation 的子命令。每次必须显式选择一个场景，失败后也不会自动进入下一场景。

## 3. 推荐执行顺序

### 3.1 只读检查已有 Notebook

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py inspect `
  --notebook-name "__LOCAL_ONENOTE_MCP_ISOLATED__" `
  --output .local-validation\inspect
```

Notebook 不存在时 `inspect` 会以退出码 2 停止，不会创建对象。

### 3.2 创建或幂等补齐隔离结构

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py create `
  --notebook-name "__LOCAL_ONENOTE_MCP_ISOLATED__" `
  --run-dir .local-validation\run-001
```

`create` 会精确匹配同名 Notebook。不存在时创建；存在时复用，并幂等补齐：

```text
__LOCAL_ONENOTE_MCP_ISOLATED__
├─ Group-A
│  └─ Move-Source
│     ├─ Parent       level=1（自动补齐富文本、表格、1×1 PNG）
│     ├─ Child        level=2
│     └─ Sibling      level=1
├─ Group-B
└─ Delete-Sandbox
   ├─ Disposable-Group
   └─ Disposable-Section
      └─ Disposable-Page
```

`create` 会以固定 marker 幂等检查 `Parent`，只在缺失时调用 typed `append_to_page` 与 `add_image_to_page`。自动 fixture 的 marker、已观察对象类型和仍需人工准备的类型写入 `manifest.json.copy_fixture`；重复运行不会再次追加同一富文本/表格，也不会在已存在 Image 时重复插图。

同一父级出现重复的预期名称时会停止，脚本不会猜测使用哪一个对象。首次成功后，`manifest.json` 保存后续场景使用的精确 ID。

### 3.3 建立只读基线

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py read `
  --notebook-name "__LOCAL_ONENOTE_MCP_ISOLATED__" `
  --output .local-validation\baseline-001 `
  --export-onepkg
```

也可以使用别名 `baseline`，或以 `--notebook-id "<id>"` 代替名称。该命令保存 typed tree、每个 Page 的完整 XML SHA-256 和对象白名单字段；`--export-onepkg` 还会生成 `baseline.onepkg`，且拒绝覆盖已有导出。审计不会保存完整 XML、附件二进制或 base64。

### 3.4 Rename：正向、回读、恢复

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate rename `
  --run-dir .local-validation\run-001 `
  --target move_source
```

`--target` 可取 `group_a`、`group_b` 或 `move_source`；`--new-name` 可指定临时名称。脚本核验对象 ID、父级和全部 Page hash，随后恢复原名并比较完整稳定快照。

### 3.5 Reorder/Page Level：正向、回读、恢复

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate reorder `
  --run-dir .local-validation\run-001 `
  --page-level 2
```

脚本把 `Sibling` 临时放到 `Parent` 后并设为指定 level，核验位置、level、Page ID 和内容 hash，再恢复原顺序与 level。

### 3.6 Move：正向、回读、恢复

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate move `
  --run-dir .local-validation\run-001
```

脚本把 `Move-Source` 从 `Group-A` 移到同 Notebook 的 `Group-B`，核验 Section ID、Page ID/顺序及 Page hash，再移回 `Group-A`。通过只代表当前 OneNote/Office 版本组合的实测结果。

### 3.7 Delete：单独进程、仅非永久

先从 `manifest.json` 的 `structure` 中复制 `disposable_group`、`disposable_section` 或 `disposable_page` 的 ID，再运行：

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate delete `
  --run-dir .local-validation\run-001 `
  --delete-target-id "<manifest 中的 disposable ID>"
```

Delete 只接受 manifest 预先记录的三个 disposable ID，并再次核对它仍位于 `Delete-Sandbox` 下；`permanently` 固定为 `false`。正向删除后同时保存默认 active tree 和 `include_recycle_bin=true` 的层级快照，目标必须消失或带有回收站标记。当前 typed MCP 没有回收站恢复工具，因此脚本不会伪造“恢复”；成功后再次运行同一 `create` 命令可以补齐新的 disposable fixture（ID 会变化，manifest 同步更新）。脚本不会清空回收站。

### 3.8 Page 子树 Copy：正向、回读、清理

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate copy-page `
  --run-dir .local-validation\run-001
```

场景复制 `Parent` 及其 `Child` 子页面到 `Disposable-Section`，核验 old→new ID、相对层级、源 Page hash 和服务端完整 XML 回读结果，然后按叶到根顺序把新副本移入回收站并比较恢复后的 active snapshot。

`create` 已自动向 `Parent` 添加富文本、表格和一张确定性的 1×1 PNG。附件、墨迹和媒体没有对应的稳定 typed 创建工具，需在运行 `copy-page` 前按当前 OneNote UI 手工准备：

1. 打开精确标题为 `Parent` 的 Page；
2. 使用“插入 → 文件附件”添加一个无业务数据的临时小文件，并选择附件而非打印输出；
3. 使用“绘图”画一小段可丢弃墨迹；
4. 若当前 OneNote 版本可稳定创建媒体对象，录制几秒无业务内容的音频或插入一次性媒体；不稳定或不可用时在报告中明确记为未准备；
5. 等待 OneNote 保存完成，再运行 `validate copy-page`，检查 `plan.json` 的 `content_capabilities/copyability.issues` 是否出现预期类型。

`plan.json` 和 `copy-result.json` 会列出 `content_type_unverified` 或 omitted issue。尚未由用户确认的类型不得加入代码中的保真 allowlist。

### 3.9 Section / SectionGroup Copy

每次只运行一个场景：

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate copy-section `
  --run-dir .local-validation\run-001
```

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate copy-section-group `
  --run-dir .local-validation\run-001
```

两者都会保留源对象、验证递归目标，并严格按 `id_map` 对新建 Page、Section、SectionGroup 执行叶到根的精确 ID 清理；每一步删除前都会重新读取确认字段。失败后不会自动继续或猜测性清理；请按 `created_ids/id_map` 在 OneNote UI 与证据文件中核对。

所有 Copy 场景除检查服务端 `copy_report.verified` 外，Runner 还会独立从 before/after snapshot 重算源子树，要求 `id_map` 源集合精确匹配、目标 ID 唯一且无额外活动对象，并逐项验证容器父级、Page 目标 Section、父子页关系、相对层级和相对顺序。

### 3.10 Notebook Copy

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate copy-notebook `
  --run-dir .local-validation\run-001
```

目标只允许创建在 `<run-dir>\notebook-copies`。Runner 验证后调用 `close_notebook`，但不会删除 Notebook 文件夹；`restored.json` 明确记录残留绝对路径。确认无用后由用户在 OneNote 已关闭该 Notebook 的前提下自行处理该一次性目录。

### 3.11 Page 重建式 Move

只有 Page Copy 的实际内容类型已经确认并加入保真 allowlist 后才运行：

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py validate reconstructive-move-page `
  --run-dir .local-validation\run-001
```

场景使用 manifest allowlist 中的 `Disposable-Page`，复制到 `Move-Source` 后仅在完整回读等价时将旧 ID 移入回收站。它不恢复旧 ID；成功后需重新运行 `create` 补齐 disposable fixture。若返回 `copy_only`，源对象仍保留而目标副本也存在，必须先人工核对，不能直接继续下一场景。

## 4. 证据与退出码

典型目录：

```text
.local-validation/run-001/
├─ manifest.json
├─ calls.jsonl
├─ prepared.json
├─ page-hashes.json
├─ report.md
├─ server.stderr.log
└─ scenarios/
   ├─ rename/{before,after,restored,result}.json
   ├─ reorder/{before,after,restored,result}.json
   ├─ move/{before,after,restored,result}.json
   ├─ delete/{before,after,recycle-bin,restored,result}.json
   ├─ copy-page/{before,plan,copy-result,after,restored,result}.json
   ├─ copy-section/{before,plan,copy-result,after,restored,result}.json
   ├─ copy-section-group/{before,plan,copy-result,after,restored,result}.json
   ├─ copy-notebook/{before,plan,copy-result,after,restored,result}.json
   └─ reconstructive-move-page/{before,plan,after,recycle-bin,restored,result}.json
```

各场景子进程还会在自己的目录写 `calls.jsonl`、`server.stderr.log` 和专用 `temp/`。调用审计中的正文、XML、base64 会被长度和 SHA-256 摘要替代。

Copy/重建式 Move 的 mutation envelope 无论成功还是返回结构化 `partial_failure`，都会写入 `copy-result.json`。若其中存在 `created_ids`、`copy_only` 或源部分回收状态，`failure.json` 会标记 `needs_manual_cleanup`，并直接列出 `created_ids`、`id_map` 与 `outcome`；Runner 不会猜测性重试或自动清理这些部分结果。

| 退出码 | 含义 |
| ---: | --- |
| 0 | 场景通过；可恢复场景还表示目标已清理并恢复 active snapshot |
| 2 | 参数、manifest 或目标身份检查失败，未执行 mutation |
| 3 | MCP/COM/transport 失败，需结合审计判断状态 |
| 4 | 正向 mutation 成功但恢复失败，需要人工处理 |
| 5 | 回读不变量失败 |

失败后先保留 Notebook 和证据目录，不要直接运行下一 mutation。可用只读 `inspect` 或 `read` 获取当前状态，再在 OneNote UI 中核对。

全部实机场景完成后，可把本次 OneNote/Office 组合写入 manifest 和报告：

```powershell
.venv\Scripts\python.exe tests\manual_isolated\run.py report `
  --run-dir .local-validation\run-001 `
  --onenote-version "<OneNote 版本>" `
  --office-channel "<Office channel>"
```

## 5. 开发者自检（不访问 OneNote）

```powershell
.venv\Scripts\python.exe -B -m pytest tests\manual_isolated\tests -p no:cacheprovider
```

这些测试只验证参数、权限矩阵、日志脱敏和纯数据比较；不会启动 MCP 子进程。
