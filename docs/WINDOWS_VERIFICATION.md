# Windows 第二阶段验收（v0.7.6 / build 49）

日期：2026-10-13。代码基线 `1472e79`，在 `codex/windows-port` 继续；macOS `main` 未改变。第一阶段结果保留在 [Windows 交接](WINDOWS_HANDOFF.md)，本页记录实际 Windows 证据。

## 环境与修复

本机为 Windows 11 专业版 x64（10.0.26200），普通、未提升权限的当前用户，系统时区 `W. Europe Standard Time`。工作区包含中文。Python 3.12.14 x64、PySide6 / Qt 6.11.2、Playwright 1.63.0、Chrome 151.0.7922.170、PyInstaller 6.22.3、hooks-contrib 2026.8；运行依赖仍按仓库固定版本。项目支持 Python 3.12+，本轮没有另验 3.13。

- 修复冻结浏览器缓存测试的环境泄漏。原 macOS 测试向真实 `os.environ` 注入缓存路径，后续 Windows Chromium 夹具误用 `~/Library/Caches`。改用模块内独立环境，同时检查 macOS 与 Windows 默认缓存及用户覆盖。
- Windows 强制终止夹具现在结束完整的自有进程树。虚拟环境启动器可能另建实际 Python 进程；仅杀启动器并不代表锁持有进程已退出。原生运行锁释放后，SQLite UNKNOWN 订单锁继续保留。
- 原生 ACL 检查的 PowerShell 子进程使用自己的默认模块路径，避免继承 PowerShell 7 的不兼容模块而找不到 `Get-Acl`。与微软 [PSModulePath 说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath?view=powershell-7.6) 描述的跨版本继承行为一致。
- 准备清单与付款码窗口按当前逻辑工作区限制尺寸。付款码仅等比调整显示，保存和复制仍使用原图片；过期后调整窗口不能恢复图片。
- 构建时暂时使用 Windows 系统与虚拟环境的 PATH，结束后恢复原值。原开发终端的 Poppler ICU 与系统 ICU 同名但导出不同，误收集后 Qt 不能加载；静态检查发现缺 20 个 Qt 所需导出，用正确系统 DLL 的诊断对照全部通过。最终产物须由干净搜索路径重新构建，不分发手工修改的诊断包。
- 新增 `package.ps1`，构建完成后生成完整便携 ZIP、SHA256、使用说明及相邻的自检脚本。用户无需 Python / Node。
- build 46 补齐复用购物袋时的取货地区选择。真实结账入口的按钮显示「选择地区」，却有 `aria-label="false"`；按可见文字确认唯一控件，从首选允许门店取得省市和区县，选择官网地区并等待保存，再执行原有门店编号、供货及优先级检查。已选择地区不重新设置，未知控件或失败保存暂停，已尝试门店继续的结果仍不重发。新增直辖市、普通省市、已展开选择器、失败保存及未知控件回归，Qt 内置夹具也覆盖这一入口。

## 已完成的 Windows 源码与系统检查

| 检查 | 实际结果 | 本机证据 |
| --- | --- | --- |
| setup / check | 原生 Windows PowerShell 5.1，退出 0；环境全部 ok，Chrome 可用 | 环境 JSON、构建日志 |
| 最终完整离线回归 | **529 passed / 0 failed / 3 skipped / 278.98 秒** | `reports/windows-source-20261013-124241/tests.xml`、`windows-build-46-final.log` |
| 跳过范围 | 2 项 Cocoa 辅助功能、1 项 macOS scutil；Windows 原生 ACL 和正式 Chrome 未跳过 | 上述 XML |
| GUI / 生命周期专项 | **25 passed / 5.34 秒**；含调整窗口后已过期图片仍不可导出、复制保留原始尺寸 | `windows-dialog-regression.xml` |
| 取货地区与结账专项 | **38 passed / 22.72 秒**；直辖市、普通省市、已展开地区、失败保存和未知控件，继续仅一次 | `windows-checkout-region.xml`；增加慢标签等待和地区日志后又完成上面的全量回归 |
| 实际 Qt 内置地区选择 | **4 passed / 21.90 秒**；真实 Qt/CDP 控件、地区请求与门店继续、会话重开均完全拦截 | `windows-embedded-region.xml`，最终全量再次执行 |
| 平台与原生专项 | **13 passed / 1.05 秒**；原生 DACL、进程树强制终止后运行锁恢复及 UNKNOWN 锁保留 | `windows-native-final.xml` |
| NTFS 与加密恢复 | 合成账户重新载入；data、密钥、密文、两类浏览器目录及继承文件共 8 处 ACL 正确；普通其他用户未获准 | `native_system_probe.json` |
| 防止空闲睡眠 API | 实际工作线程进入 / 异常退出使用 Windows API，并恢复原线程状态；关闭开关不调用 | 同上 |
| 可见原生 Qt 界面 | `windows` 插件；7 页均无水平溢出；实际 DPR 1 / 1.25 / 1.5 / 2，并加验窄逻辑屏幕 | `gui-scale-*/result.json` 与截图 |
| 托盘与退出 | 会话开启时关闭窗口隐藏；触发托盘显示动作后恢复；退出停止定时器 | 同上；操作由 Qt 自动触发，托盘实际鼠标点击仍待人工检查 |
| 小屏幕对话框 | 960×516 逻辑工作区；账户、取货人、准备清单及合成付款图窗口均完整容纳 | `native_dialog_probe.json` 与截图 |
| 缺少环境 | setup/check/run/test/build/self-test 在缺 Python、venv 或 exe 的隔离副本明确退出 1 | `windows-missing-dependencies.json` |

以上全部使用合成资料、隔离数据及离线网页；没有迁移 Mac 用户数据。完整回归包含软件 Chrome 的正常退出 / Cookie 恢复 / 自有进程重接，以及内置 renderer 与 Cookie 恢复、完全拦截的付款和单次提交夹具。自动 GUI 检查不替代多显示器移动、原生文件对话框或物理睡眠的人工验收。

初轮回归 460 passed、14 failed、48 errors、3 skipped，主要为缓存测试污染造成的连锁错误；修复后 522 passed、1 failed、3 skipped，剩余为 Windows 启动器强制终止夹具；修复后 523 passed、3 skipped。新增付款图片显示回归及小屏修复后取得上表最终结果。首次打包环境自检因错误 ICU 失败；诊断包的临时只读文件也阻止过重建。失败保留在本机日志，不记为最终通过。

## 打包与独立运行

build 46 生成完整 `dist/iPhone AutoBuyer/` 和 **`dist/iPhone AutoBuyer Windows v0.7.6.zip`**（263,971,081 字节，约 252 MiB）。ZIP 共 3,714 个唯一条目，CRC 检查通过，包含 exe、`_internal`、使用说明以及相邻的两份自检脚本，无私有密钥、密文、SQLite 或浏览器 profile；不能单独复制 exe。

SHA256：`0158320a6fbe5ae11c40b8c9b341284de84387109513ee1f07aaade43d873f75`，旁边同时生成 `.zip.sha256`。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整正式构建 | check、全量源码、干净 DLL 搜索路径 PyInstaller、自检和 ZIP 一次流程全部退出 0；产物 v0.7.6 / build 46 | `reports/windows-build-46-final.log` |
| 原分发目录自检 | environment / GUI / embedded 全部 ok、退出 0；合成订单 PAYMENT_PENDING、submit_count=1、synthetic_orders=1 | 临时目录 `AutoBuyer packaged d971868c-9d49-474c-a4d8-ebb315662d50` |
| ZIP 解压后独立运行 | build 46 工作区外中文 / 空格路径；PATH 中无 Python / py / Node / pip；清除开发和 Qt 环境覆盖；全部自检退出 0；复用下行的独立 Chromium 缓存 | `windows-cold-46-summary.json`、`windows-cold-46-self-test.log`；临时目录 `AutoBuyer packaged 5dc5ef4e-cd71-4825-8bfd-fdec7c79e501` |
| 全新 Chromium 安装 | build 45 从完整 ZIP 独立运行，exe 自行安装全新浏览器缓存；源码外环境 / GUI / embedded / 合成订单自检全通过；build 46 没有重下同版本浏览器 | `windows-cold-summary.json`、`windows-cold-self-test.log`；临时目录 `AutoBuyer packaged 5894f05d-0411-41b5-96a0-463c5961f872` |
| 独立合成提交 | PAYMENT_PENDING，合成订单 1 个、提交 1 次；未请求 Apple | 上述 `synthetic-order.json` |
| 普通可见启动 | 正常 exe 已在本机打开，窗口响应；用户可在软件内操作浏览器 | 本机当前会话，另记官网阶段 |

独立运行验证仍在同一 Windows 账户；它证明分发目录和驱动不依赖开发工具路径，**不能代替全新普通用户 / 新电脑**。自检下载只连接浏览器分发 CDN。第一次独立检查的开发验收脚本因 PowerShell 5.1 的 stderr 捕获和 UTF-8 无 BOM 问题中断；修正的是忽略目录中的验收脚本，正式入口为 ASCII。复验取得上述最终结果。

build 45 的源码通过后，曾单独重试产物构建；build 46 则从正式 `build.ps1` 一次执行环境、最终源码回归、产物构建、自检和便携打包，全部通过。已有产物可单独执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\package.ps1
```

便携使用和离线自检命令见 [Windows 使用说明](WINDOWS_QUICKSTART.md)。

## 真实 Apple 网站

2026-10-13 **11:40:20（Europe/Berlin，软件北京时间 17:40:20）**，Windows 源码的软件独立 Chrome，用白色 / 256GB 的公开 iPhone 17 配置完成一次供货查询，读到 **12 家门店**，总计 **12.3 秒**。这只是当时供货页面的读取结果，不能作为持续库存承诺。

这一次公开供货查询使用合成隔离会话，无账号、密码或取货资料；`added_to_bag=false`、`checkout_started=false`、`orders_submitted=0`。证据 `reports/windows-public-stock.json`。

随后用户在 build 45 的内置官网完成 Apple 登录和验证码。普通认证诊断看到验证及商店返回成功，但受保护账户页面尚未确认，不能宣称持久登录验收完成。实际官网试跑遵守当前设置，在官网显示游客入口后采用游客备用结账。

实际试跑复用核对一致的 **iPhone 17 / 薰衣草紫色 / 256GB / 数量 1 / RMB 6,799** 购物袋，没有重新加购。2026-10-13 18:23:45 北京时间，取货方式已切换成功，但地区尚未选择，原实现误报门店列表仍在加载。通过正常界面选择 **上海 / 普陀区** 后，读到环球港等门店可取货；恢复任务自动按优先级确认 **Apple 环球港（R683）**，并进入取货信息页。18:35:41 北京时间停在官网要求的手机验证。该结果证明门店选择和进入取货表单成功，**尚未到最终核对页**，没有 Apple 订单或付款。

更新前通过软件「关闭会话」正常退出，持久资料保留；不自动发送短信、提交验证码或绕过验证。Windows 完整回归和合成订单不替代这一真实官网剩余步骤。

build 46 正常重开后，再用原商品、允许门店和付款配置启动官网模拟。再次复用同一购物袋，按原优先级确认环球港并进入取货信息页，2026-10-13 **18:51:43 北京时间** 暂停在「已到官网取货人表单」。原试跑取货资料为进程内临时资料：本机没有保存 `private-inputs`，持久配置无 `profile_id`、`transmit_identity=false`；正常退出不会把临时资料改为持久保存。新版保持会话打开，没有代填资料或操作手机验证。脱敏阶段及零提交记录见 `reports/live-checkout-summary.json`。

用户再次提供本次使用的取货人后，**2026-10-13 13:05:09（Europe/Berlin）** 的官网模拟已填写并读回字段，但游客备用结账仍要求手机验证。随后执行登录状态复核，结果为 `unknown`，不记录为有效登录。用户要求重试后，将业务结账策略改为 **只用 Apple 账户**；**13:23:38（Europe/Berlin）** 官网明确要求重新登录，软件暂停并保留同一购物袋，不再点击游客入口。页面已留在「使用你的 Apple 账户结账」，待本人完成认证；未点击短信验证、未提交密码、未创建订单。证据 `reports/demo-20261013/retest.json`。此设置是当前用户的购买配置，没有改动应用默认策略。

## 本机实机 Demo

`reports/demo-20261013/AutoBuyer-Windows-demo.mp4` 为约 23 秒的本机录制，H.264、1280×928、12 fps，无音频。展示公开商品、门店优先级、登录状态复核和账户登录停止点；没有将其标为完整下单成功，省略取货人、账号输入及验证码画面。整段解码和逐场景视觉检查通过。Windows GDI 对 Qt 窗口只返回旧画面，最终采用实时窗口抓取重录；其他应用遮挡的帧已排除。视频与原始录制资料继续留在 Git 忽略的本机报告目录，未上传。

## build 47 最终回归与分发

2026-10-13，新版从环境检查、最终全量回归、干净 DLL 搜索路径构建、分发自检到 ZIP 打包，整轮退出 0。为保留当前运行的旧安装版及日常 Chrome，本轮输出单独放在 `dist/build47/`；标准 `build.ps1` 的默认目录没有改变。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整 Windows 回归 | **544 passed / 3 skipped，281.17 秒**；跳过仅为 2 Cocoa、1 macOS 网络查询 | `reports/windows-source-20261013-151003/tests.xml` |
| 分发环境 / GUI / 内置 renderer | 全部 ok，退出 0，v0.7.6 / build 47 | `reports/windows-build-47-final.log`；临时目录 `AutoBuyer packaged cd15e8e4-58a6-43f7-a5a7-e77435e15112` |
| 分发合成订单 | PAYMENT_PENDING、submit_count=1、synthetic_orders=1；未请求 Apple | 上述临时目录 `synthetic-order.json` |
| ZIP 与冻结模块 | 3,714 唯一条目、CRC 正确、未夹带用户资料；冻结 PYZ 含 Windows 原生网络模块 | 本机静态分发检查 |

build 47 包为 **`dist/build47/iPhone AutoBuyer Windows v0.7.6.zip`**，263,978,221 字节；SHA256：`e69324a58c1ad5e4c22f70385f67489387f4fd7f0702da196516b0f1a168480b`。build 47 没有重复全新浏览器下载或全新用户验收；build 46 的独立解压验证仍按其原版本记录。

build 47 增加本轮登录排查修正：商店/购物袋复核沿当前官网账户链接，Windows 原生只读网络采样，关闭页面后保留最近诊断环境，显示实际连接页阶段以及 Chrome 标签组提示。专项登录、过期会话和原页保留检查 **95 passed / 1 macOS skipped**，诊断与 GUI 专项 **74 passed**。初次全量回归有一项可见 Chrome 付款单选控件未选中的失败（535 passed / 1 failed / 3 skipped），随后相应 8 个可见/无头结账案例重跑全通过；最终全量与分发自检结果另列于 build 47 记录。

本轮旧安装版已经成功接入日常 Chrome，但新账户复核和 14:54:30 Europe/Berlin 的实际账户结账仍要求认证，没有新增真实提交。新的账户检查路径取得离线复现与修复证据；尚未把它标成 Apple 503 根因或真实登录修复成功。详情见 [登录诊断](LOGIN_DIAGNOSIS.md)。

## build 48：自动登录步骤与 Windows 窗口遮挡

最终完整源码回归 **547 passed / 3 skipped / 292.10 秒**，仅跳过 macOS 专项；证据 `reports/windows-source-build48-final.xml`。账户登录专项 **84 passed**。Windows 实际 Chrome 结账、会话恢复、诊断专项 **71 passed**；新增不透明 Qt 覆盖窗口的实际可信点击和扩展连接专项 **18 passed**。最初的覆盖夹具误将 Windows CRLF 与 LF 精确比较，已改为按行内容判断，行为断言仍保留；最后的全量再次执行所有用例。

IDMS 第一阶段的密码框祖先 `aria-hidden="true"`、高度为 0，但密码输入框仍有非零尺寸；旧实现提前填写并将账户“继续”误标为密码提交。新版排除明确隐藏的密码控件，顺序执行两个正常步骤。购物袋登录导航等待改为 15 秒，仍仅点击一次。另发现软件专用 Chrome 被其他窗口完全遮挡时为 `visibilityState=hidden`，正常点击无法等待稳定；Windows 启动增加 `--disable-backgrounding-occluded-windows`，完全遮挡下动画帧继续，正常单选产生一次可信 click/change，webdriver 仍为 false。首轮全量在此修复前有 11 个可见 Chrome 失败（535 passed / 11 failed / 3 skipped）；修复后取得上述最终全量结果。不是绕过控件核对或服务器拒绝。

干净 DLL 路径构建成功；最终冻结环境、GUI、内置 renderer 和合成订单自检全部退出 0，合成 PAYMENT_PENDING、submit_count=1、synthetic_orders=1。证据 `reports/windows-build48-final.log`、`windows-self-test48-final.log`，临时隔离目录 `AutoBuyer packaged 7c2b921b-b653-4dd5-9a26-257fffc4f8c5`。冻结 PYZ 静态复核包含新的遮挡参数和隐藏密码过滤。最终分发包 **`dist/build48/iPhone AutoBuyer Windows v0.7.6.zip`**，263,977,251 字节，SHA256 **`b0bd0e44df0b50430689b0e9d55dc894863fdb8be1b1ce9e08b92fa1a4f4c797`**；完整解压运行仍按 build 46 的原有版本证据记录。

用户本轮授权的真实认证初始化返回 200 / JSON，完成同一流程剩余的单次密码提交后仍为 **503 / HTML**。正式提交记录仍为 0，实际核对页未通过。扩展选择页等待延长为 10 分钟并提示 Welcome 页，仍可随时取消；新的日常 Chrome 受保护会话复核尚待完成。详情见 [登录诊断](LOGIN_DIAGNOSIS.md)，本机脱敏报告 `reports/windows-login-investigation-build48.json`。

## build 49：正常 Chrome 输入与新启动标签恢复

最终完整回归 **549 passed / 3 skipped / 309.23 秒**，证据 `reports/windows-source49-final.xml`。仅修改正常 Chrome 的登录输入路径和专用 Chrome 新启动时的旧标签处理；Qt 按钮路径仍独立保留。登录与 Qt 专项 **41 passed / 50.93 秒**，原生 Chrome 冷重开、Cookie 保留和多页进程重接专项 **4 passed / 10.07 秒**。首轮直接将所有后端改为原生点击，使 Qt 登录夹具失败，已按其实际后端保留原路径；最终完整回归再次执行全部用例。

干净搜索路径 PyInstaller 构建成功；最终冻结环境、GUI、内置 renderer 和合成单次订单自检全部通过。证据 `reports/windows-build49.log`、`windows-self-test49.log`、`windows-package49.log`，合成隔离目录 `AutoBuyer packaged 93347d19-2a2a-4c45-8ec0-c63f541e9766`。最终包 **`dist/build49/iPhone AutoBuyer Windows v0.7.6.zip`**，263,978,343 字节；SHA256 **`5a357d0a79be7196cfede9d8be6fff131d1aaee769ea2c42a8d65061d419dc61`**。没有重复新电脑或全新用户验收。

真正的登录结果仍未通过：16:38 的正常 Chrome 点击链和 17:00 的单一新登录页链，均为初始化 200 / JSON、单次密码提交 503 / HTML。公开入口的请求语言、认证语言参数和时钟均已核实。没有继续循环重发密码，没有创建真实订单。日常 Chrome 控制接口不可用，真实成功认证对照尚未取得，详情见 [登录诊断](LOGIN_DIAGNOSIS.md)。

## 仍需完成的验收

| 范围 | 剩余内容 |
| --- | --- |
| 新用户 / 新电脑 | 真正没有开发环境的全新普通 Windows 用户；中文用户名、不同 Python 版本、杀毒和企业策略、SmartScreen 实际提示 |
| 桌面人工检查 | 跨显示器移动、实际托盘菜单鼠标操作、原生导入 / 导出文件对话框与中文路径、辅助下载窗口、物理空闲睡眠与恢复 |
| 正常 Apple 登录 | 内置普通认证由用户完成，但受保护页面尚未复核；独立 Chrome、退出重开后的受保护页面确认仍待完成 |
| 官网模拟至核对页 | build 45、46 内置均已确认 R683 并进入取货表单；用户再次提供临时资料后已读回字段。当前按本人要求只用 Apple 账户重试，官网要求重新登录，认证由本人完成。之后继续两种浏览器 × 实际可用付款方案，到 REVIEW_READY / COMPLETED 停止。新地区逻辑已有完全拦截回归，真实自动选择的新入口仍需地区未设置的正常会话复核 |
| 真实提交 / 付款 | 按交接要求另行安排；本轮不执行。合成订单与本次公开供货读取都不替代真实订单或支付结果 |

私有报告、截图、浏览器目录、加密资料和构建产物继续由 Git 忽略。Git 只记录源码、脚本、测试及以上脱敏说明。
