# Windows 迁移准备：第一阶段（v0.7.6 / build 44）

更新时间：2026-10-13。分支：`codex/windows-port`。从已经验证的 v0.7.5 / build 43、`05ba1efee6c0b5b21d9ec226c02abd63260c1844` 开始，在独立工作树完成源码适配和 Windows 开发入口。`main`、本机已安装的 Mac 应用和正式用户数据继续保留。准确命令、测试结果、提交和逐项验收见 [Windows 交接](WINDOWS_HANDOFF.md)。

**这是 Windows 真机验收前的交付。** macOS 的 Windows 分支夹具、Windows wheel 下载和 PowerShell 语法解析不等于 Windows API、Windows 浏览器、Windows 安装包或 Apple 官网通过。本阶段没有生成 Windows exe，也没有进行真实官网登录、下单或付款。

## 已完成的适配

| 范围 | 第一阶段实现 | Windows 待验 |
| --- | --- | --- |
| 数据及权限 | `%LOCALAPPDATA%\Apple AutoBuyer`；空环境值有回退；显式数据目录转绝对路径；中文/空格路径；私有路径拒绝符号链接和目录 junction | NTFS DACL、中文用户名、只读目录、权限失败 |
| 私有资料 | 直接输入仍用 Fernet 加密并自动载入；Windows 用当前用户 SID、SYSTEM、Administrators 的受保护 DACL；写入前保护临时文件，fsync 后同目录原子替换；失败保留旧记录 | 原生 SID/ACL API、权限继承、重开恢复；不能把 chmod 当 Windows 权限验证 |
| Chrome 查找 | 保留用户/系统标准安装目录，增加 HKCU/HKLM 的 Chrome App Paths（32/64 位注册表视图） | 实际安装、企业策略、注册表访问和 Chrome 更新 |
| Chrome 归属恢复 | 路径按原生规则规范化；Windows 不区分大小写及斜杠；支持两种参数表示；同时要求当前用户、准确程序和专用 profile，拒绝重复参数、其他用户、renderer 和相似目录 | Windows psutil 输出、PID 归属、异常退出和正常关闭后 Cookie 刷盘 |
| 进程及订单锁 | portalocker 非阻塞锁；链接锁文件拒绝；异常/崩溃释放运行锁；SQLite 的 UNKNOWN/PENDING/CONFIRMED 提交锁继续跨重启保留 | Windows 文件锁竞争、强制终止、重启后的 UNKNOWN 防重提交 |
| 浏览器及睡眠 | Chrome 正常退出优先 Browser.close；内置页面先销毁再释放 profile；Windows SetThreadExecutionState 保留/恢复工作线程原状态，API 实现集中在平台层 | 两种浏览器实际行为、电源 API 与退出恢复 |
| Qt 桌面 | 托盘提供显示、关闭会话、退出菜单；无托盘时保留可见窗口；默认 Qt 6 DPI 支持；窗口按逻辑工作区限尺寸，购买摘要避免单字竖排，窄屏可滚动；添加 Windows 字体候选 | 100/125/150/200% 缩放、跨屏、中文渲染、原生文件对话框、托盘通知 |
| 编码和时区 | Windows 标准输出/错误使用 UTF-8；日志/JSON 显式 UTF-8；统一北京时间；打包收集 tzdata | 不同 Windows 系统语言/代码页、无系统 IANA 数据库的 exe |
| 构建 | PowerShell setup/check/run/test/build/self-test；准确处理中文/空格路径、缺失依赖和非零退出；固定 Windows 工具版本；Qt WebEngine 官方 hooks；显式 tzdata 数据；保留 Playwright 驱动、加密模块与公开目录 | Windows 原生构建、完整目录在无开发环境的普通用户下自检 |

平台选择集中在 `app/platforms/`：目录/路径/Chrome 查找、权限、Windows API、运行时、跨进程锁及 macOS 辅助功能。`app/utils/files.py` 合并加密资料、诊断 JSON 和付款码的原子写入；`app/platforms/power.py` 负责会话生命周期；`app/platforms/qt.py` 管理 Qt 平台初始化，原 utils 入口保留兼容导入。Cocoa 修补仅在 macOS 构建和 Cocoa / Qt 6.11.2 运行时启用；Windows 不编译、不加载 dylib。

## 保留的产品行为

购买引擎、登录与结账适配仍为一套实现。公开商品/门店 JSON 没有修改：继续使用 iPhone 17、18 系列和 Duo 的型号、配置、价格和 SKU 直达；预选门店优先级及当前结账页缺货换店继续保留。

账号与取货资料直接填写，默认保存到本机、重开自动载入，不增加软件密码或原生安全存储确认。旧原生凭据后端保留兼容接口，购买流程不自动读取它。两个已启用的 Apple 隐私选项继续自动处理，不增加软件二次确认。

独立 Chrome 与内置 Qt WebEngine 均保留；中国商店内置语言为 `zh-CN,zh;q=0.9`，认证及商店域名沿官网实际跳转。普通支付宝/微信使用 0 期，与花呗、微信分付及银行分期分别识别；备用普通付款不继承分期期数。商品、配置、数量、金额、允许门店、付款方式与有效期数仍须最终核对。

正式模式授权和核对通过后只提交一次；结果未知仍保留订单锁。官网模拟只到核对页。页面变化唤醒、较慢结账观察和既有刷新/退避策略保留。付款二维码仍按真实订单号绑定、提取、保存和执行原有效期，合成测试不算真实支付证据。

## Windows 开发入口

优先使用 Windows 11 x64、Python 3.13 x64 和正式 Google Chrome。Python 3.12 x64 也在源码声明范围内，但本次 wheel 检查以 3.13 为目标。安装入口为 `scripts/setup.ps1`：创建 `.venv`，安装 `requirements-windows.txt`，检查 pip，安装测试所需的 Playwright Chromium，再执行离线环境检查。无需激活虚拟环境、Node.js 或 Xcode。

`requirements-windows.txt` 复用原有固定运行依赖，并固定 pytest 9.1.1、pytest-asyncio 1.4.0、PyInstaller 6.22.3、hooks-contrib 2026.8。跨平台 wheel 下载成功只证明可获得相应二进制依赖；完整 Windows 安装仍须在目标机执行。

`build.ps1` 先环境检查和源码回归，失败即停止；随后用 spec 构建 onedir 并对产物执行离线环境、GUI、内置 renderer 和合成订单检查。预期输出 `dist\iPhone AutoBuyer\iPhone AutoBuyer.exe`，**必须分发整个目录及 `_internal`**。本阶段不提供 MSI、签名或 Windows 已发布产物。

Qt WebEngineProcess、platform 插件、ICU、WebEngine 资源/高 DPI pak、V8 snapshot 和 locales 由 PyInstaller 官方 Qt hooks 收集，离线环境命令逐项检查 Windows 必需文件。移除了可能排除 Windows WebEngine DLL 依赖的 QtQuick/QML exclusions。Playwright 驱动随目录打包；Chromium 本体使用每个 Windows 用户自己的缓存，可通过软件安装。内置浏览器不需要另装 Chromium；合成订单自检需要 Chromium。

实现选择依据：[Qt WebEngine 部署资源](https://doc.qt.io/qt-6/qtwebengine-deploying.html)、[Qt 高 DPI 默认行为](https://doc.qt.io/qt-6/highdpi.html)、[Playwright 系统要求](https://playwright.dev/python/docs/intro)、[PyInstaller 各系统分别构建](https://www.pyinstaller.org/en/stable/usage.html#supporting-multiple-operating-systems)、[Microsoft DACL 设置与继承](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow)、[线程电源状态返回值](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)。

## 数据边界和验收交接

Windows 必须正常完成首次 Apple 登录、重新输入资料并建立新的本机密钥。不要复制 Mac 的 Cookie、profile、账号密文、local.key、二维码或私有报告。Fernet 的密钥与密文仍在同一私有目录；权限是系统账户边界，不是硬件隔离，也不代替 Windows DACL 实测。

完整剩余清单、分源码/打包/官网的记录方式及精确命令见 [WINDOWS_HANDOFF.md](WINDOWS_HANDOFF.md)。Windows 首轮官网仅用模拟模式到核对页停止；自动提交先在完全拦截的夹具验证。真实订单和付款须另行明确安排。
