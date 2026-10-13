# Windows 第一阶段交接 — iPhone AutoBuyer

日期：2026-10-13。目标优先级：Windows 11 x64。

> 后续 Windows 真机工作已在同分支推进到 v0.7.8 / build 51：源码全量 565 passed、3 项 macOS 专项跳过。原生 ACL、电源 API、两类浏览器离线流程与便携包有分阶段证据；当前商品页账户入口复核、购物袋稳定及保存等待已补齐。历史真实官网曾进入取货信息页，build 49 持续日常 Chrome 会话曾确认登录；本轮新连接尚未成立，最终核对页仍待真实验收。最新证据及剩余工作见 [Windows 第二阶段验收](WINDOWS_VERIFICATION.md)。下面的第一阶段描述保留为历史基线。

- 分支：[`codex/windows-port`](https://github.com/ZIYUEZHANG0731/iphone-autobuyer-macos/tree/codex/windows-port)。
- 适配及测试提交：`f1104f905c754a05a56fbb20850958ef611dae1a`（v0.7.6 / build 44）；本交接文档随后在同分支追加。克隆后用 `git log -2 --oneline` 查看代码提交和文档提交，用 `git rev-parse HEAD` 记录本次验收实际版本。
- 可回退的已验证 Mac 基线：`05ba1efee6c0b5b21d9ec226c02abd63260c1844`，v0.7.5 / build 43；`main` 仍停在该提交。
- 第一阶段开发位置为独立工作树；本机正式 Mac 应用未更新，原有数据、密钥和浏览器目录未迁移。

**第一阶段完成源码和入口准备，Windows 真机尚未验收。** 本次没有 Windows exe/安装包、Windows API 成功证据或新的 Apple 官网结果。第二阶段不得把下表中的 macOS 结果记为 Windows 通过。

## 已完成与当前测试

源码适配详见 [WINDOWS_PREPARATION.md](WINDOWS_PREPARATION.md)。主要变化：Windows 私有 DACL、中文/空格/绝对路径、Chrome 注册表查找及严格归属核对、原子持久写入复用、Windows 电源 API 隔离、UTF-8、时区资源、托盘退出入口、窄屏/高 DPI 布局、Windows 依赖与构建/分发自检入口。购买流程及公开商品/门店目录共用原有实现。

测试环境：macOS 15.7.1 / arm64，Python 3.13.3，PySide6 6.11.2，Playwright 1.63.0，cryptography 50.0.2，portalocker 4.4.0，psutil 7.2.2；独立 `.venv`。为验证无系统时区数据库的回退，在该开发虚拟环境额外安装 tzdata 2026.5；运行依赖仍只在 Windows 要求 tzdata。

| 检查 | 当前环境结果 | 证据边界 |
| --- | --- | --- |
| 最终完整离线回归 `python -m pytest -q -ra -m "not live"` | 524 passed，0 failed，1 skipped / 296.19 秒 | 源码控制/合成浏览器结果；无真实官网下单 |
| 资料/GUI 专项 | 55 passed，1 skipped / 7.76 秒 | Windows 分支是夹具；包含 Mac 原生 Qt 进程退出 |
| 平台层归并后专项 | 26 passed，1 skipped / 4.72 秒 | 平台、权限、进程锁、Qt GUI/renderer 生命周期；随后重新完成上面的全量回归 |
| GUI/内置 renderer CLI 自检 | GUI_SMOKE_OK、EMBEDDED_SMOKE_OK，退出 0；JSON ok=true | macOS 源码；截图检查窄屏摘要，仍需 Windows 渲染验收 |
| 合成订单 CLI | first_available → PAYMENT_PENDING；submit_count=1、synthetic_orders=1；退出 0 | 本地合成身份/商品；不是 Apple 订单 |
| 依赖和编译 | pip check、compileall、git diff --check 通过；离线环境检查全部 ok | 当前开发环境 |
| Windows x64 wheel | Python 3.13 的运行及固定工具版本解析/下载成功；核对 QtWebEngineProcess.exe、中文 locales、V8、Playwright node.exe/cli.js、cryptography Rust pyd、tzdata Asia/Shanghai | 不加载 Windows 二进制；跨平台 pip 的 markers 按主机计算，因此另显式下载 tzdata、pywin32-ctypes、pefile；须重新在 Windows 完整安装 |
| PowerShell 入口 | macOS 可移植 PowerShell 7.5.3：7 个 ps1 的 AST 解析均 0 错误；UTF-8/空格参数助手与合成 JSON 检查通过；非 Windows 运行入口拒绝并退出 1 | 不等于 Windows PowerShell 5.1 或 Windows 原生命令执行通过 |
| Windows 原生 ACL 测试 | skipped：本机不是 Windows | 只有在 Windows 执行该测试才能获得原生 API 证据 |

开发中首次平台专项出现 2 个夹具失败：注册表夹具错误使用 POSIX 路径，旧原子写入夹具仍补丁到已合并模块；修正后通过。首次全量出现 1 个测试失败：新增 POSIX 权限条件漏了模块级 os 导入；已修正并重新做完整回归。这些失败不记为产品验收通过。

本机证据都在独立工作树的忽略目录 `reports/`：`windows-stage1-final-pytest.txt`、`windows-stage1-final-tests.xml`、`windows-stage1-targeted-final.txt`、`windows-stage1-environment.json`、`windows-stage1-gui.*`、`windows-stage1-embedded.*`、`windows-stage1-cli.*`、`windows-stage1-powershell*.txt`、`windows-stage1-wheels.txt`。测试使用隔离目录和合成账户；报告、截图、浏览器资料、wheel 和工具下载均不提交。

## 新 Windows 电脑：克隆、依赖、启动

前置：Git for Windows、Python 3.13 **x64**（含 `py` launcher）、正式 Google Chrome；使用普通当前用户。仓库是私有仓库，Git 需正常登录具有访问权限的 GitHub 账户。不要从 Mac 拷贝任何用户数据目录、Cookie、profile、账号密文、local.key、二维码或私有报告。

在 PowerShell 中执行，以下目录名刻意包含中文和空格：

```powershell
Set-Location -LiteralPath ([Environment]::GetFolderPath('MyDocuments'))
git clone --branch codex/windows-port --single-branch https://github.com/ZIYUEZHANG0731/iphone-autobuyer-macos.git "iPhone 抢购 Windows"
Set-Location -LiteralPath ".\iPhone 抢购 Windows"
git rev-parse HEAD
git log -2 --oneline
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1
```

`-ExecutionPolicy Bypass` 只作用于该次 PowerShell 进程，不修改系统长期策略。受管理电脑的组策略仍以实际限制为准。脚本缺依赖或任一步失败均非零退出；不要继续记为通过。

若使用已有 Python 的完整路径替代 launcher：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Python "C:\Program Files\Python313\python.exe"
```

路径应替换为本机实际的 Python 3.13 x64 可执行文件。正常使用无需激活 `.venv`。setup 安装固定 Windows 工具及原有运行依赖，并安装测试所需的 Chromium；独立浏览器优先使用已安装的 Chrome。

Windows 默认正式数据位置：`%LOCALAPPDATA%\Apple AutoBuyer`；Playwright Chromium 默认为 `%LOCALAPPDATA%\ms-playwright`（保留用户显式的 PLAYWRIGHT_BROWSERS_PATH）。Windows 首次需要重新输入账号/取货人，并通过 Apple 正常双重认证；不读取 Mac 的保存记录。

## 源码验收命令

先完成下方清单的源码/系统部分，再打包。自动回归不请求 Apple：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

脚本自动创建 `%TEMP%` 下的唯一合成数据目录，运行 `not live` 全量测试，包含真实 Chrome 与内置浏览器离线夹具。源码报告写到 `reports\windows-source-时间戳`，失败资料留在隔离目录。没有 Chrome 的环境会跳过 Chrome 原生测试；这不能算两种浏览器都通过。`-UnitOnly` 会排除 e2e，只用于定位失败，不作为最终完整验收。

手工源码自检也使用隔离目录：

```powershell
$taskAcceptanceRoot = Join-Path $env:TEMP ('AutoBuyer 验收 ' + [guid]::NewGuid())
$env:AUTOBUYER_DATA_DIR = Join-Path $taskAcceptanceRoot 'data'
.\.venv\Scripts\python.exe -m app.main --check-environment --report (Join-Path $taskAcceptanceRoot 'environment.json')
.\.venv\Scripts\python.exe -m app.main --smoke-test --report (Join-Path $taskAcceptanceRoot 'gui.json')
.\.venv\Scripts\python.exe -m app.main --embedded-smoke-test --report (Join-Path $taskAcceptanceRoot 'embedded.json')
.\.venv\Scripts\python.exe -m app.main --local-test first_available --headless --report (Join-Path $taskAcceptanceRoot 'synthetic-order.json')
Remove-Item Env:AUTOBUYER_DATA_DIR
```

记录每条的 `$LASTEXITCODE`、报告 ok 和合成提交次数；存在失败先定位，不用真实官网试跑补替源码测试。Qt 的 offscreen 用于自动测试，托盘、DPI 和文件对话框必须另外在可见 Windows 桌面检查。

## Windows 构建与分发验收命令

构建必须在 Windows 执行。先停止本次验收的 app/软件 Chrome，保持数据隔离；运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

顺序为环境检查 → 完整源码回归 → PyInstaller onedir → 产物环境/GUI/内置 renderer/合成订单检查。失败立即停止。产物为整个 `dist\iPhone AutoBuyer`，包括 exe 和 `_internal`；不只复制 exe。本阶段没有制作安装向导或 Windows 签名。

已有构建可单独自检：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\self-test.ps1 -Distribution ".\dist\iPhone AutoBuyer" -WithChromium
```

随后将**完整目录**以及 `scripts\self-test.ps1`、`scripts\windows-common.ps1` 复制到没有 Python/开发环境的 Windows 11 x64 普通用户环境。不要复制源码 `.venv`、开发机器用户目录或浏览器缓存。保留这两个脚本相邻，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\验收 工具\self-test.ps1" -Distribution "D:\验收 应用\iPhone AutoBuyer"
# 如需验证独立 Chromium 和合成订单，使用 exe 自身安装当前用户缓存；无需 Python/Node：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\验收 工具\self-test.ps1" -Distribution "D:\验收 应用\iPhone AutoBuyer" -InstallChromium
```

第二条首次会下载 Chromium，然后以完全本地的合成场景验收驱动和唯一提交；网络只用于浏览器下载，不进入 Apple。自检报告和数据写入 `%TEMP%\AutoBuyer packaged GUID`，不接触默认正式数据。另在可见桌面双击 exe，验证 Qt 插件/renderer、托盘、中文 UI、退出与重开；安装 Chrome 后验证软件独立会话。记录系统运行库缺失、SmartScreen、杀毒/企业策略拦截等实际结果，不能只因构建成功就判定分发成功。

## 剩余问题及 Windows 11 x64 验收清单

以下是第一阶段交接时的 **待验清单**；Windows 第二阶段的已验结果和剩余部分见 [当前验收](WINDOWS_VERIFICATION.md)。按源码、打包、真实官网三组分别留证，已有 Mac 合成测试不能勾选 Windows 通过。

| 类别 | 待验内容 | 通过判据/证据 |
| --- | --- | --- |
| 源码/系统 | Python、PowerShell 5.1/7、路径及缺依赖 | 中文用户名/含空格中文路径；setup/check/run/test/build 退出码正确；不装 Xcode；缺失 venv/依赖会清晰失败 |
| 源码/系统 | 启动、停止、退出和重开 | 可见 GUI、后台 QThread/renderer 正常结束；托盘显示/关闭会话/退出都可达；无托盘仍可见 |
| 源码/系统 | 输入保存、权限和导入/导出 | 合成账户与取货人保存、改名/修改、删除、仅本次、重开恢复；无软件授权密码/原生 vault 弹窗；文件对话框中文路径可用 |
| 源码/系统 | NTFS 权限 | 原生 ACL 测试通过；Get-Acl 检查 data、private-inputs、local.key、records.enc、Chrome/内置资料及新文件继承；不允许 Everyone/普通其他用户；只读/ACL失败保留旧记录，不写出秘密 |
| 源码/系统 | 运行锁与订单锁 | 两进程竞争、正常释放、强制终止后运行锁可重取；UNKNOWN/PENDING/CONFIRMED 跨重启仍阻止同 intent 重提；新计划保留旧记录 |
| 源码/系统 | 托盘、DPI 和时区 | 100/125/150/200%、跨显示器、中文字体、日历及取货/付款对话框；窄屏可滚动核对全部字段；德国/中文系统仍显示北京时间同一时刻 |
| 源码/系统 | 睡眠阻止 | 当前工作线程的 SetThreadExecutionState 调用及恢复有效；禁用后不调用；停止任务保留会话时保持，关闭会话/退出后释放；不声称合盖/强制睡眠仍运行 |
| 源码/系统 | 独立 Chrome | 普通/用户/注册表安装查找；自身 profile、正常 Browser.close、带/不带到期 Cookie 重开；app 强制终止后仅恢复自身 Chrome；日常 Chrome/其他用户/相似目录不接管、不关闭 |
| 源码/系统 | 内置浏览器 | WebEngine 渲染、下载取消、辅助页面和 CDP 连接；zh-CN,zh 语言；关闭会话保留页面、退出正常刷盘、重开合成 Cookie 恢复；崩溃后的页面恢复 |
| 打包/分发 | 完整目录及自检 | build 与 exe 环境/GUI/renderer/合成订单检查通过；检查 WebEngineProcess、qwindows、pak/ICU/V8/locales、Python DLL、Playwright 驱动、加密 Rust 模块、tzdata 和公开 JSON |
| 打包/分发 | 无开发环境用户 | 全新普通用户无 Python/Node/.venv；从中文/空格路径启动整个目录；无开发机隐含路径；Chromium由 exe 安装；保存重开及权限仍有效 |
| 真实官网 | 正常首次登录 | 在 Windows 软件独立 Chrome、内置浏览器分别完成正常 Apple 验证；沿官网实际域名、受保护页面确认登录；退出重开再复核，不迁移 Mac Cookie/profile |
| 真实官网 | 在售现货至核对页 | 两种浏览器 × 实际可用付款方案分别记录；商品/SKU/配置/数量/价格、允许门店和优先级、隐私自动处理、取货资料、最终方式及有效期数均正确；只运行官网模拟，到 REVIEW_READY/COMPLETED 停止 |
| 真实官网 | 门店/慢页面/付款变化 | 实际允许门店与缺货切店在当前结账页生效；付款保存及较慢跳转保持单次点击；普通支付宝/微信 0 期，分期各自核对；无可用方式/未知字段暂停 |
| 拦截测试 | 自动提交/未知恢复/付款码 | 先运行完全拦截的 Chrome 和内置夹具；授权后单次点击，未知结果锁持久，二维码绑定真实样式的合成订单且原期限不延长；不创建真实订单 |
| 另行安排 | 真实提交与支付 | **本次和第二阶段首轮均不执行**；真实订单、实际二维码和付款方案消费结果须另外明确安排，不能用合成订单替代 |

本地网络诊断中的 macOS scutil 摘要在 Windows 不可用，当前会明确 unavailable；浏览器请求类别/状态诊断继续共用。Windows 企业 Chrome 策略、文件占用/杀毒造成的替换失败、长路径/非 NTFS 目录及未签名分发行为都需记录实际结果。第一轮使用普通本地 NTFS 用户目录；遇到不支持的权限设置会失败，不降级到假定 chmod 已保护。

真实官网只用当前有货商品，保留刷新节奏和服务器 Retry-After；遇到验证码、541/503/429、未知付款或未确认字段，保存脱敏证据并按现有暂停行为处理。不要因切换到新电脑解除或遗忘已有未明确结果的订单；正式购买安排前应人工核对历史订单。

## 记录与后续 Git 操作

每场记录：验收提交、Windows 版本/架构、Python/Qt/Playwright/Chrome 版本、源码或完整分发目录、实际浏览器、付款方式和期数、预期/实际结果、退出码、证据位置。源码自动 XML、打包自检 JSON、真实官网脱敏摘要分别保存，禁止互相替代。

报告和截图保留在本机 `reports/` 或隔离验收目录。不得提交账号/密码、联系人、Cookie、完整 URL 查询参数、浏览器数据、local.key、二维码或私有报告。`.gitignore` 已补齐常见用户数据和浏览器目录。真实截图应人工检查敏感内容后再分享，Git 只提交源码、脚本、测试和文档。

继续工作仍使用 `codex/windows-port`，暂不合并 main。需要查看回退基线可使用独立 checkout：

```powershell
git worktree add --detach "..\iPhone AutoBuyer v075" 05ba1efee6c0b5b21d9ec226c02abd63260c1844
```

不对正式数据执行 reset/删除/迁移。修复 Windows 实际发现的问题后，重新做相关回归和最后完整检查，再更新本文各项状态；新的真实官网结果必须注明 Windows 证据及停止点。
