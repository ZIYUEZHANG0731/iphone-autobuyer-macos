# Windows 迁移准备（v0.7.5）

本轮整理系统依赖和重复流程，保留 macOS 已验证的登录语言、数据目录、凭据服务名称和订单锁。提供 Windows 启动、打包入口；尚未在 Windows 真机执行，不能当成完成了 Windows 移植。

账号与取货资料直接输入后默认以 `cryptography` 的 Fernet 加密保存在应用私有目录，重开自动载入；可选择仅本次使用。购买流程不调用 Windows 凭据管理器。密钥保存在同一私有目录，安全边界为当前系统账户；旧原生记录和引用保留。`app/platforms/accessibility.py` 中的表格归属修补仅在 macOS Cocoa / Qt 6.11.2 启用；Windows 打包不编译或携带该 dylib，也不需要 Xcode。表格更新前清理辅助功能缓存使用跨平台 Qt 事件。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `app/platforms/` | 系统目录、Chrome 位置、原生凭据后端、跨进程锁 |
| `app/security/vault.py` | 账户与取货人的共同安全读写，类型和服务名分别保留 |
| `app/security/local_inputs.py` | 直接输入的本机认证加密、原子保存与启动时载入；不调用系统凭据后端 |
| `app/utils/files.py` | 私有 JSON 的临时文件与原子替换 |
| `app/utils/power.py` | macOS caffeinate / Windows 线程电源状态 |
| `app/services/chrome_session.py` | 根据进程参数核对软件 Chrome 的用户、程序与资料目录 |
| `app/services/local_test.py` | GUI 与命令行共用的合成场景运行器 |
| `app/gui/browser_controls.py`、`profiles.py` | 主窗口中的浏览器/账户操作与取货人管理 |
| `app/adapters/apple_login.py`、`apple_consent.py` | 官网登录复核与已知隐私声明处理 |

已删除两个旧的 Chrome 实验脚本。登录诊断使用软件内的「记录登录问题」，避免另外维护旧启动方式。付款、登录和取货人仍共用一个浏览器会话。

v0.7.5 的付款名称、别名、有效分期期数与显示文本集中在 `app/services/payment_methods.py`，GUI、准备清单和最终授权共用；普通支付宝、微信支付与各自的分期方案分别识别。两种浏览器的提交校验不再固定招商银行 24 期，Windows 迁移继续沿用相同规则，并在真实 Windows 环境验证控件与付款跳转。

## 整理量

以 v0.6.5 的 `d39bb45` 为基线：主窗口 946 → 756 行（减少 20.1%），Apple 主适配文件 1137 → 920 行（减少 19.1%）。这部分主要是按职责拆分；实际净量同时计入新增系统支持、控件操作与隐私诊断。

`app/` Python 总量 6470 → 6651 行，`scripts/` Python 480 → 200 行；两者合计净减少 99 行（1.4%）。不计测试、文档和新增 PowerShell 入口。核心收益是合并重复实现、明确系统边界和删除 280 行旧实验脚本，后续迁移只需维护一套购买流程。

## Windows 启动与构建

在 Windows 安装 Python 3.12 或更新版本、Google Chrome，进入工程目录运行 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\scripts\run.ps1
```

使用内置浏览器时，Qt WebEngine 随 PySide6 提供。独立 Chrome 优先使用本机安装；没有 Chrome 时才使用 Playwright Chromium。

```powershell
.\scripts\build.ps1
```

预期产物为 `dist\iPhone AutoBuyer\iPhone AutoBuyer.exe`，分发时保留整个目录。Windows 二进制须在 Windows 上构建；macOS 的 `.app` 不能转换成 Windows 程序。[PyInstaller 文档](https://www.pyinstaller.org/en/stable/usage.html#supporting-multiple-operating-systems)

Windows 默认数据目录为 `%LOCALAPPDATA%\Apple AutoBuyer`，浏览器缓存为 `%LOCALAPPDATA%\ms-playwright`。旧原生后端使用 Windows 凭据管理器，禁止明文后端；当前直接输入通过本机加密文件恢复。新增 Windows 时区数据依赖，预约仍统一为北京时间。POSIX 的 700/600 在 Windows 不等价于访问控制列表，发布验收必须核对当前用户目录的实际权限及 Fernet 依赖打包。

## 真机验收仍需完成

1. 源码和打包版的启动、Qt 页面渲染、正常退出；内置浏览器关闭与重启后保留合成 Cookie。
2. Windows 凭据管理器临时合成记录的保存、读取、删除；系统异常不泄露记录内容。
3. 独立 Chrome 打开、正常退出、异常退出后的归属恢复；不可接管日常 Chrome 或其他用户进程。
4. 跨进程锁竞争与退出释放；订单的 UNKNOWN 锁在重启后仍阻止重复提交。
5. 同一机器的睡眠阻止和恢复；安装包目录、用户数据目录权限与系统安全软件兼容性。
6. Apple 中国商店登录、当前在售商品库存、隐私确认、取货人和付款选择。以官网模拟下单模式检查，到核对页停止，不产生订单。

平台分支夹具在 macOS 验证，只证明目录选择、后端选择和控制逻辑，不证明 Windows API、安装包或官网认证实际成功。Apple 的账户会话、可信设备和付款资料不能靠复制 macOS 浏览器目录迁移；在 Windows 上需正常完成首次登录。
